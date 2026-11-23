# 宫格 × 镜号 × 旁白句 对照清单（出图前排表 + 填图后义对位体检）
#
# 为什么需要它：宫格出图时格位与旁白句序一旦错开一格，画面就整体滞后一句。
# 切图 / 填图 / 渲染三道环节只认序号，错配被固化在图片里，清缓存、重渲、查滤镜都改不动。
# 本脚本把「第几格 ↔ 第几镜 ↔ 哪句旁白 ↔ 该格应画什么」提前排成一张能勾验的表。
#
#   python panel_map.py --id 003 --root D:/workspace --panels 2     # 出图前：按宫格分组排表
#   python panel_map.py --id 003 --root D:/workspace                # 任意时刻：逐镜对照表
#   python panel_map.py --id 003 --root D:/workspace --check        # 填图后体检，退出码 0/1
#
# 清单默认写到 episodes/<id>/宫格对照清单.md，同时把摘要打到屏幕。
# 退出码：0 = 无硬问题；1 = 有硬问题（缺图 / src 越界 / note 还是占位文本，无法核对情节）。

import argparse
import json
import os
import pathlib
import re

EXTS = (".jpg", ".jpeg", ".png", ".webp")
PLACEHOLDER_HINTS = ("待补写", "待生成", "本篇画面待生成", "内置占位", "待按情节补写", "待补")
SEG_TAG = {"钩子": "①钩子", "起因": "②起因", "冲突": "③冲突", "升华": "④升华"}


def story_root(arg_root):
    p = arg_root or os.environ.get("STORY_ROOT") or str(pathlib.Path.cwd())
    return pathlib.Path(p).resolve()


def load_episode(ep):
    lp, sp = ep / "lines.json", ep / "shots.json"
    if not lp.exists():
        raise SystemExit(f"找不到 {lp}，先跑 start_story.py")
    if not sp.exists():
        raise SystemExit(f"找不到 {sp}，先跑 start_story.py")
    lines = json.loads(lp.read_text(encoding="utf-8"))
    if isinstance(lines, dict):                      # 兼容 {"lines": [...]}
        lines = lines.get("lines") or []
    cfg = json.loads(sp.read_text(encoding="utf-8"))
    return [str(x).strip() for x in lines], cfg


def is_placeholder(note):
    note = (note or "").strip()
    if not note:
        return True
    return any(h in note for h in PLACEHOLDER_HINTS)


def src_targets(src, images, motions):
    """把 shot.src 解成 [(图/素材路径, 组内第几个)]；无法解析返回 []。"""
    s = str(src or "").strip()
    out = []
    if s.startswith("img2:"):
        ids = [x.strip() for x in s[5:].split(",")]
        if len(ids) == 2 and all(x.isdigit() for x in ids):
            k = len(ids)
            for n, idx in enumerate(ids):
                i = int(idx)
                if 0 <= i < len(images):
                    out.append((images[i], f"第{n + 1}/{k}张"))
    elif s.startswith("img:"):
        if s[4:].isdigit():
            i = int(s[4:])
            if 0 <= i < len(images):
                out.append((images[i], "单张"))
    elif s.startswith("motion:"):
        if s[7:].isdigit():
            j = int(s[7:])
            if 0 <= j < len(motions):
                out.append((motions[j], "动态"))
    return out


def panel_key(path):
    """s07_饭局_p01.jpg -> ('s07_饭局', 1)；不是切分出来的图返回 (None, 0)。"""
    stem = pathlib.Path(str(path)).stem
    m = re.match(r"(.+)_p(\d{1,2})$", stem, re.I)
    if m:
        return m.group(1), int(m.group(2))
    return None, 0


def collect(ep, lines, cfg):
    shots = cfg.get("shots") or []
    images = cfg.get("images") or []
    motions = cfg.get("motions") or []
    rows = []
    for i, s in enumerate(shots):
        tg = src_targets(s.get("src"), images, motions)
        rows.append({
            "i": i,
            "line": lines[i] if i < len(lines) else "",
            "beat": s.get("beat") or "",
            "note": str(s.get("note") or "").strip(),
            "src": str(s.get("src") or "").strip(),
            "motion": bool(s.get("motion")),
            "files": [t[0] for t in tg],
            "slots": [t[1] for t in tg],
        })
    return rows, images, motions


def grid_groups(rows, images):
    """按图名 _pNN 归组，返回 [(base, panels, [(pNN, 图, 镜号)])]，按首次出现排序。"""
    order, groups = [], {}
    for img in images:
        base, pnn = panel_key(img)
        if not base:
            continue
        if base not in groups:
            groups[base] = {}
            order.append(base)
        groups[base][pnn] = img
    out = []
    for base in order:
        g = groups[base]
        panels = max(g.keys())
        items = []
        for img in images:
            b, pnn = panel_key(img)
            if b != base:
                continue
            who = [r["i"] for r in rows if img in r["files"]]
            items.append((pnn, img, who[0] if who else None))
        items.sort(key=lambda x: x[0])
        out.append((base, panels, items))
    return out


