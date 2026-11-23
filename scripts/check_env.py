# 开工前环境自检：确认技能自带 runtime 完整、命令与 Python 依赖就位。
#
#   python check_env.py                 # 离线检查（默认）
#   python check_env.py --check-voice  # 额外联网复核片头音色是否仍是 v4 原说书人
#
# 工作目录由 STORY_ROOT 指定（可选）；本脚本只检查技能包自身与外部命令，不写工作目录。

import argparse
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
RUNTIME = HERE.parent / "runtime"

# 技能自带运行时必需文件，缺一不可
REQUIRED = [
    "series/template.json",
    "series/new_episode.py",
    "series/head_voice.py",
    "series/ffpath.py",                       # ffmpeg/ffprobe 路径解析（不在 PATH 也能开工）
    "series/lens_default.json",               # 景别默认模板（不再依赖 001 样板篇）
    "episodes/001/v12/make_v12.py",
    "episodes/001/meta.json",
    "episodes/001/shots.json",
    "episodes/001/caption.srt",
    "episodes/001/v4/talk_test.mp4",
    "episodes/001/v4/talk_ref.mp3",
    "episodes/001/v4/ref_text.txt",
    "episodes/001/v2/host_rgba.png",
    "episodes/001/v2/bg1.jpg",
    "episodes/001/v2/host_modern_rgba.png",   # 现代说书人遮罩（host=modern）
    "episodes/001/v4/talk_modern.mp4",       # 现代说书人口型视频（host=modern）
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-voice", action="store_true", help="联网复核片头音色")
    a = ap.parse_args()

    print(f"技能目录：{HERE.parent}")
    print(f"运行时：{RUNTIME}")
    root_env = os.environ.get("STORY_ROOT")
    print(f"工作目录 STORY_ROOT：{root_env or '未设置（建篇时默认当前目录）'}")
    if not RUNTIME.is_dir():
        print("  [缺失] runtime/ 目录不存在，技能包不完整")
        raise SystemExit(1)

    ok = True
    for rel in REQUIRED:
        hit = (RUNTIME / rel).exists()
        ok &= hit
        print(f"  [{'OK' if hit else '缺失'}] runtime/{rel}")

    # ffmpeg 常不在 PATH（本机装在 C:\ffmpeg\bin）；用技能自带 ffpath 解析，
    # 引擎与建篇脚本都走同一套解析，所以这里查到路径就等于渲染链路能跑。
    sys.path.insert(0, str(RUNTIME / "series"))
    try:
        import ffpath
        where = ffpath.report()
        for cmd in ("ffmpeg", "ffprobe"):
            hit = bool(where.get(cmd))
            ok &= hit
            print(f"  [{'OK' if hit else '缺失'}] {cmd}"
                  + (f" -> {where[cmd]}" if hit else "（PATH 与常见目录都没找到）"))
            if not hit:
                print("        可设环境变量 FFMPEG_BIN 指向 ffmpeg 目录后重跑")
    except Exception as e:
        ok = False
        print(f"  [缺失] ffpath.py 解析失败：{e}")

    # 交付脚本（技能目录 scripts/）：缺了会走到一半才发现，提前报出来
    for rel in ("scripts/start_story.py", "scripts/fill_shots.py",
                "scripts/split_panels.py", "scripts/run_episode.py",
                "scripts/make_cover.py", "scripts/check_ai_patterns.py",
                "scripts/panel_map.py"):
        hit = (HERE.parent / rel).exists()
        ok &= hit
        print(f"  [{'OK' if hit else '缺失'}] {rel}")

    # 001 只是说书人素材与镜头运动的样板：它的 11 张场景图已随精简包移除，
    # 因此 001 本身不能出片（出片一律用本篇 场景图/），只作参考，不计入缺项。

    for mod in ("edge_tts", "PIL", "numpy"):
        hit = importlib.util.find_spec(mod) is not None
        ok &= hit
        print(f"  [{'OK' if hit else '缺失'}] Python 包 {mod}")

    if a.check_voice:
        print("\n片头音色复核（需联网）：")
        r = subprocess.run(
            [sys.executable, str(RUNTIME / "series/head_voice.py"), "--check-voice"],
            cwd=RUNTIME / "series", capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        print(r.stdout.strip() or r.stderr.strip())
        ok &= (r.returncode == 0)

    print("\n结论：" + ("环境可用，可以开工" if ok else "有缺项，先补齐再开工"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
