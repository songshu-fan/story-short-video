# 宫格图切分：把一张 ImageGen 宫格图（两宫格 / 四宫格）切成逐镜场景图。
#
#   python split_panels.py --grids-dir "原图/" --out "episodes/jingwei/场景图" --panels 2
#   python split_panels.py --images g1.png g2.png --panels 4 --out "场景图/"
#   python split_panels.py --grids-dir "原图/" --panels 2 --dry-run     # 只看切分结果，不写盘
#
# 为什么有这个脚本：
#   一个镜头一图要逐张出图，两宫格一次出 2 镜、四宫格一次出 4 镜，省一半以上积分，
#   但宫格图每格不是 16:9，且有时会留白边/串图，必须按「格」单独裁切并放大。
#   本脚本把宫格图拆成 1920x1080 的单镜图，落到场景图目录，随后照常跑 fill_shots.py，
#   引擎不需要任何改动（它看到的仍是普通单镜图）。
#
# 版式：
#   --panels 2  两宫格：图比宽则上下叠（1x2），图比高则左右并排（2x1），也可 --layout 强制
#   --panels 4  四宫格：固定 2x2，阅读顺序 左上→右上→左下→右下
#   切出的文件名 <原名>_p01.jpg … 按阅读顺序，fill_shots 按文件名排序正好对上镜号。

import argparse
import pathlib
import sys

EXTS = (".jpg", ".jpeg", ".png", ".webp")
WHITE = 245          # 任一通道 < 245 视为非白（去白边）
TARGET = (1920, 1080)


def cells(rows, cols):
    """返回阅读顺序的 (r, c) 列表：先左后右、先上后下。"""
    return [(r, c) for r in range(rows) for c in range(cols)]


def content_box(cell, pad=0.0):
    """格子内容真实外接框：检测非白像素，返回 (l, t, r, b)；找不到内容返回整格。"""
    import numpy as np
    arr = np.asarray(cell.convert("RGB"), dtype=np.uint8)
    mask = (arr < WHITE).any(axis=2)
    if not mask.any():
        return (0, 0, cell.width, cell.height)
    ys, xs = np.where(mask)
    l, r = int(xs.min()), int(xs.max()) + 1
    t, b = int(ys.min()), int(ys.max()) + 1
    # 只防退化（几乎没内容），不因「内容接近满格」放弃裁白边：
    # 满幅图的外接框本来就等于整格，行为不变；带白边的格子则必须按外接框裁。
    if (r - l) < cell.width * 0.1 or (b - t) < cell.height * 0.1:
        return (0, 0, cell.width, cell.height)
    if pad:
        dx, dy = int(cell.width * pad), int(cell.height * pad)
        l, t = max(0, l - dx), max(0, t - dy)
        r, b = min(cell.width, r + dx), min(cell.height, b + dy)
    return (l, t, r, b)


def to_16x9(box, cell, anchor="top"):
    """把内容框裁成 16:9（默认贴顶，与既有单图流程一致），尽量保留整幅宽度。"""
    l, t, r, b = box
    w, h = r - l, b - t
    want_h = w * 9 / 16
    if want_h <= h:                       # 内容够高：宽度不缩，纵向取 16:9
        h2 = int(round(want_h))
        if anchor == "center":
            t2 = t + (h - h2) // 2
        else:
            t2 = t
        return (l, t2, r, t2 + h2)
    want_w = h * 16 / 9                   # 内容偏矮：高度不缩，横向补足或居中
    w2 = int(round(want_w))
    if w2 <= cell.width:
        l2 = l + (w - w2) // 2
        return (l2, t, l2 + w2, b)
    return (0, t, cell.width, t + int(round(cell.width * 9 / 16)))


