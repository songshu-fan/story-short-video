# 一条命令跑完一篇：建篇 -> 填图 -> 渲染 -> 抽帧验收（可选出全片 / 封面）。
#
#   python run_episode.py --id jingwei --title "精卫填海" --lines lines.json \
#       --images-dir "scenes/" --root "D:/workspace" --profile fast
#   python run_episode.py --id jingwei --root "D:/workspace" --full   # 已建过篇，直接出全片
#
# 每一步都调用既有脚本（start_story / fill_shots / make_v12 / make_cover），
# 本脚本只负责串流程、计时和汇报，不改任何渲染规则。

import argparse
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
RUNTIME = SKILL / "runtime"
ENGINE = RUNTIME / "episodes/001/v12/make_v12.py"


def run(name, cmd):
    t0 = time.time()
    print(f"\n=== {name} ===")
    r = subprocess.run([sys.executable, *cmd], text=True,
                       encoding="utf-8", errors="replace")
    dt = time.time() - t0
    print(f"=== {name}：{'完成' if r.returncode == 0 else '失败'} {dt:.1f}s ===")
    if r.returncode != 0:
        raise SystemExit(r.returncode)
    return dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--root", help="工作目录；也可用 STORY_ROOT；默认当前目录")
    # 建篇参数（shots.json 已存在时可省略）
    ap.add_argument("--title")
    ap.add_argument("--volume", default="自定义")
    ap.add_argument("--series", default="中华上下五千年",
                    help="系列名（片头卷名行与片尾落款）")
    ap.add_argument("--host", choices=["ancient", "modern"], default="ancient")
    ap.add_argument("--lines")
    ap.add_argument("--structure")
    ap.add_argument("--needle")
    ap.add_argument("--images-dir")
    ap.add_argument("--images-paths", nargs="*", default=[])
    ap.add_argument("--grids-dir", help="宫格原图目录：先切分成单镜图再填图（两宫格/四宫格省积分）")
    ap.add_argument("--panels", type=int, choices=[2, 4], default=2,
                    help="宫格数：2=两宫格（默认）/ 4=四宫格")
    ap.add_argument("--layout", help="宫格版式，如 1x2（左右）/ 2x1（上下）；默认按图比例判断")
    ap.add_argument("--profile", choices=["fast", "quality"])
    ap.add_argument("--cache", action="store_true")
    ap.add_argument("--encoder", choices=["libx264", "qsv", "auto"])
    ap.add_argument("--ss", type=int)
    # 渲染参数
    ap.add_argument("--test", type=int, default=45, help="测试片秒数，0=不渲测试片")
    ap.add_argument("--no-tail", action="store_true", help="不渲片尾针验收片")
    ap.add_argument("--full", action="store_true", help="渲正式全片（成片/主题名.mp4）")
    ap.add_argument("--cover", action="store_true", help="出三平台封面（需先有全片/场景图）")
    a = ap.parse_args()

    root = pathlib.Path(a.root).resolve() if a.root else None
    ep = (root / "episodes" / a.id) if root else pathlib.Path.cwd() / "episodes" / a.id
    shots = ep / "shots.json"

    timings = []
    # 宫格图先切成单镜图，落到本篇 场景图/，后续填图按普通目录走
    if a.grids_dir:
        cmd = [str(HERE / "split_panels.py"), "--grids-dir", a.grids_dir,
               "--panels", str(a.panels), "--out", str(ep / "场景图")]
        if a.layout:
            cmd += ["--layout", a.layout]
        timings.append(("宫格切分", run("宫格切分", cmd)))
        a.images_dir = str(ep / "场景图")

    if not shots.exists():
        if not (a.title and a.lines):
            raise SystemExit(f"{ep} 还没有 shots.json：首次建篇需要 --title 与 --lines")
        cmd = [str(HERE / "start_story.py"), "--id", a.id, "--title", a.title,
               "--volume", a.volume, "--series", a.series,
               "--host", a.host, "--lines", a.lines]
        if a.structure:
            cmd += ["--structure", a.structure]
        if a.needle:
            cmd += ["--needle", a.needle]
        if root:
            cmd += ["--root", str(root)]
        if a.images_dir:
            cmd += ["--images-dir", a.images_dir]
        if a.images_paths:
            cmd += ["--images-paths", *a.images_paths]
        if a.profile:
            cmd += ["--profile", a.profile]
        if a.cache:
            cmd += ["--cache"]
        if a.encoder:
            cmd += ["--encoder", a.encoder]
        if a.ss:
            cmd += ["--ss", str(a.ss)]
        timings.append(("建篇", run("建篇", cmd)))
    else:
        print(f"已有 {shots}，跳过建篇")
        if a.images_dir or a.images_paths:
            cmd = [str(HERE / "fill_shots.py"), "--id", a.id]
            if root:
                cmd += ["--root", str(root)]
            if a.images_dir:
                cmd += ["--images-dir", a.images_dir]
            elif a.images_paths:
                cmd += ["--images", *a.images_paths]
            timings.append(("填图", run("填图", cmd)))

    flags = []
    if a.cache:
        flags.append("--cache")
    if a.encoder:
        flags += ["--encoder", a.encoder]
    if a.ss:
        flags += ["--ss", str(a.ss)]

    if a.test:
        timings.append((f"测试片 {a.test}s",
                        run(f"测试片 {a.test}s",
                            [str(ENGINE), "--episode", str(shots), "--test", str(a.test), *flags])))
    if not a.no_tail:
        timings.append(("片尾针", run("片尾针", [str(ENGINE), "--episode", str(shots),
                                                "--tail", "8", *flags])))
    if a.full:
        timings.append(("全片", run("全片", [str(ENGINE), "--episode", str(shots), *flags])))
    if a.cover:
        imgs = sorted((ep / "场景图").glob("*"))
        imgs = [p for p in imgs if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
        if not imgs:
            print("[封面] 场景图目录为空，跳过")
        else:
            cmd = [str(HERE / "make_cover.py"), "--episode-dir", str(ep),
                    "--image", str(imgs[len(imgs) // 2])]
            if root:
                cmd += ["--root", str(root)]
            timings.append(("封面", run("封面", cmd)))

    print("\n=== 总耗时 ===")
    for name, dt in timings:
        print(f"  {name}: {dt:.1f}s")
    print(f"  合计 {sum(dt for _, dt in timings):.1f}s")
    print(f"  本篇目录：{ep}")


if __name__ == "__main__":
    main()
