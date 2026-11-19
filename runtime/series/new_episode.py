# 一键建篇：为某一篇生成「专属数据层」，全系列规则一律沿用 series/template.json。
# 复用 v1/make.py 已跑通的 edge-tts → 时间轴 → 字幕链路，但不渲染视频（渲染交给 v12 引擎）。
# 不改 v1~v12 的任何文件。
#
#   python new_episode.py --id 002 --lines ../episodes/002/lines.json --structure 1,3,7,2
#   python new_episode.py --id 003            # 未提供稿子时，按原文切出待润色的初稿
#
# 产出 episodes/NNN/：meta.json / lines.json / script.json / shots.json /
#                     caption.srt / vo.wav / vo/ / head_voice.wav / 场景图/ / 成片/

import argparse
import asyncio
import hashlib
import json
import os
import pathlib
from pathlib import Path
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import edge_tts

from head_voice import TEMPLATE as HEAD_TEMPLATE, ensure as ensure_head_voice
from ffpath import ffmpeg, ffprobe

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
VOICE = "zh-CN-YunjianNeural"
HEAD, GAP, TAIL = 1.0, 0.32, 3.6   # 片尾留白 3.6s：给结尾针的定帧卡留窗口（原 1.5s）

# ---- 四段式剧本模板：固定的是功能顺序，段内句数自由 ----
SEGMENT_DEFS = [
    ("hook",     "① 3 秒钩子", 15),
    ("cause",    "② 背景起因", None),
    ("conflict", "③ 核心冲突", None),
    ("uplift",   "④ 结局升华", None),
]
HOOK_MAX_CHARS = 15
NEEDLE_MAX_CHARS = 20
BAD_HOOK_PREFIX = ("很久以前", "相传", "话说", "在很久")

# 默认景别模板（取自第 001 篇），按镜头号循环复用；
# 数量不是硬结构，新篇可按需在 shots.json 里逐镜改。
DEFAULT_LENS = [
    [1.00, 0.055,    0,   0, 260, False],
    [1.22, 0.045,  120,  20, 220, False],
    [1.45, 0.040, -110, -30, 180, False],
    [1.10, 0.060,  -70, -60, 240, False],
    [1.00, 0.060,    0,   0, 210, False],
    [1.16, 0.050,  100,  15, 190, False],
    [1.12, 0.055,  -90, -20, 230, False],
    [1.30, 0.040,   80,  30, 200, False],
    [1.00, 0.050,    0,   0, 270, False],
    [1.24, 0.045, -130,  20, 230, False],
    [1.14, 0.055,   90, -25, 250, False],
    [1.22, 0.045, -100,  10, 240, False],
    [1.06, 0.050,    0,   0, 300, False],
]
# 旧版兜底：没有 script.json 段信息时仍可建篇（不推荐）
BEATS = ["交代背景", "建立人物", "制造冲突", "转折", "高潮", "完成反转", "推进",
         "推进", "推进", "推进", "转史实", "转史实", "收束"]


def parse_structure(n, text=None):
    """四段各自句数。不给 --structure 时按 1 / 25% / 55% / 余下 推断，并标记待人工复核。"""
    if text:
        nums = [int(x.strip()) for x in text.split(",") if x.strip()]
        if len(nums) != 4 or any(v <= 0 for v in nums) or sum(nums) != n:
            raise SystemExit(f"--structure 须是 4 个正整数且总和等于旁白句数 {n}：{text}")
        return nums, False
    if n < 4:
        raise SystemExit(f"旁白至少 4 句才能分四段，当前 {n} 句；可用 --structure 显式指定")
    hook = 1
    cause = max(1, round((n - 1) * 0.25))
    conflict = max(1, round((n - 1) * 0.55))
    uplift = n - hook - cause - conflict
    if uplift < 1:
        conflict -= 1
        uplift = 1
    return [hook, cause, conflict, uplift], True


def count_chars(text):
    """按汉字计字数，忽略空白与标点。"""
    return len(re.sub(r"[\s，。！？；：、,.!?「」『』…—-]", "", text))


