# 把本篇场景图 / 动态素材填进 shots.json，清掉第 001 篇的占位标记。
# 图片张数与动态素材段数都自由，没有固定数量。
#
#   python fill_shots.py --id 003 --images-dir "D:/.../episodes/003/场景图"
#   python fill_shots.py --id 003 --images a.jpg b.jpg c.jpg          # 任意张数
#   python fill_shots.py --id 003 --images-dir ".../场景图" --pairs    # 一镜两图，自动配对 img2
#   python fill_shots.py --id 003 --motions clip1.mp4 clip2.mp4 --dynamic-at 2,5
#   python fill_shots.py --id 003 --motion clip.mp4 --dynamic-at 3    # 单段也可
#
# 换图后会自动把越界的 img:i / img2:i,j 重映射回新图片列表，不用手工改 src。
# --pairs：开工确认第 5 项选「一镜 2 张」时用。图片按 2 张一组配给每个镜
#          （镜 k -> img2:2k,2k+1），自动跳过动态镜、不为它消耗图片。
# --dynamic-at 指定哪些镜改用动态素材；同一段素材被多个镜引用时引擎自动 split，
# 各镜可用不同 phase 取不同画面。

import argparse
import json
import os
import pathlib
import re
import sys

EXTS = (".jpg", ".jpeg", ".png", ".webp")


def story_root(arg_root):
    """工作目录：命令行 --root > 环境变量 STORY_ROOT > 当前工作目录。"""
    p = arg_root or os.environ.get("STORY_ROOT") or str(pathlib.Path.cwd())
    return pathlib.Path(p).resolve()


def resolve_path(path, ep):
    """路径解析（命令行可能从任何工作目录执行）：
    绝对路径直接用；相对路径先按本篇目录、再按当前工作目录找，都不存在则返回本篇目录下的候选，
    便于报错时打印一个能看懂的绝对路径。"""
    p = pathlib.Path(path)
    if p.is_absolute():
        return p.resolve()
    cand = (ep / p).resolve()
    if cand.exists():
        return cand
    cwd_cand = (pathlib.Path.cwd() / p).resolve()
    if cwd_cand.exists():
        return cwd_cand
    return cand


def rel_to_ep(path, ep):
    """放在本篇 场景图/ 下就写相对路径，否则写绝对路径（引擎都支持）。"""
    p = resolve_path(path, ep)
    try:
        r = p.relative_to(ep.resolve())
        if not str(r).startswith(".."):
            return r.as_posix()
    except ValueError:
        pass
    return p.as_posix()


