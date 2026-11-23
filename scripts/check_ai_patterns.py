# 旁白稿 A 层机械化体检：emoji / 弯引号 / markdown 符号 / 空洞词 / 半角破折号。
# 纯标准库、离线、零积分、默认不写任何文件（--fix-quotes 才改稿）。
#
#   python check_ai_patterns.py --lines <root>/episodes/<id>/lines.json
#   python check_ai_patterns.py --lines lines.json --format 9:16
#   python check_ai_patterns.py --lines lines.json --fix-quotes     # 弯引号换成「」，其余照报
#
# 退出码：0 = 干净（可能带提示），1 = 有硬伤。B 层（排比/称呼/情感标签）与 C 层靠人工，脚本不碰。

import argparse
import json
import pathlib
import re
import sys

# ---- 硬伤：命中即不可建篇（进 TTS 会被念错，进 ASS 会出方块/糊字）----

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"      # 表情、符号、旗帜、补充平面
    "\U00002600-\U000027BF"       # 常见符号 ✅❌⚠⭐
    "\U00002B00-\U00002BFF"       # ⬛⬜等
    "\U0000FE0F\U0000200D"        # 变体选择符 / 零宽连接
    "\U00002190-\U000021FF"       # 箭头 →←↑↓
    "]")

# markdown / 富文本残留：lines.json 必须是纯字符串
MARKDOWN_RES = [
    (re.compile(r"\*\*"), "机械加粗 **"),
    (re.compile(r"__"), "下划线 __"),
    (re.compile(r"`"), "代码符 `"),
    (re.compile(r"^\s*#{1,6}\s"), "标题符 #"),
    (re.compile(r"^\s*>\s"), "引用符 >"),
    (re.compile(r"\*"), "星号 *"),
    (re.compile(r"\[[^\]]*\]\([^)]*\)"), "markdown 链接"),
]

CURLY_QUOTES = {"\u201c": "\u300c", "\u201d": "\u300d", "\u2018": "\u300c", "\u2019": "\u300d"}

# A 层关键词（空洞升华 / 模糊归因 / 填充废话 / 万能积极结尾 / 客服腔 / 过度对冲）
A_WORDS = [
    "标志着", "彰显着", "见证了", "在历史长河中", "谱写了新篇章", "谱写了壮丽诗篇",
    "专家认为", "专家分析", "研究表明", "有资料显示", "众所周知",
    "值得注意的是", "需要指出的是", "在这个时候", "总的来说", "综上所述",
    "未来可期", "令人深思", "值得我们学习", "引人深思",
    "希望对你有帮助", "当然可以", "让我们来看看", "接下来介绍", "接下来我们",
    "可能或许大概", "在一定程度上", "也许可以说", "不得不说",
]

# 半角连字符当长破折号：汉字/汉字、汉字-空格-汉字
DASH_RE = re.compile(r"(?<=[\u4e00-\u9fff])-(?=[\u4e00-\u9fff])|[\u4e00-\u9fff]\s-\s[\u4e00-\u9fff]")
# 半角标点混入中文（句子里出现 , . ! ? ; : 紧邻汉字）
HALF_PUNCT_RE = re.compile(r"(?<=[\u4e00-\u9fff])[,.;:!?](?=[\u4e00-\u9fff]?)")

PUNCT = "，。、；：？！「」『』（）《》…—·\"'‘’“” \t\r\n,.;:!?()[]{}<>-~"
MAX_CHARS = {"16:9": 22, "9:16": 18}


def char_count(text):
    """按实际进字幕的汉字/字母数字计字数，标点与空白不计。"""
    return sum(1 for ch in text if ch not in PUNCT)