def build_script(ep, meta, lines, structure_text=None, needle=None, discussion=None):
    """写 script.json：四段归属 + 结尾针金句。lines.json 仍保持纯字符串数组。

    discussion 可选：评论区钩子问题（一句能吵起来的话）。不传则不写该键，
    产物与旧版逐字一致；传了则出片后随 项目说明_v12.json 一起输出。
    """
    n = len(lines)
    nums, auto = parse_structure(n, structure_text)
    segments, at = [], 0
    for (key, label, max_chars), cnt in zip(SEGMENT_DEFS, nums):
        segments.append({"key": key, "label": label,
                         "lines": list(range(at, at + cnt)),
                         "max_chars": max_chars})
        at += cnt
    gold = needle or lines[-1]
    hook_chars, needle_chars = count_chars(lines[0]), count_chars(gold)
    warns = []
    if hook_chars > HOOK_MAX_CHARS:
        warns.append(f"首句 {hook_chars} 字 > {HOOK_MAX_CHARS}，3 秒钩子偏长")
    if needle_chars > NEEDLE_MAX_CHARS:
        warns.append(f"金句 {needle_chars} 字 > {NEEDLE_MAX_CHARS}，字卡会显拥挤")
    if lines[0].startswith(BAD_HOOK_PREFIX):
        warns.append("首句是背景式开头，不是钩子")
    data = {
        "id": meta["id"], "title": meta["title"],
        "template": "四段式：① 3秒钩子 → ② 背景起因 → ③ 核心冲突 → ④ 结局升华",
        "auto": auto, "structure": nums, "segments": segments,
        "needle": gold,
        "hook_max_chars": HOOK_MAX_CHARS, "needle_max_chars": NEEDLE_MAX_CHARS,
        "hook_chars": hook_chars, "needle_chars": needle_chars,
        "warnings": warns,
        "_note": "auto=true 时四段区间按比例推断，定稿前人工核对 segments；超字只警告不拦截",
    }
    if discussion:
        data["discussion"] = discussion
    (ep / "script.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def beat_for(segments, i):
    """镜头 beat 由四段归属生成，可再按情节细化。"""
    for s in segments:
        if i in s["lines"]:
            return s["label"]
    return "推进"


def default_ending(needle=None, hold=2.5):
    """结尾针：A 文案针 + B 定帧卡共用同一句。needle 不写则引擎自动取 lines.json 末句。"""
    d = {"hold": hold, "gap": 0.45, "fade_out": 0.25}
    if needle:
        d["needle"] = needle       # 只在字卡文字与末句不同时才写
    return d


def ff(args, cwd):
    r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-v", "error", *args],
                       cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 失败：\n" + r.stderr[-2000:])
    return r


def probe(path, cwd):
    r = subprocess.run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8")
    return float(r.stdout.strip())


def srt_time(t):
    ms = int(round(t * 1000))
    return (f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:"
            f"{ms // 1000 % 60:02d},{ms % 1000:03d}")


def draft_from_source(text, max_lines=13):
    """没有人工润色稿时，按中文句末标点切一份初稿，标记待润色。
    max_lines 只是初稿上限、可改，不是结构限制；正式旁白句数由润色稿决定。"""
    body = "\n".join(l for l in text.splitlines()
                     if l.strip() and not l.strip().startswith("#"))
    parts = [s.strip() for s in re.split(r"(?<=[。！？])", body) if s.strip()]
    return parts[:max_lines], True


def build_audio_and_caption(ep, lines):
    """逐句 TTS → 静音拼接 → 响度归一 → SRT。

    两处提速：
    1) TTS 并发 3 路（原来逐句串行、每句还等 0.4s），单句仍保留 6 次重试 + 超时；
    2) 音频链按 digest 缓存：稿子没变就不再重做 mp3→wav 与 loudnorm 拼接。
    """
    vo = ep / "vo"
    vo.mkdir(exist_ok=True)
    digest = hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode("utf-8")).hexdigest()
    cache = vo / ".lines.sha"
    mp3s = [vo / f"{i:03d}.mp3" for i in range(1, len(lines) + 1)]
    if cache.exists() and cache.read_text(encoding="utf-8").strip() == digest \
            and all(p.exists() and p.stat().st_size > 0 for p in mp3s):
        print("  复用已有语音缓存")
    else:
        # 稿子变了就清掉旧分句，避免复用错文本的音频。
        for old in vo.glob("[0-9][0-9][0-9].mp3"):
            old.unlink()

        conc = max(1, int(os.environ.get("STORY_TTS_CONCURRENCY", "3") or 3))

        async def synth():
            sem = asyncio.Semaphore(conc)        # 默认 3 路并发：够快，也不至于被服务端限流

            async def one(i, text):
                target = vo / f"{i:03d}.mp3"
                async with sem:
                    for attempt in range(1, 7):
                        try:
                            # 服务端异常时 WebSocket 可能一直挂住，必须给单句设超时，
                            # 否则整批建篇会卡到被系统杀掉。
                            await asyncio.wait_for(
                                edge_tts.Communicate(text, VOICE).save(str(target)),
                                timeout=25)
                            if target.exists() and target.stat().st_size > 0:
                                return
                            raise RuntimeError("合成结果为空音频")
                        except Exception as e:
                            if target.exists() and target.stat().st_size == 0:
                                target.unlink()
                            if attempt == 6:
                                raise
                            print(f"  第 {i} 句 TTS 失败，重试 {attempt}/5（{type(e).__name__}）")
                            await asyncio.sleep(min(1.0 * attempt, 3.0))

            await asyncio.gather(*(one(i, t) for i, t in enumerate(lines, 1)))

        print(f"  合成 {len(lines)} 句旁白（{conc} 路并发）…")
        asyncio.run(synth())
        cache.write_text(digest, encoding="utf-8")

    # ---- 音频链缓存：mp3→wav→拼接→loudnorm 的结果按 digest+参数缓存 ----
    stamp = vo / ".audio.json"
    sig = {"v": 2, "lines": digest, "head": HEAD, "gap": GAP, "tail": TAIL}
    if (ep / "vo.wav").exists() and stamp.exists() \
            and json.loads(stamp.read_text(encoding="utf-8")) == sig:
        durations = json.loads(stamp.read_text(encoding="utf-8"))["durations"]
        print("  复用已有音频拼接（稿子与留白未变）")
    else:
        def conv(i):
            ff(["-i", f"vo/{i:03d}.mp3", "-ar", "24000", "-ac", "1",
                "-c:a", "pcm_s16le", f"vo/{i:03d}.wav"], ep)

        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(conv, range(1, len(lines) + 1)))
        durations = []
        durs = {}
        for i in range(1, len(lines) + 1):
            durs[f"{i:03d}"] = probe(f"vo/{i:03d}.wav", ep)
        durations = [durs[f"{i:03d}"] for i in range(1, len(lines) + 1)]

        for name, sec in [("head.wav", HEAD), ("gap.wav", GAP), ("tail.wav", TAIL)]:
            ff(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", f"{sec:.3f}", "-c:a", "pcm_s16le", f"vo/{name}"], ep)

        seq, t = ["vo/head.wav"], HEAD
        for i, d in enumerate(durations, 1):
            seq.append(f"vo/{i:03d}.wav")
            t += d
            if i < len(durations):
                seq.append("vo/gap.wav")
                t += GAP
        seq.append("vo/tail.wav")

        (ep / "concat.txt").write_text("".join(f"file '{p}'\n" for p in seq), encoding="utf-8")
        ff(["-f", "concat", "-safe", "0", "-i", "concat.txt",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:linear=true",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "vo.wav"], ep)
        stamp.write_text(json.dumps({**sig, "durations": durations}), encoding="utf-8")

    seq, cues, t = ["vo/head.wav"], [], HEAD
    for i, d in enumerate(durations, 1):
        cues.append((t, t + d, lines[i - 1]))
        t += d
        if i < len(durations):
            t += GAP
    total = t + TAIL

    srt = "\n".join(
        f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{txt}\n"
        for n, (a, b, txt) in enumerate(cues, 1))
    (ep / "caption.srt").write_text(srt, encoding="utf-8")
    return total, len(cues)


def build_shots(ep, meta, n_lines, drafted, segments=None, ending=None):
    """镜头表骨架：镜头数 = 旁白句数；图片张数默认一镜一图，两者都可再改。"""
    shots = []
    for i in range(n_lines):
        lens = list(DEFAULT_LENS[i] if i < len(DEFAULT_LENS) else DEFAULT_LENS[-1])
        lens[5] = False
        shots.append({
            "n": i,
            "beat": (beat_for(segments, i) if segments
                     else (BEATS[i] if i < len(BEATS) else "推进")),
            "src": f"img:{i}",        # 语义引用第 i 张图；动态镜改 motion:j
            "lens": lens,
            "note": "",
        })
    data = {
        "id": meta["id"], "title": meta["title"], "volume": meta["volume"],
        "engine": "v12", "status": "骨架：图片路径待填（张数可自由增减）",
        "paths_relative_to": "本文件所在目录",
        "motions": [],
        "images": [None] * n_lines,
        "accents": sorted({i for i in (2, 5, n_lines - 1) if 0 <= i < n_lines}),
        # 片头文案按篇自动带出标题；音轨由 head_voice.py 按方案 A 生成
        "head_text": HEAD_TEMPLATE.format(title=meta["title"]),
        "shots": shots,
        "todo": [
            f"补 {n_lines} 张（或任意张数）本篇场景图到 场景图/ 并填 images",
            "需要动态镜时填 motions，并把对应镜的 src 改成 motion:j，加 motion/phase",
            "为每个镜头写画面提示词 note",
            "人工润色 lines.json 后重跑本脚本刷新语音与字幕" if drafted
            else "渲染前确认旁白稿已定稿",
        ],
    }
    if ending is not None:
        data["ending"] = ending
    (ep / "shots.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="篇号/短名，用作 episodes/ 下的目录名")
    ap.add_argument("--lines", help="人工润色后的旁白稿 JSON 数组路径")
    ap.add_argument("--structure", help="四段各自句数，如 1,3,7,2；不给则按比例推断")
    ap.add_argument("--needle", help="结尾针字卡金句；不给则用 lines.json 末句")
    ap.add_argument("--tail-hold", type=float, default=2.5,
                    help="片尾定帧卡停留秒数（默认 2.5）")
    # 技能包自包含：没有 manifest 时也能直接建篇（通用用法见 scripts/start_story.py）
    ap.add_argument("--title", help="故事标题；缺省时从 manifest 取")
    ap.add_argument("--volume", default="自定义", help="卷名/分类")
    ap.add_argument("--root", help="工作目录；默认 runtime 目录")
    a = ap.parse_args()

    manifest_path = ROOT / "manifest.json"
    stories = (json.loads(manifest_path.read_text(encoding="utf-8")).get("stories", [])
               if manifest_path.exists() else [])
    hit = next((s for s in stories if s["id"] == a.id), None)
    if hit is None and not a.title:
        raise SystemExit(
            f"没有 manifest 条目，也没有给 --title：请加 --title 指定故事标题"
            f"（通用建篇请直接用 scripts/start_story.py --id ... --title ... --lines ...）")

    base = pathlib.Path(a.root).resolve() if a.root else ROOT
    ep = base / "episodes" / a.id
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "场景图").mkdir(exist_ok=True)
    (ep / "成片").mkdir(exist_ok=True)

    meta = {"id": a.id,
            "volume": (hit or {}).get("volume") or a.volume,
            "title": (hit or {}).get("title") or a.title,
            "source": (f"../../stories/{a.id}.md" if hit else "命令行 --lines")}
    (ep / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if a.lines:
        lines = json.loads(pathlib.Path(a.lines).read_text(encoding="utf-8"))
        drafted = False
    elif hit and (ROOT / hit.get("file", "")).is_file():
        src = (ROOT / hit["file"]).read_text(encoding="utf-8")
        lines, drafted = draft_from_source(src)
    else:
        raise SystemExit("需要 --lines 指定旁白稿 JSON，或提供该篇的 manifest 源文")
    (ep / "lines.json").write_text(
        json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")

    chars = sum(len(re.sub(r"\s", "", s)) for s in lines)
    print(f"第 {a.id} 篇《{meta['title']}》：{len(lines)} 句 / {chars} 字"
          f"{'（原文初稿，待润色）' if drafted else '（人工润色稿）'}")
    script = build_script(ep, meta, lines, a.structure, a.needle)
    print(f"  四段 {script['structure']}"
          f"{'（按比例推断，待人工复核）' if script['auto'] else ''}"
          f" / 钩子 {script['hook_chars']} 字 / 金句 {script['needle_chars']} 字")
    for w in script["warnings"]:
        print(f"  [提示] {w}")
    total, cues = build_audio_and_caption(ep, lines)
    build_shots(ep, meta, len(lines), drafted, script["segments"],
                default_ending(a.needle, a.tail_hold))
    ensure_head_voice(ep)       # 片头方案 A：按本篇标题合成说书人音轨（001 复用原声）

    print(f"  音频 {total:.1f}s / {cues} 条字幕 / {cues} 镜")
    print(f"  结尾针：末句金句 + 片尾定帧卡（尾部留白 {TAIL}s）")
    print(f"  产出：{ep}")
    print(f"  预计成片 {total + 5.0:.1f}s（含 5s 片头；title 片头模式按 head.len 重算）｜"
          f"句数与时长按本篇需要自定，90~105s 只是参考区间")



def _src_label(src, images, motions):
    """把 shots[].src 解析成可读素材名（img:i / img2:i,j / motion:j）。"""
    src = str(src or "")
    def name(i, lst):
        try:
            return Path(lst[i]).name
        except Exception:
            return f"#{i}(越界)"
    if src.startswith("img2:"):
        a, b = src[5:].split(",", 1)
        return f"{name(int(a), images)} + {name(int(b), images)}（前半/后半）"
    if src.startswith("img:"):
        return name(int(src[4:]), images)
    if src.startswith("motion:"):
        return f"[动态] {name(int(src[7:]), motions)}"
    return src or "（空）"


def write_director_doc(ep):
    """把本篇 shots.json / lines.json / script.json 汇成 导演稿.md。

    start_story 与 fill_shots 结束时自动调用；零积分、纯本地，可随时重跑刷新。
    手改本文件会被下次刷新覆盖——正式的逐镜备注仍以 shots.json 的 note 为准。
    """
    ep = pathlib.Path(ep)
    cfg = json.loads((ep / "shots.json").read_text(encoding="utf-8"))
    lines = json.loads((ep / "lines.json").read_text(encoding="utf-8"))
    try:
        script = json.loads((ep / "script.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        script = {}
    shots = cfg.get("shots", [])
    images = cfg.get("images") or []
    motions = cfg.get("motions") or []
    chars = sum(len(re.sub(r"\s", "", s)) for s in lines)
    seg = script.get("structure") or "-"
    needle = script.get("needle") or (lines[-1] if lines else "")
    head_img = cfg.get("head_image")
    ending = cfg.get("ending") or {}

    rows = []
    for s in shots:
        i = s.get("n", 0)
        text = lines[i] if 0 <= i < len(lines) else ""
        direction = "上→下" if i % 2 == 0 else "下→上"   # 正片奇数镜上→下、偶数镜下→上
        rows.append(
            f"| {i + 1} | {s.get('beat', '')} | {text} | "
            f"{_src_label(s.get('src'), images, motions)} | {direction} | "
            f"{s.get('note', '')} |")

    # 片头模式（shots.json.head）：host=说书人 5 秒口播；title=只有标题卡，无人像
    head_cfg = cfg.get("head") or {}
    if str(head_cfg.get("mode", "host")).lower() == "title":
        hlen = head_cfg.get("len")
        voice = bool(head_cfg.get("voice", True))
        if hlen is None and voice:
            # 有口播时片头长度跟着语音实测走（引擎同一口径：语音终点 + 0.95s）
            hv_wav = ep / "head_voice.wav"
            if hv_wav.exists():
                try:
                    from head_voice import speech_span
                    hlen = min(4.2, max(2.6, speech_span(hv_wav)[1] + 0.95))
                except Exception:
                    hlen = None
        if hlen is None:
            hlen = min(3.6, max(2.0, 0.9 + 0.21 * len(cfg.get("title", ""))))
        head_desc = (f"标题卡+底图，无人像；保留口播与字幕，{float(hlen):.2f}s"
                     if voice else f"只有标题卡+底图，无人像、无口播，{float(hlen):.2f}s")
    else:
        head_desc = f"5s 说书人：「{head_cfg.get('text') or cfg.get('head_text', '')}」"

    doc = f"""# 导演稿：《{cfg.get('title', '')}》（{cfg.get('id', '')}）

- 卷：{cfg.get('volume', '')} ｜ 引擎：{cfg.get('engine', 'v12')}
- 镜头数 {len(shots)}（= 旁白句数）｜ 旁白 {chars} 字 ｜ 四段 {seg}
- 片头（{head_desc}）
- 片头底图：{head_img if head_img else '（未设置，沿用引擎通用底图 bg1.jpg）'}
- 结尾针：金句「{needle}」+ 片尾定帧卡 {ending.get('hold', 2.5)}s
- 图片 {len(images)} 张 ｜ 动态素材 {len(motions)} 段
- 状态：{cfg.get('status', '')}

> 本文件由脚本自动生成（建篇/填图后刷新）；逐镜备注的正式数据在 shots.json 的 note。

| 镜 | 段 | 旁白 | 画面素材 | 上下浮动 | 画面备注 |
|--:|---|---|---|---|---|
""" + "\n".join(rows) + "\n"
    (ep / "导演稿.md").write_text(doc, encoding="utf-8")
    print(f"  导演稿 -> {ep / '导演稿.md'}")
    return ep / "导演稿.md"


if __name__ == "__main__":
    main()
