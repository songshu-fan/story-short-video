# 古风氛围底床合成（零积分、自包含，不依赖任何外部音频素材）。
# 中国五声音阶：宫商角徵羽；埙用正弦+泛音+颤音近似，编钟用非谐波泛音+长衰减近似。
# 生成的是「垫底氛围音」而非成曲，音量会被引擎压到旁白之下并做侧链闪避。
# 若用户提供了真实 BGM 音频，可绕过本模块直接混音。

import math
import wave

import numpy as np

SR = 48000                     # 与引擎音频链一致
PENTA = (0, 2, 4, 7, 9)        # 五声音阶相对半音：宫 商 角 徵 羽


def generate(path, duration, seed=7, root=220.0, theme="神话"):
    """生成一段 duration 秒的古风氛围音，写成 48kHz 立体声 WAV 到 path。
    seed 决定旋律随机序列（同 seed 可复现）；root 为基频（A3=220）。
    theme 仅做轻微调式/音色权重调整，不改整体结构。"""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    if n <= 0:
        return
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float64)

    # 1) 低频铺底：根音 + 五度，缓慢呼吸，轻微失谐做出立体声宽度。
    drone_amp = 0.18 if theme in ("神话", "史诗") else 0.14
    for mult, amp in ((0.5, drone_amp), (0.75, drone_amp * 0.6)):
        lfo = 0.65 + 0.35 * np.sin(2 * np.pi * t / 11.0 + mult)
        out += amp * lfo * np.sin(2 * np.pi * root * mult * t)
    out += 0.05 * np.sin(2 * np.pi * root * 0.5 * 1.003 * t + 0.7)

    # 2) 埙旋律：每隔几秒走一个五声音阶音，长音 + 颤音 + 泛音。
    pos = 2.0
    while pos < duration - 1.0:
        deg = int(rng.integers(0, 5))
        octave = int(rng.choice((0, 0, 0, 12)))
        freq = root * 2 ** ((PENTA[deg] + octave) / 12)
        span = min(float(rng.uniform(2.2, 3.8)), duration - pos)
        i0 = int(pos * SR)
        i1 = min(n, i0 + int(span * SR))
        if i1 <= i0:
            break
        lt = np.arange(i1 - i0) / SR
        env = np.minimum(1.0, lt / 0.35) * np.exp(-lt / 1.6)
        vib = 1 + 0.006 * np.sin(2 * np.pi * 4.2 * lt)
        wav = (np.sin(2 * np.pi * freq * lt * vib)
               + 0.28 * np.sin(2 * np.pi * 2 * freq * lt * vib)
               + 0.10 * np.sin(2 * np.pi * 3 * freq * lt * vib))
        out[i0:i1] += 0.12 * env * wav
        pos += float(rng.uniform(2.4, 3.6))

    # 3) 编钟点缀：非谐波泛音 + 指数长衰减，间隔较长。
    pos = 4.0
    while pos < duration - 1.0:
        freq = root * 2 ** (int(rng.choice((0, 7, 12))) / 12)
        span = min(4.0, duration - pos)
        i0 = int(pos * SR)
        i1 = min(n, i0 + int(span * SR))
        if i1 <= i0:
            break
        lt = np.arange(i1 - i0) / SR
        env = np.exp(-lt / 1.1)
        wav = (np.sin(2 * np.pi * freq * lt)
               + 0.50 * np.sin(2 * np.pi * 2.76 * freq * lt)
               + 0.30 * np.sin(2 * np.pi * 5.40 * freq * lt)
               + 0.20 * np.sin(2 * np.pi * 8.90 * freq * lt))
        out[i0:i1] += 0.09 * env * wav
        pos += float(rng.uniform(7.0, 12.0))

    # 4) 首尾淡入淡出 + 三段短延迟近似混响。
    fade = min(int(1.5 * SR), max(1, n // 4))
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    rev = np.zeros_like(out)
    for delay, gain in ((0.13, 0.25), (0.29, 0.16), (0.47, 0.09)):
        k = int(delay * SR)
        if k < n:
            rev[k:] += gain * out[:-k]
    out += rev

    peak = float(np.max(np.abs(out))) if n else 0.0
    if peak > 0:
        out = out / peak * 0.9
    # 双声道：右声道轻微延迟与相位差，制造空间感。
    left = out
    k = int(0.011 * SR)
    right = np.zeros_like(out)
    right[k:] = out[:-k] * 0.98
    stereo = np.stack((left, right), axis=1)
    pcm = np.clip(stereo * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