def check_text(text):
    """返回 (硬伤列表, 提示列表)，每项是短描述。"""
    hard, warn = [], []

    m = EMOJI_RE.search(text)
    if m:
        hard.append(f"emoji/装饰符「{m.group(0)}」")
    for rx, label in MARKDOWN_RES:
        mm = rx.search(text)
        if mm:
            hard.append(f"markdown 残留：{label}（{mm.group(0)[:12]}）")
            break
    curl = [q for q in CURLY_QUOTES if q in text]
    if curl:
        hard.append("弯引号 " + "".join(curl) + "（改「」或去掉）")
    hit = [w for w in A_WORDS if w in text]
    if hit:
        hard.append("空洞/套话：" + "、".join(hit))
    if DASH_RE.search(text):
        hard.append("半角 - 当破折号（改逗号或断句）")
    if HALF_PUNCT_RE.search(text):
        hard.append("半角标点混入中文（改全角）")

    if text.count("——") > 1:
        warn.append(f"破折号堆砌（{text.count('——')} 处，最多留一个）")
    return hard, warn


def run(lines, fmt="16:9"):
    """lines: 字符串数组。返回 (hard_count, warn_count, reports)。"""
    hard_n = warn_n = 0
    reports = []
    limit = MAX_CHARS.get(fmt, 22)
    for i, raw in enumerate(lines):
        text = str(raw)
        hard, warn = check_text(text)
        n = char_count(text)
        if n > limit:
            warn.append(f"{n} 字，超过{fmt} 单句上限 {limit}")
        if hard or warn:
            reports.append((i, hard, warn, text))
        hard_n += len(hard)
        warn_n += len(warn)
    return hard_n, warn_n, reports


def main():
    ap = argparse.ArgumentParser(
        description="旁白稿 A 层体检：emoji / 弯引号 / markdown / 空洞套话 / 半角破折号。"
                    "退出码 0=干净，1=有硬伤。B/C 层靠人工判断。")
    ap.add_argument("--lines", required=True, help="旁白稿 JSON 字符串数组路径")
    ap.add_argument("--format", choices=["16:9", "9:16"], default="16:9",
                    help="画幅，决定单句字数提示上限（16:9=22 字 / 9:16=18 字）")
    ap.add_argument("--fix-quotes", action="store_true",
                    help="把弯引号 “ ” ‘ ’ 就地换成「」，写回文件；其余硬伤照报不修")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出报告（供脚本消费）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    a = ap.parse_args()

    p = pathlib.Path(a.lines)
    if not p.is_absolute() and not p.exists():
        raise SystemExit(f"稿子不存在：{p}")
    lines = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(lines, list) or not lines:
        raise SystemExit("旁白稿必须是非空 JSON 字符串数组")

    if a.fix_quotes:
        fixed = 0
        for i, s in enumerate(lines):
            new = str(s)
            for k, v in CURLY_QUOTES.items():
                if k in new:
                    fixed += new.count(k)
                    new = new.replace(k, v)
            lines[i] = new
        p.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
        if not a.quiet:
            print(f"[修复] 弯引号替换 {fixed} 处 -> 「」（已写回 {p.name}）")

    hard_n, warn_n, reports = run(lines, a.format)

    if a.json:
        print(json.dumps({"lines": str(p), "format": a.format,
                          "hard": hard_n, "warn": warn_n,
                          "reports": [{"line": i + 1, "text": t, "hard": h, "warn": w}
                                      for i, h, w, t in reports]},
                         ensure_ascii=False, indent=2))
    else:
        for i, hard, warn, text in reports:
            for h in hard:
                print(f"[硬伤] 句{i + 1}：{h}  ← {text[:40]}")
            for w in warn:
                print(f"[提示] 句{i + 1}：{w}  ← {text[:40]}")
        if not a.quiet:
            if hard_n:
                print(f"\n共 {hard_n} 处硬伤 / {warn_n} 处提示 —— 未通过，先改稿再建篇")
            elif warn_n:
                print(f"\n0 处硬伤 / {warn_n} 处提示 —— 通过，提示项人工判断")
            else:
                print("\n0 处硬伤 / 0 处提示 —— 通过")

    sys.exit(1 if hard_n else 0)


if __name__ == "__main__":
    main()