GRID_POS = {
    1: ["整格"],
    2: ["左格", "右格"],
    3: ["左格", "中格", "右格"],
    4: ["左上", "右上", "左下", "右下"],
}


def grid_pos(n, panels):
    table = GRID_POS.get(panels) or []
    return table[n - 1] if 0 < n <= len(table) else f"格{n}"


def short(txt, n=44):
    txt = (txt or "").replace("|", "/").replace("\n", " ").strip()
    return txt if len(txt) <= n else txt[:n] + "…"


def build_md(ep, title, rows, images, motions, panels_arg=0):
    fmt = ""
    try:
        fmt = (json.loads((ep / "shots.json").read_text(encoding="utf-8")) or {}).get("format", "")
    except Exception:
        pass
    static = [r for r in rows if not r["motion"]]
    groups = grid_groups(rows, images)
    panels = panels_arg or (max((g[1] for g in groups), default=0))
    n_grid = len(groups) if groups else (len(static) + panels - 1) // panels if panels else len(static)

    md = [f"# 宫格对照清单 — {title}", ""]
    md.append(f"本篇目录：`{ep.as_posix()}`　画幅：`{fmt or '16:9'}`　"
              f"镜数：{len(rows)}（静态 {len(static)} / 动态 {len(rows) - len(static)}）　"
              f"图数：{len(images)}　动态素材：{len(motions)} 段")
    md.append("")
    md.append("> **用法**：出图前照下表逐格写提示词（格位↔镜号↔旁白句钉死，错位就在这里拦住）；"
              "切图填图后回来逐格勾验——「这一格画的是不是这一句」。"
              "发现错位**重出图**，不要指望重渲、清缓存、改滤镜能修好：错在图片本身。")
    md.append("")

    if groups:
        md.append("## 一、已切分的宫格 ↔ 镜号 ↔ 旁白")
        md.append("")
        for gi, (base, gp, items) in enumerate(groups, 1):
            idxs = [w for (_, _, w) in items if w is not None]
            span = f"镜 {min(idxs)}–{max(idxs)}" if idxs else "未填图"
            md.append(f"**宫格 {gi}：`{base}`（{gp} 格，{span}）**")
            md.append("")
            md.append("| 格位 | 镜号 | 段 | 旁白原文（该镜字幕） | 该格应画的情节（note） | 勾验 |")
            md.append("|---|---|---|---|---|---|")
            for (pnn, img, who) in items:
                row = rows[who] if who is not None else None
                md.append("| {} | {} | {} | {} | {} | [ ] |".format(
                    grid_pos(pnn, gp),
                    ("镜 " + str(who)) if who is not None else "—",
                    (SEG_TAG.get(row["beat"], row["beat"]) if row else "—"),
                    short(row["line"], 40) if row else "—",
                    short(row["note"], 40) if row else "—",
                ))
            md.append("")
    elif panels:
        # 还没出图：按宫格数把静态镜切成连续段，先把「第几张宫格·第几格·哪句旁白」排好
        md.append(f"## 一、出图前排表（按 {panels} 格/张，宫格按连续镜段推进）")
        md.append("")
        for g in range(n_grid):
            seg = static[g * panels:(g + 1) * panels]
            if not seg:
                break
            span = f"镜 {seg[0]['i']}–{seg[-1]['i']}"
            md.append(f"**宫格 {g + 1}（`s{seg[0]['i']:02d}_…`，{span}）**")
            md.append("")
            md.append("| 格位 | 镜号 | 段 | 旁白原文（照这一句写提示词） | 应画情节 | 出图后勾验 |")
            md.append("|---|---|---|---|---|---|")
            for k in range(panels):
                if k < len(seg):
                    r = seg[k]
                    md.append("| {} | 镜 {} | {} | {} | {} | [ ] |".format(
                        grid_pos(k + 1, panels), r["i"],
                        SEG_TAG.get(r["beat"], r["beat"]),
                        short(r["line"], 40), short(r["note"], 40)))
                else:
                    md.append(f"| {grid_pos(k + 1, panels)} | —（备用/空） | — | — | — | — |")
            md.append("")
    # 逐镜总表（两种模式都出，供填图后按镜核对）
    md.append("## 二、逐镜总表（填图后按这张核对）")
    md.append("")
    md.append("| 镜号 | 段 | 旁白原文 | 画面情节 note | 图/素材 | 宫格格位 |")
    md.append("|---|---|---|---|---|---|")
    for r in rows:
        files = []
        for f, slot in zip(r["files"], r["slots"]):
            base, pnn = panel_key(f)
            pos = f"{pathlib.Path(f).name}"
            if base:
                pos += f"（{grid_pos(pnn, 4 if pnn <= 4 else pnn)}）"
            files.append(pos if slot == "单张" or slot == "动态" else f"{pos}·{slot}")
        md.append("| {} | {} | {} | {} | {} | {} |".format(
            r["i"], SEG_TAG.get(r["beat"], r["beat"] or "—"),
            short(r["line"], 34), short(r["note"], 34),
            "、".join(files) or "**缺图**", r["src"] or "—"))
    md.append("")
    md.append("## 三、勾验总则")
    md.append("")
    md.append("1. 逐格读**旁白原文**那一列，再看该格画面讲的是不是同一件事——不是同一件就是错位；")
    md.append("2. 错位一律**重出图**（图名带镜号），重出图放 `重出/`、切完用 `--images` 按镜号显式传序；")
    md.append("3. 空镜/远景优先一格无人、一格有人，方便肉眼分辨相邻两句；")
    md.append("4. 相近的历史场景（宫殿/城垣/船队）像素差异可能很大，**像素比对不能判语义对错**，只作白边/串图参考。")
    return "\n".join(md) + "\n", panels


