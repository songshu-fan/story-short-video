# 把三组「发布标题 + 一句描述」写进本篇 script.json，导演稿.md 会自动列出来
#
#   python set_titles.py --id 003 --root D:/workspace \
#       --cand "标题一|描述一" --cand "标题二|描述二" --cand "标题三|描述三"
#   python set_titles.py --id 003 --root D:/workspace --show      # 只看当前写了什么
#
# 写法：每组用 "|" 隔开标题与描述（标题里没有 | 即可，也可用 --sep 换分隔符）。
# 字段落在 script.json 的 titles: [{"title": "...", "desc": "..."}]，
# 导演稿.md 的「发布候选」表直接读它——建篇/填图后刷新都不会丢。

import argparse
import json
import os
import pathlib
import sys


def story_root(arg_root):
    p = arg_root or os.environ.get("STORY_ROOT") or str(pathlib.Path.cwd())
    return pathlib.Path(p).resolve()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--root", help="工作目录；也可用 STORY_ROOT；默认当前目录")
    ap.add_argument("--cand", nargs="*", action="append", default=[],
                    help='候选，一组一条：--cand "标题|描述" "标题|描述" "标题|描述"'
                         '（也可重复写 --cand），建议给满三组')
    ap.add_argument("--sep", default="|", help="标题与描述的分隔符，默认 |")
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
            print("本片还没有写入发布候选：用 --cand \"标题|描述\" 给三组（建议 3 条）")
        else:
            print(f"当前 {len(cur)} 组发布候选：")
            for i, t in enumerate(cur, 1):
                print(f"  {i}. {t.get('title', '')}")
                print(f"     {t.get('desc', '')}")
        return

    items = []
    for c in cands:
        title, _, desc = str(c).partition(a.sep)
        title, desc = title.strip(), desc.strip()
        if not title:
            raise SystemExit(f"--cand 里没有标题：{c!r}"
                             f"（格式是「标题{a.sep}描述」）")
        items.append({"title": title, "desc": desc})
    script["titles"] = items
    sp.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {len(items)} 组发布候选 -> {sp}")
    for i, t in enumerate(items, 1):
        print(f"  {i}. {t['title']}　|　{t['desc']}")

    # 刷新导演稿，让候选直接出现在用户要看的那份文档里
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "runtime" / "series"))
        import new_episode as ne
        ne.write_director_doc(ep)
    except Exception as e:
        print(f"  [提示] 导演稿刷新失败（不影响已写入的候选）：{e}")


if __name__ == "__main__":
    main()