def split_one(path, layout, out_dir, quality, pad, anchor, dry):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    W, H = im.size
    rows, cols = layout
    cw, ch = W // cols, H // rows
    made = []
    for k, (r, c) in enumerate(cells(rows, cols), 1):
        cell = im.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
        box = content_box(cell, pad)
        box = to_16x9(box, cell, anchor)
        panel = cell.crop(box).resize(TARGET, Image.LANCZOS)
        dst = out_dir / f"{path.stem}_p{k:02d}.jpg"
        cover = (box[2] - box[0]) * (box[3] - box[1]) / float(cell.width * cell.height)
        note = "" if cover >= 0.55 else f"  [注意] 内容仅占格 {cover:.0%}，可能有白边，请抽帧核对"
        if not dry:
            out_dir.mkdir(parents=True, exist_ok=True)
            panel.save(dst, quality=quality, subsampling=2)
        print(f"  {path.name} 格{k} -> {dst.name}  裁切 {box[2]-box[0]}x{box[3]-box[1]} -> {TARGET[0]}x{TARGET[1]}"
              f"（内容占比 {cover:.0%}）{note}")
        made.append(dst)
    return made


def pick_layout(path, panels, layout_arg):
    from PIL import Image
    if layout_arg:
        r, c = (int(x) for x in layout_arg.lower().split("x"))
        return (r, c)
    if panels == 4:
        return (2, 2)
    im = Image.open(path)
    ratio = im.width / im.height
    if ratio >= 1.2:                 # 宽图：左右并排（两宫格 2x1）
        return (1, 2)
    if ratio <= 0.85:                # 竖图：上下叠（两宫格 1x2）
        return (2, 1)
    raise SystemExit(f"{path.name} 是接近方形的图，无法自动判断两宫格版式；"
                     "请用 --layout 2x1 或 --layout 1x2 指定")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grids-dir", help="宫格图目录，按文件名排序")
    ap.add_argument("--images", nargs="*", default=[], help="宫格图路径列表")
    ap.add_argument("--out", required=False,
                    help="切出的单镜场景图目录；默认与本篇 场景图/ 同级（--grids-dir/../场景图）")
    ap.add_argument("--panels", type=int, choices=[2, 4], default=2,
                    help="宫格数：2=两宫格（默认）/ 4=四宫格")
    ap.add_argument("--layout", help="显式版式，如 2x1（左右两格）/ 1x2（上下两格）/ 2x2")
    ap.add_argument("--quality", type=int, default=92, help="JPEG 质量，默认 92")
    ap.add_argument("--pad", type=float, default=0.0, help="内容框外扩比例，默认 0")
    ap.add_argument("--anchor", choices=["top", "center"], default="top",
                    help="16:9 纵向锚点：top=贴顶（默认，与单图流程一致）/ center=居中")
    ap.add_argument("--dry-run", action="store_true", help="只打印切分信息，不写文件")
    a = ap.parse_args()

    if a.grids_dir:
        d = pathlib.Path(a.grids_dir)
        if not d.is_dir():
            raise SystemExit(f"宫格图目录不存在：{d}")
        grids = sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS)
        out_dir = pathlib.Path(a.out) if a.out else d.parent / "场景图"
    elif a.images:
        grids = [pathlib.Path(p) for p in a.images]
        out_dir = pathlib.Path(a.out) if a.out else pathlib.Path.cwd() / "场景图"
    else:
        raise SystemExit("给 --grids-dir 或 --images 指定宫格图")

    if not grids:
        raise SystemExit("没有找到宫格图（支持 jpg/jpeg/png/webp）")
    missing = [str(p) for p in grids if not p.exists()]
    if missing:
        raise SystemExit("宫格图不存在：" + "、".join(missing))

    print(f"宫格图 {len(grids)} 张，按 {a.panels} 宫格切分 -> {out_dir}"
          + ("（dry-run，不写盘）" if a.dry_run else ""))
    total = 0
    for g in grids:
        layout = pick_layout(g, a.panels, a.layout)
        print(f"[{g.name}] 版式 {layout[0]}x{layout[1]}")
        total += len(split_one(g, layout, out_dir, a.quality, a.pad, a.anchor, a.dry_run))

    print(f"\n共切出 {total} 张单镜场景图" + ("（dry-run 未写盘）" if a.dry_run else ""))
    print("下一步：")
    print(f'  python "{pathlib.Path(__file__).resolve().parent.as_posix()}/fill_shots.py" '
          f'--id <本篇id> --images-dir "{out_dir.as_posix()}" --root "<工作目录>"')
    print("  若镜数多于切出的张数，fill_shots 会循环复用；不够就再补宫格图重切。")


if __name__ == "__main__":
    main()