def check(rows, images, groups):
    hard, soft = [], []
    for r in rows:
        if not r["src"]:
            hard.append(f"镜 {r['i']}：src 为空，没有画面")
            continue
        if r["motion"]:
            if not r["files"]:
                hard.append(f"镜 {r['i']}：动态镜 src={r['src']} 越界（motions 只有限几段）")
            continue
        if not r["files"]:
            hard.append(f"镜 {r['i']}：src={r['src']} 越界或图片列表为空")
            continue
        if is_placeholder(r["note"]):
            hard.append(f"镜 {r['i']}：note 仍是占位文本「{short(r['note'], 16)}」，无法核对画面情节")
            continue
        if not re.match(r"^【[^】]*】", r["note"]):
            soft.append(f"镜 {r['i']}：note 没按「【景别·主体】」开头，auto 运镜会判不准")
        if not r["line"]:
            soft.append(f"镜 {r['i']}：没有对应旁白句（lines.json 比镜头少）")
    # 宫格覆盖的镜要连续
    for base, panels, items in groups:
        idxs = [w for (_, _, w) in items if w is not None]
        if idxs and len(idxs) != max(idxs) - min(idxs) + 1:
            soft.append(f"宫格 {base}：服务的镜号不连续（{sorted(idxs)}），同张宫格不该隔段插")
    # 相邻两镜同一张图
    for a, b in zip(rows, rows[1:]):
        if a["files"] and b["files"] and a["files"] == b["files"]:
            soft.append(f"镜 {a['i']} 与镜 {b['i']} 用了同一张图，画面会连成一片")
    # 一镜两图却只给了一张
    for r in rows:
        if r["motion"]:
            continue
        if r["src"].startswith("img2:") and len(r["files"]) < 2:
            hard.append(f"镜 {r['i']}：src 写了 img2 但只解析出 {len(r['files'])} 张图")
    return hard, soft


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--root", help="工作目录；也可用 STORY_ROOT；默认当前目录")
    ap.add_argument("--panels", type=int, default=0,
                    help="出图方式：0=自动（按图名 _pNN 推断）/ 2=两宫格 / 4=四格；还没出图时用它排表")
    ap.add_argument("--out", help="清单输出路径；默认 episodes/<id>/宫格对照清单.md")
    ap.add_argument("--check", action="store_true", help="填图后体检：缺图 / 占位 note / 错位风险")
    ap.add_argument("--quiet", action="store_true", help="只写文件，屏幕只打一行结果")
    a = ap.parse_args()

    root = story_root(a.root)
    ep = root / "episodes" / a.id
    if not ep.is_dir():
        raise SystemExit(f"本篇目录不存在：{ep}")
    lines, cfg = load_episode(ep)
    title = cfg.get("title") or a.id
    rows, images, motions = collect(ep, lines, cfg)

    md, panels = build_md(ep, title, rows, images, motions, a.panels)
    out = pathlib.Path(a.out) if a.out else (ep / "宫格对照清单.md")
    out.write_text(md, encoding="utf-8")

    hard, soft = check(rows, images, grid_groups(rows, images))
    if not a.quiet:
        print(f"对照清单：{out}")
        print(f"  {len(rows)} 镜 / {len(images)} 图 / {len(motions)} 段动态素材"
              f" / 宫格分组 {max(panels, 0)} 格档")
        for r in rows[:24]:
            print(f"  镜{r['i']:>2} | {short(r['line'], 30):<32} | {short(r['note'], 30)}")
        if len(rows) > 24:
            print(f"  …（共 {len(rows)} 镜，完整表见清单文件）")

    if a.check:
        print("")
        for h in hard:
            print(f"  [硬问题] {h}")
        for s in soft:
            print(f"  [提示] {s}")
        if not hard and not soft:
            print("  [稿检] 逐镜对位无硬问题（语义仍需按清单逐格勾验）")
        print(f"  {'通过（提示项人工判断）' if not hard else '未通过：先修上列硬问题再出图/填图/渲染'}")
        raise SystemExit(1 if hard else 0)


if __name__ == "__main__":
    main()