def spread_slots(total, count):
    """把 count 个素材等间隔铺到 total 个位置，返回每个位置用的素材下标。
    作用：素材比位置少时，重复均匀散在全片，不出现「后段连着吃同一张」。"""
    if total <= 0 or count <= 0:
        return [0] * max(total, 0)
    return [min(count - 1, int(j * count / total)) for j in range(total)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--root", help="工作目录；也可用环境变量 STORY_ROOT；默认当前目录")
    ap.add_argument("--images-dir", help="本篇场景图目录，按文件名排序取全部图片")
    ap.add_argument("--images", nargs="*", help="任意张数的图片路径")
    ap.add_argument("--motions", nargs="*", help="任意段数的动态素材路径")
    ap.add_argument("--motion", help="单段动态素材路径（--motions 的简写，兼容旧用法）")
    ap.add_argument("--dynamic-at", default="",
                    help="哪些镜改用动态素材，0 起算逗号分隔，如 '2,5'")
    ap.add_argument("--pairs", action="store_true",
                    help="一镜两图：图片按 2 张一组配给每个镜（镜 k -> img2:2k,2k+1），"
                         "自动跳过动态镜；开工确认第 5 项选「一镜 2 张」时用")
    ap.add_argument("--head-image",
                    help="片头底图路径；不给时若 head_image 为空则默认取中间那张本篇图")
    ap.add_argument("--host", choices=["ancient", "modern"],
                    help="片头说书人：ancient=古装 / modern=现代休闲学者；不给则沿用 shots.json.host")
    a = ap.parse_args()

    ep = story_root(a.root) / "episodes" / a.id
    cfg_path = ep / "shots.json"
    if not cfg_path.exists():
        raise SystemExit(f"找不到 {cfg_path}，先跑 start_story.py")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # ---- 图片：任意张数 ----
    if a.images_dir:
        d = resolve_path(a.images_dir, ep)          # 相对路径不再按当前目录裸解析
        if not d.is_dir():
            raise SystemExit(f"场景图目录不存在：{d}\n"
                             f"  （相对路径已按本篇目录 {ep} 与当前目录 {pathlib.Path.cwd()} 各试一次）")
        imgs = sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS)
        if not imgs:
            raise SystemExit(f"场景图目录里没有图片：{d}（支持 jpg/jpeg/png/webp）")
        print(f"  场景图目录：{d}")
    elif a.images:
        imgs = [resolve_path(p, ep) for p in a.images]
    else:
        imgs = []

    if imgs:
        missing = [str(p) for p in imgs if not p.exists()]
        if missing:
            raise SystemExit("图片不存在：" + "、".join(missing))
        cfg["images"] = [rel_to_ep(p, ep) for p in imgs]
        k, remapped = len(imgs), 0
        for s in cfg.get("shots", []):
            src = str(s.get("src", "")).strip()
            if src.startswith("img:") and src[4:].isdigit() and int(src[4:]) >= k:
                s["src"] = f"img:{int(src[4:]) % k}"
                remapped += 1
            elif src.startswith("img2:"):              # 一镜两图：两个下标都要重映射
                ids = [x.strip() for x in src[5:].split(",")]
                if len(ids) == 2 and all(x.isdigit() for x in ids):
                    vals = [int(x) % k for x in ids]
                    if vals != [int(x) for x in ids]:
                        s["src"] = f"img2:{vals[0]},{vals[1]}"
                        remapped += 1
        print(f"  已填入 {k} 张场景图（归属校验见下方）"
              + (f"，{remapped} 个镜头的 img:i 已重映射" if remapped else ""))

    # ---- 动态素材：任意段数 ----
    new_motions = list(a.motions) if a.motions is not None else None
    if new_motions is None and a.motion:
        new_motions = [a.motion]
    if new_motions is not None:
        missing = [p for p in new_motions if not resolve_path(p, ep).exists()]
        if missing:
            raise SystemExit("动态素材不存在：" + ", ".join(missing))
        cfg["motions"] = [rel_to_ep(p, ep) for p in new_motions]
        cfg.pop("motion", None)                 # 旧单字段不再与新字段并存
        print(f"  动态素材 {len(new_motions)} 段 -> {cfg['motions']}")

    motions = cfg.get("motions") or ([cfg["motion"]] if cfg.get("motion") else [])

    # ---- 片头说书人：显式指定则覆盖 ----
    if a.host:
        cfg["host"] = a.host
        print(f"  片头说书人 -> {a.host}（{'古装' if a.host == 'ancient' else '现代休闲学者'}）")

    # ---- 片头底图：默认取中间那张本篇图（避开首镜钩子与末镜收束） ----
    if a.head_image:
        p = resolve_path(a.head_image, ep)
        if not p.exists():
            raise SystemExit(f"--head-image 图片不存在：{p}")
        cfg["head_image"] = rel_to_ep(p, ep)
        print(f"  片头底图 -> {cfg['head_image']}")
    elif cfg.get("images") and not cfg.get("head_image"):
        imgs = cfg["images"]
        cfg["head_image"] = imgs[len(imgs) // 2]
        print(f"  片头底图 -> {cfg['head_image']}"
              f"（默认中间那张本篇图，可用 --head-image 覆盖）")

    if a.dynamic_at:
        idx = [int(x.strip()) for x in a.dynamic_at.split(",") if x.strip()]
        if not motions:
            raise SystemExit("指定 --dynamic-at 前需先用 --motions / --motion 给动态素材")
        shots = cfg.get("shots", [])
        bad = [i for i in idx if not 0 <= i < len(shots)]
        if bad:
            raise SystemExit(f"动态镜序号越界：{bad}（共 {len(shots)} 镜）")
        for k, i in enumerate(idx):
            shots[i].update({
                "src": f"motion:{k % len(motions)}",
                "motion": True,
                "phase": round(0.4 + (k % 4) * 1.5, 2),
                "crop16x9": shots[i].get("crop16x9", True),
            })
        print(f"  镜 {idx} 已改为动态镜（共 {len(motions)} 段素材）")

    # ---- 一镜两图：图片按 2 张一组配给每个镜（开工确认第 5 项）----
    shots = cfg.get("shots", [])
    static = [s for s in shots if not s.get("motion")]
    if a.pairs:
        imgs = cfg.get("images") or []
        if len(imgs) < 2:
            raise SystemExit("--pairs 至少需要 2 张图片（一镜两图）")
        need = 2 * len(static)
        pairs = len(imgs) // 2
        # 图片够用就顺序成对；不够就用等间隔复用，让重复均匀散在全片
        slots = (spread_slots(len(static), pairs) if pairs < len(static)
                 else list(range(len(static))))
        for s, p in zip(static, slots):
            i = 2 * p if 2 * p + 1 < len(imgs) else len(imgs) - 2
            s["src"] = f"img2:{i},{i + 1}"
        print(f"  一镜两图：{len(static)} 个镜已配对为 img2（需 {need} 张 / 实有 {len(imgs)} 张）")
        if len(imgs) < need:
            print(f"  [警告] 图片差 {need - len(imgs)} 张，已等间隔复用同一批图，"
                  "建议补图后重跑（画面会重复，但不会堆在某一段）")
        if len(imgs) % 2:
            print("  [提示] 图片数为奇数，末张图不会被用到；建议凑成偶数")
    elif static and cfg.get("images") and len(cfg["images"]) < len(static):
        # ---- 一镜一图但图片不够：等间隔复用，避免后段连着吃同一张（均匀分配）-----
        imgs = cfg["images"]
        for s, i in zip(static, spread_slots(len(static), len(imgs))):
            s["src"] = f"img:{i}"
        print(f"  图片 {len(imgs)} 张 < 镜头 {len(static)} 个：已按等间隔均匀复用"
              f"（每 {len(static) / len(imgs):.1f} 镜一轮），重复不会集中在片尾）")

    # ---- 素材归属校验：新故事不得沿用第 001 篇的画面 ----
    paths = list(cfg.get("images") or []) + list(cfg.get("motions") or [])
    if cfg.get("motion"):
        paths.append(cfg["motion"])
    foreign = ([p for p in paths
                if p and re.search(r"(^|/)001/", str(p).replace("\\", "/"))]
               if ep.name != "001" else [])

    if foreign:
        cfg["status"] = "工程验证：仍含内置占位素材，不是成品画面（正式渲染会被拦截）"
        cfg["todo"] = [t for t in cfg.get("todo", []) if "本篇素材已就位" not in t]
        cfg["todo"].append("替换掉指向 runtime 占位的素材，改成本篇生成的画面")
        print("[警告] 以下素材仍指向内置占位（runtime/001），不算本篇画面：")
        for p in foreign[:5]:
            print("   ", p)
    elif imgs or new_motions is not None or a.dynamic_at:
        for s in cfg.get("shots", []):
            if s.get("note") in ("内置占位素材，待替换为本篇画面",
                                 "本篇画面待生成"):
                s["note"] = "本篇画面，待补写提示词备注"
        cfg["status"] = "本篇素材已就位（镜头备注仍需按情节核对）"
        cfg["todo"] = [t for t in cfg.get("todo", [])
                       if not re.search(r"场景图|动态镜|占位素材", t)]
        cfg["todo"].append("渲 --test 45 抽帧检查字幕、镜头差异、无异常")
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已更新 {cfg_path}")
    print("  提醒：换素材后仍需按情节填 beat/note；16:9 动态素材记得把 crop16x9 改 false")
    # 导演稿.md：建篇/填图/改片头底图后自动刷新（零积分、纯本地）。
    try:
        series_dir = pathlib.Path(__file__).resolve().parent.parent / "runtime" / "series"
        sys.path.insert(0, str(series_dir))
        import new_episode as ne
        ne.write_director_doc(ep)
    except Exception as e:
        print(f"  [提示] 导演稿生成失败（不影响填图）：{e}")


if __name__ == "__main__":
    main()
