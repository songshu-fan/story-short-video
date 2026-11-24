# 把三组「发布候选」写进本篇 script.json，导演稿.md 会自动列出来
#
#   python set_titles.py --id 003 --root D:/workspace \
#       --cand "标题一|核心思想一|描述一" --cand "标题二|核心思想二|描述二" ...
#   python set_titles.py --id 003 --root D:/workspace --show      # 只看当前写了什么
#
# 写法（v2.11.0 三字段）：每组用 "|" 隔开 标题 | 核心思想 | 描述。
#   · 标题     默认就用本篇主题名（= 成片名），要张力才加破折号副题
#   · 核心思想 一句 15~25 字，说清这篇讲什么、立什么判断（可留空）
#   · 描述     60~120 字，前 30 字要是钩子（抖音折叠前只露两行）
# 向后兼容：只给两段时按「标题|描述」处理，核心思想留空（老口径的稿子照旧能写）。
# 字段落在 script.json 的 titles: [{"title","idea","desc"}]，
# 导演稿.md 的「发布候选」表直接读它——建篇/填图后刷新都不会丢。

import argparse
import json
import os
import pathlib
import sys


def story_root(arg_root):
    p = arg_root or os.environ.get("STORY_ROOT") or str(pathlib.Path.cwd())
    return pathlib.Path(p).resolve()


def parse_cand(raw, sep):
    """「标题|核心|描述」→ dict；只给两段按「标题|描述」。"""
    parts = [x.strip() for x in str(raw).split(sep, 2)]
    if len(parts) == 3:
        title, idea, desc = parts
    elif len(parts) == 2:
        title, idea, desc = parts[0], "", parts[1]
    else:
        title, idea, desc = parts[0], "", ""
    if not title:
        raise SystemExit(f"--cand 里没有标题：{raw!r}"
                         f"（格式是「标题{sep}核心思想{sep}描述」，两段时是「标题{sep}描述」）")
    return {"title": title, "idea": idea, "desc": desc}


def hints(item):
    out = []
    n = len(item.get("desc") or "")
    if n and n < 40:
        out.append(f"描述只有 {n} 字，建议 60~120 字（前 30 字要是钩子）")
    elif n > 140:
        out.append(f"描述 {n} 字偏长，建议压到 120 字内")
    i = len(item.get("idea") or "")
    if i and not (10 <= i <= 30):
        out.append(f"核心思想 {i} 字，建议 15~25 字")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--root", help="工作目录；也可用 STORY_ROOT；默认当前目录")
    ap.add_argument("--cand", nargs="*", action="append", default=[],
                    help='候选，一组一条：--cand "标题|核心思想|描述" "..." "..."'
                         '（也可重复写 --cand），建议给满三组')
    ap.add_argument("--sep", default="|", help="字段分隔符，默认 |")
    ap.add_argument("--show", action="store_true", help="只打印当前已写的候选，不改文件")
    a = ap.parse_args()

    ep = story_root(a.root) / "episodes" / a.id
    sp = ep / "script.json"
    if not sp.exists():
        raise SystemExit(f"找不到 {sp}，先跑 start_story.py")
    script = json.loads(sp.read_text(encoding="utf-8"))

    cands = [c for group in (a.cand or []) for c in (group or [])]
    cur = script.get("titles") or []
    if a.show or not cands:
        if not cur:
            print('本片还没有写入发布候选：用 --cand "标题|核心思想|描述" 给三组（建议 3 条）')
        else:
            print(f"当前 {len(cur)} 组发布候选：")
            for i, t in enumerate(cur, 1):
                print(f"  {i}. {t.get('title', '')}")
                if t.get("idea"):
                    print(f"     核心：{t['idea']}")
                print(f"     描述：{t.get('desc', '')}")
        return

    items = [parse_cand(c, a.sep) for c in cands]
    script["titles"] = items
    sp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {len(items)} 组发布候选 -> {sp}")
    for i, t in enumerate(items, 1):
        print(f"  {i}. {t['title']}")
        if t["idea"]:
            print(f"     核心：{t['idea']}")
        print(f"     描述：{t['desc']}（{len(t['desc'])} 字）")
        for h in hints(t):
            print(f"     [提示] {h}")

    # 刷新导演稿，让候选直接出现在用户要看的那份文档里
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "runtime" / "series"))
        import new_episode as ne
        ne.write_director_doc(ep)
    except Exception as e:
        print(f"  [提示] 导演稿刷新失败（不影响已写入的候选）：{e}")


if __name__ == "__main__":
    main()
