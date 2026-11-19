# ffmpeg / ffprobe 定位：不依赖用户 shell 的 PATH。
#
# 背景：引擎与建篇脚本原先直接调 "ffmpeg"，而 Windows 上 ffmpeg 常装在
# C:\ffmpeg\bin 却不在 PATH 里，于是 check_env 报缺失、渲染在中途炸掉。
# 这里统一解析：环境变量 FFMPEG_BIN / FFMPEG_PATH / FFPROBE_PATH 优先，
# 其次 PATH，再其次常见安装位置；都没有时给出可照做的安装提示。
#
# 用法：
#   from ffpath import ffmpeg, ffprobe
#   subprocess.run([ffmpeg(), "-i", ...])

import os
import pathlib
import shutil

_EXE = ".exe" if os.name == "nt" else ""

# 常见安装位置（按优先级）。只用于兜底，不写死某一个用户的目录。
_CANDIDATE_DIRS = [
    os.environ.get("FFMPEG_BIN", ""),
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
    os.path.expanduser(r"~\scoop\shims"),
    os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Links"),
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
]

_cache = {}


def _resolve(name):
    if name in _cache:
        return _cache[name]
    env_key = "FFMPEG_PATH" if name == "ffmpeg" else "FFPROBE_PATH"
    env = os.environ.get(env_key)
    if env and pathlib.Path(env).is_file():
        _cache[name] = env
        return env
    hit = shutil.which(name)
    if hit:
        _cache[name] = hit
        return hit
    for d in _CANDIDATE_DIRS:
        if not d:
            continue
        p = pathlib.Path(d) / f"{name}{_EXE}"
        if p.is_file():
            _cache[name] = str(p)
            return str(p)
    raise RuntimeError(
        f"找不到 {name}。请安装 ffmpeg 并让它出现在 PATH 里，"
        f"或设置环境变量 FFMPEG_BIN 指向 ffmpeg 所在目录"
        f"（当前常见位置已尝试：{[d for d in _CANDIDATE_DIRS if d]}）")


def ffmpeg():
    return _resolve("ffmpeg")


def ffprobe():
    return _resolve("ffprobe")


def report():
    """供 check_env 打印实际使用的路径；缺失时返回 (None, 错误文本)。"""
    out = {}
    for name, fn in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        try:
            out[name] = fn()
        except RuntimeError as e:
            out[name] = None
            out[name + "_error"] = str(e)
    return out


if __name__ == "__main__":
    for k, v in report().items():
        print(f"{k}: {v}")
