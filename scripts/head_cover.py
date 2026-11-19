# 片头/封面一图两用：把一张 16:9 横图拆成 片头底图 + 2:3 竖版封面。
#
#   python head_cover.py --image "episodes/<id>/原图/片头图_横.png" --episode-dir "episodes/<id>"
#
# 为什么：
#   片头底图若借用某张场景图，一定会和正片某一镜重复（同一张图既在片头又在某镜）。
#   所以每篇单独出**一张 16:9 横图**：人物 + 场景同图，横图直接做片头，
#   再从这张横图纵向裁一条 2:3 做三平台封面，一次出图两处复用。
#
# 出图约定（提示词照这个写）：
#   2048x1152 / 16:9；人物中远景、全身或大半身（高约占画面 55%~70%，别贴边、别只给半身大头），
#   水平位置偏左三分之一；右侧三分之一留给说书人（引擎贴右侧合成）；
#   顶部留片名位，底部留甲板/地面暗部（片头去水印与封面裁切都用得上）。

import argparse
import pathlib

COVER_RATIO = 2 / 3          # 封面 1024x1536 = 2:3
HEAD_SIZE = (1920, 1080)
COVER_SIZE = (1024, 1536)


def crop_16x9_dewatermark(im, trim_bottom):
    """取 16:9：按 (1 - trim_bottom) 的高度重算宽度，切掉底部水印带（只裁不拉伸）。"""
    w, h = im.size
    h2 = max(1, int(round(h * (1 - trim_bottom))))
    w2 = max(1, int(round(h2 * 16 / 9)))
    if w2 > w:                                  # 源图偏窄：以宽度为准，改为裁高度
        w2 = w
        h2 = max(1, int(round(w * 9 / 16)))
    x = (w - w2) // 2
    return im.crop((x, 0, x + w2, h2))


def vertical_slice(im, cover_x):
    """纵裁 2:3：满高、宽 = 高×2/3，锚点 cover_x（0=贴左，0.5=居中，1=贴右）。"""
    w, h = im.size
    cw = int(round(h * COVER_RATIO))
    if cw >= w:                                 # 源图太窄：满宽，再按宽度算高度
        cw = w
        ch = int(round(w / COVER_RATIO))
        y0 = max(0, int(round((h - ch) * 0.25)))
        return im.crop((0, y0, w, y0 + ch))
    x0 = int(round((w - cw) * max(0.0, min(1.0, cover_x))))
    return im.crop((x0, 0, x0 + cw, h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="本篇专属 16:9 横图（jpg/png/webp）")
    ap.add_argument("--episode-dir", required=True, help="本篇目录，产物写进 <本篇>/片头图/")
    ap.add_argument("--out", default=None, help="输出目录，缺省 <episode-dir>/片头图")
    ap.add_argument("--cover-x", type=float, default=0.30,
                    help="封面纵裁锚点：0 贴左 / 0.5 居中 / 1 贴右，默认 0.30（人物在左三分之一时用）")
    ap.add_argument("--trim-bottom", type=float, default=0.06,
                    help="片头图裁掉的底部比例（去平台水印），默认 0.06；水印已不可见可设 0")
    ap.add_argument("--quality", type=int, default=92)
    a = ap.parse_args()

    from PIL import Image
    src = pathlib.Path(a.image)
    if not src.is_file():
        raise SystemExit(f"横图不存在：{src}")

    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.episode_dir) / "片头图"
    out.mkdir(parents=True, exist_ok=True)

    im = Image.open(src).convert("RGB")
    W, H = im.size
    if W / H < 1.4:
        raise SystemExit(f"这张图是 {W}x{H}，不是横图；片头图请出 16:9（如 2048x1152）")

    head = crop_16x9_dewatermark(im, a.trim_bottom).resize(HEAD_SIZE, Image.LANCZOS)
    head_path = out / "head_16x9.jpg"
    head.save(head_path, quality=a.quality, subsampling=2)

    cover = vertical_slice(im, a.cover_x).resize(COVER_SIZE, Image.LANCZOS)
    cover_path = out / "cover_2x3.jpg"
    cover.save(cover_path, quality=a.quality, subsampling=2)

    print(f"横图 {src.name} {W}x{H}")
    print(f"  片头底图 -> {head_path.name}  {HEAD_SIZE[0]}x{HEAD_SIZE[1]}（底部裁 {a.trim_bottom:.0%} 去水印）")
    print(f"  封面竖图 -> {cover_path.name}  {COVER_SIZE[0]}x{COVER_SIZE[1]}（纵裁锚点 {a.cover_x:.2f}）")
    print("\n下一步：")
    print('  1) shots.json 的 head_image 写 "片头图/head_16x9.jpg"，再重跑 write_director_doc 刷新导演稿')
    print("  2) make_cover.py --image 指 cover_2x3.jpg 出抖音/小红书；B 站 16:9 用 head_16x9.jpg")


if __name__ == "__main__":
    main()
