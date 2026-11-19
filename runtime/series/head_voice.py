# 片头说书人音轨「方案 A」：按篇换词，零积分。
#
# 背景：v4/talk_test.mp4 的视频流是 AI 口型说书人（全系列复用，不重制），
#       但它自带的音轨固定念「今天讲一个盘古开天辟地的故事。」，换篇会说错篇名。
# 做法：只替换音轨，视频流、5.0s 时间轴、字幕一律不动。
#       音色锁定 v4 原说书人（zh-CN-YunjianNeural，与 talk_ref.mp3 波形相关 1.0000）。
#       新词保持原速，只把语音起点对齐到 0.173s；篇名偏长时才轻微加速（≤1.20x），
#       绝不为填满口型窗口给声音变速——变速会毁掉原音色的听感。
#       尾部留白到精确 5.000s，响度归一交给渲染引擎统一处理。
#
#   python head_voice.py --episode 002     # 单篇生成 / 刷新
#   python head_voice.py --all            # 283 篇批量（有签名缓存，已生成秒跳过）
#   python head_voice.py --check-voice    # 复核音色是否仍是 v4 原说书人
#
# 产出 episodes/NNN/head_voice.wav，供 v12 引擎 --episode 分支消费。
# 第 001 篇文案与原音轨一致，直接复用原声，不生成新文件。

import argparse
import asyncio
import json
import pathlib
import re
import subprocess
import time

import edge_tts
from ffpath import ffmpeg, ffprobe

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
# 原说书人音色，禁止替换。
# 校验：用本声音合成 ORIGINAL_TEXT，与 episodes/001/v4/talk_ref.mp3 波形相关 1.0000、
# 时长同为 3.696s，即字节级同源；正片旁白也是这个声音，但片头音色是独立保留的，
# 不是「换成旁白音色」。跑 --check-voice 可随时复核。
VOICE = "zh-CN-YunjianNeural"
ORIGINAL_TEXT = "今天讲一个盘古开天辟地的故事。"
TEMPLATE = "今天讲一个{title}的故事。"

# 原片头音轨语音窗口（ffmpeg silencedetect -38dB 实测）
SPEECH_START = 0.173
SPEECH_END = 2.798
HEAD_LEN = 5.0
# 口型视频 2.0s 起收拢、3.6s 才闭合，语音终点留到 2.90s 都不会穿帮。
# 只设这个软上限：篇名短就早收嘴、绝不为填满窗口给声音变速（变速会毁掉原音色听感）。
MAX_SPEECH_END = 2.90
SIGNATURE_VERSION = 3


def ff(args):
    r = subprocess.run([ffmpeg(), "-y", "-hide_banner", "-v", "error", *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 失败：\n" + r.stderr[-2000:])
    return r


def duration(path):
    r = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8")
    return float(r.stdout.strip())


def speech_span(path):
    """找出 TTS 音频里真正说话的起止，裁掉首尾静音。"""
    # 阈值必须与原音轨实测口径一致（-38dB / 0.12s），否则裁掉的尾音长短不同，
    # 对齐后语音终点会提前约 0.15s。
    r = subprocess.run(
        [ffmpeg(), "-hide_banner", "-i", str(path), "-vn",
         "-af", "silencedetect=noise=-38dB:d=0.12", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", r.stderr)]
    total = duration(path)
    # 开头静音：第一个 silence_start 接近 0，对应的 silence_end 就是语音起点
    s0 = ends[0] if (starts and starts[0] < 0.05 and ends) else 0.0
    # 结尾静音：最后一个 silence_start（在语音之后）就是语音终点
    e0 = next((x for x in reversed(starts) if x > s0 + 0.05 and x < total - 0.01), total)
    return s0, e0


def ensure(ep, head_text=None):
    """保证该篇有一份对齐好的片头音轨；返回路径，001 返回 None（复用原声）。"""
    ep = pathlib.Path(ep).resolve()
    if head_text is None:
        meta = json.loads((ep / "meta.json").read_text(encoding="utf-8"))
        head_text = TEMPLATE.format(title=meta["title"])
    text = head_text.strip()
    if text == ORIGINAL_TEXT:
        return None                       # 001：原音轨本来就对，不动

    out = ep / "head_voice.wav"
    stamp = ep / ".head_voice.json"
    sig = {"v": SIGNATURE_VERSION, "text": text, "voice": VOICE}
    if out.exists() and out.stat().st_size > 0 and stamp.exists() \
            and json.loads(stamp.read_text(encoding="utf-8")) == sig:
        return out                        # 缓存命中，不联网、不重算

    raw = ep / "head_voice_raw.mp3"
    # edge-tts 走公网 WebSocket，偶发连接超时；批量 283 篇必须能自动重试。
    for attempt in range(1, 4):
        try:
            asyncio.run(edge_tts.Communicate(text, VOICE).save(str(raw)))
            if raw.exists() and raw.stat().st_size > 0:
                break
        except Exception as exc:
            print(f"  片头合成第 {attempt} 次失败：{type(exc).__name__}，重试…")
            time.sleep(1.5 * attempt)
    else:
        raise RuntimeError(f"edge-tts 连续 3 次失败，无法生成第 {ep.name} 篇片头音轨")
    s0, e0 = speech_span(raw)
    span = max(e0 - s0, 0.1)
    # 关键：默认「不变速」。早先为了把语音硬拉到 2.625s 给 002 做了 0.82x 减速，
    # 同一个声音被拖慢 18% 后听感就变了，主人指出「开头音色还是用原说书人音色」。
    # 口型视频 2.0s 起收拢、3.6s 闭合，语音早结束只是收嘴早一点，并不穿帮。
    # 只有篇名过长、语音会拖过 2.90s 时才轻微加速，且不超过 1.20x。
    max_span = MAX_SPEECH_END - SPEECH_START
    tempo = max(1.0, span / max_span)
    if tempo > 1.20:
        print(f"  提示：篇名偏长需加速 {tempo:.2f}x，已限到 1.20x，建议人工听一遍")
        tempo = 1.20

    # 裁掉首尾静音 →（必要时）轻微加速 → 补回原音轨起点 → 留白到 5.0s。
    # 响度归一不在这里做：交给渲染引擎的 build_audio()，与原音轨走完全相同的一条链，
    # 避免 loudnorm 在链尾改变时长（实测会少 0.13s，语音也会提前结束）。
    # end_sample 按样本数精确裁切（5s×48k=240000），不受 time_base / PTS 偏移影响；
    # adelay 之后重置一次 PTS，保证从 0 起算。
    af = (f"atrim=start={s0:.4f}:end={e0:.4f},asetpts=PTS-STARTPTS,"
          f"atempo={tempo:.6f},aresample=48000,"
          "aformat=channel_layouts=mono,pan=stereo|c0=c0|c1=c0,"
          f"adelay={int(SPEECH_START * 1000)}|{int(SPEECH_START * 1000)},"
          "asetpts=PTS-STARTPTS,apad,"
          f"atrim=end_sample={int(HEAD_LEN * 48000)}")
    ff(["-i", str(raw), "-af", af, "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le", str(out)])
    raw.unlink(missing_ok=True)
    stamp.write_text(json.dumps(sig, ensure_ascii=False, indent=2), encoding="utf-8")
    end = SPEECH_START + span / tempo
    print(f"  片头音轨：{ep.name} 「{text}」 语音 {span:.2f}s / 终点 {end:.2f}s "
          f"/ 变速 {tempo:.2f}x（1.00=原说书人原速）")
    return out


def check_voice():
    """复核当前 VOICE 是否仍等于 v4 原说书人音色（同句同声应波形相关 ≈1.0000）。"""
    import numpy as np

    ref = ROOT / "episodes/001/v4/talk_ref.mp3"
    tmp = HERE / "_voice_check.mp3"

    def pcm(p):
        r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(p), "-vn",
                            "-ar", "24000", "-ac", "1", "-f", "s16le", "-"],
                           capture_output=True)
        return np.frombuffer(r.stdout, dtype="<i2").astype(float)

    for attempt in range(1, 4):
        try:
            asyncio.run(edge_tts.Communicate(ORIGINAL_TEXT, VOICE).save(str(tmp)))
            break
        except Exception as exc:
            print(f"  校验合成第 {attempt} 次失败：{type(exc).__name__}，重试…")
            time.sleep(1.5 * attempt)
    else:
        raise RuntimeError("无法联网校验音色")
    a, b = pcm(ref), pcm(tmp)
    n = min(len(a), len(b))
    c = float(np.corrcoef(a[:n], b[:n])[0, 1])
    tmp.unlink(missing_ok=True)
    print(f"  当前 VOICE={VOICE}")
    print(f"  与 v4/talk_ref.mp3（原说书人）波形相关系数 = {c:.4f}"
          f"｜时长 {len(a)/24000:.3f}s / {len(b)/24000:.3f}s")
    print("  结论：" + ("音色一致，仍是原说书人" if c > 0.99 else "音色已被改动，请检查 VOICE"))
    return c > 0.99


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", help="篇号，如 002")
    ap.add_argument("--all", action="store_true", help="按 manifest 批量生成")
    ap.add_argument("--check-voice", action="store_true", help="复核音色是否仍为 v4 原说书人")
    a = ap.parse_args()

    if a.check_voice:
        raise SystemExit(0 if check_voice() else 1)
    if a.all:
        mp = ROOT / "manifest.json"
        if not mp.exists():
            raise SystemExit("--all 需要 manifest.json（技能包内没有，请逐篇 --episode 生成）")
        manifest = json.loads(mp.read_text(encoding="utf-8"))
        for s in manifest["stories"]:
            ep = ROOT / "episodes" / s["id"]
            if (ep / "meta.json").exists():
                ensure(ep)
        return
    if not a.episode:
        raise SystemExit("需要 --episode NNN 或 --all")
    ensure(ROOT / "episodes" / a.episode)


if __name__ == "__main__":
    main()

