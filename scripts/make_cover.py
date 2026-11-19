# -*- coding: utf-8 -*-
"""make_cover.py —— 按平台生成视频封面（零积分，PIL 本地合成）。

从本篇场景图取一张做底，按平台画幅裁切、压暗渐变、叠「系列·卷名 kicker +
大标题 + 金句副题」，一次产出抖音 / 小红书 / B 站三张封面 PNG。

平台画幅与安全区：
- douyin    1080x1920 (9:16)  竖版全屏；文字左对齐，避开右侧点赞栏(右140px)与底部UI(下470px)
- xiaohongshu 1080x1440 (3:4) 信息流封面；文字居上，避开底部160px
- bilibili  1920x1080 (16:9)  横版；文字居左上，避开右下角时长角标区

用法：
  python scripts/make_cover.py --root <项目根> --episode-dir episodes/139 \
      --image episodes/139/场景图/成片图/shot_09.jpg \
      [--platform all|douyin|xiaohongshu|bilibili] [--out <目录>]
  # --title/--kicker/--subtitle 可覆盖默认（默认取 meta.json 与 script.json）

不传 --image 时可用 --image-index N 取 shots.json.images[N]。
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
import numpy as np

# ---- 字体（Windows 自带；缺了报错提示） ----
FONT_TITLE_CANDIDATES = ["C:/Windows/Fonts/STZHONGS.TTF", "C:/Windows/Fonts/simsun.ttc",
                         "C:/Windows/Fonts/msyhbd.ttc"]
FONT_BODY_CANDIDATES = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
                        "C:/Windows/Fonts/simhei.ttf"]

GOLD = (227, 197, 139)          # kicker / 金线
WHITE = (255, 255, 255)
SUB_WHITE = (232, 228, 220)

PLATFORMS = {
    "douyin":      (1080, 1920),
    "xiaohongshu": (1080, 1440),
    "bilibili":    (1920, 1080),
}
OUT_NAMES = {
    "douyin":      "cover_douyin_9x16.png",
    "xiaohongshu": "cover_xhs_3x4.png",
    "bilibili":    "cover_bili_16x9.png",
}


def pick_font(candidates, size):
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    raise SystemExit("找不到可用中文字体，请检查 C:/Windows/Fonts（需华文中宋/微软雅黑之一）")


def wrap_cjk(text, font, max_w):
    """中文逐字折行；标点悬挂不做，够用。"""
    lines, cur = [], ""
    for ch in text:
        t = cur + ch
        if font.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def fit_title(text, max_w, max_lines, size_start, size_min, candidates):
    """从大到小找能放进 max_lines 行的字号。
    若单行放得下且字号 ≥ 0.6×起始字号，优先单行（避免「刘备三顾/茅庐」这类断法）。"""
    one_line_size = None
    multi = None
    size = size_start
    while size >= size_min:
        font = pick_font(candidates, size)
        lines = wrap_cjk(text, font, max_w)
        if all(font.getlength(l) <= max_w for l in lines):
            if len(lines) == 1 and one_line_size is None:
                one_line_size = size
            if len(lines) <= max_lines and multi is None:
                multi = (size, lines)
            if one_line_size and multi:
                break
        size -= 6
    if one_line_size and one_line_size >= size_start * 0.6:
        return pick_font(candidates, one_line_size), [text]
    if multi:
        return pick_font(candidates, multi[0]), multi[1]
    font = pick_font(candidates, size_min)
    return font, wrap_cjk(text, font, max_w)[:max_lines]


def fit_subtitle(text, max_w, max_lines, size, candidates):
    """副题：去掉句尾句号；折行后末行 ≤2 字视为孤字，缩一号重排。"""
    text = text.rstrip("。！？；")
    while size > 24:
        font = pick_font(candidates, size)
        lines = wrap_cjk(text, font, max_w)
        if len(lines) <= max_lines and (len(lines) == 1 or len(lines[-1]) > 2):
            return font, lines
        size -= 6
    font = pick_font(candidates, size)
    return font, wrap_cjk(text, font, max_w)[:max_lines]


def draw_text_shadow(layer, xy, text, font, fill, shadow_alpha=170, shadow_off=5,
                     stroke=0, stroke_fill=None, anchor="la"):
    """带投影的文字；layer 为 RGBA。"""
    d = ImageDraw.Draw(layer)
    x, y = xy
    d.text((x + shadow_off, y + shadow_off), text, font=font,
           fill=(0, 0, 0, shadow_alpha), anchor=anchor)
    d.text((x, y), text, font=font, fill=fill + (255,) if len(fill) == 3 else fill,
           stroke_width=stroke, stroke_fill=stroke_fill, anchor=anchor)


def gradient_alpha(w, h, top_a, bottom_a):
    """垂直黑白渐变 alpha（0=顶）。numpy 生成，快且无带状。"""
    ramp = np.linspace(top_a, bottom_a, h, dtype=np.float32)
    return np.repeat(ramp[:, None], w, axis=1)


def build_cover(img, w, h, kicker, title, subtitle, layout):
    """img:PIL Image → 封面 RGBA。layout 为该平台的文字锚点参数。"""
    base = ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS,
                        centering=layout.get("centering", (0.5, 0.42)))
    base = ImageEnhance.Color(base).enhance(0.92)          # 轻微降饱和
    base = ImageEnhance.Brightness(base).enhance(0.86)     # 整体压暗

    # 渐变压暗：标题区 + 底部（方向随平台）
    arr = np.asarray(base).astype(np.float32)
    g = gradient_alpha(w, h, layout["grad_top"], layout["grad_bottom"])
    if layout.get("grad_horiz"):                           # 横版：左侧压暗
        gx = np.linspace(layout["grad_top"], layout["grad_bottom"], w, dtype=np.float32)
        g = np.repeat(gx[None, :], h, axis=0)
    arr = arr * (1 - (g[..., None] / 255.0) * 0.62)        # 渐变最深处压到 62%
    out = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mx = layout["margin_x"]
    max_tw = w - mx - layout["safe_right"]

    y = layout["kicker_y"]
    kf = pick_font(FONT_BODY_CANDIDATES, layout["kicker_size"])
    # kicker 字距：逐字绘制
    kx = mx
    kdraw = ImageDraw.Draw(layer)
    for ch in kicker:
        kdraw.text((kx + 3, y + 3), ch, font=kf, fill=(0, 0, 0, 150))
        kdraw.text((kx, y), ch, font=kf, fill=GOLD + (255,))
        kx += kf.getlength(ch) + layout["kicker_track"]
    y += layout["kicker_size"] + 26

    # 金色短线
    ImageDraw.Draw(layer).rectangle([mx, y, mx + 64, y + 4], fill=GOLD + (230,))
    y += 34

    tf, tlines = fit_title(title, max_tw, layout["title_max_lines"],
                           layout["title_size"], layout["title_size_min"],
                           FONT_TITLE_CANDIDATES)
    lh = int(tf.size * 1.22)
    for ln in tlines:
        draw_text_shadow(layer, (mx, y), ln, tf, WHITE, shadow_alpha=190,
                         shadow_off=6, stroke=2, stroke_fill=(0, 0, 0, 110))
        y += lh
    y += 30

    if subtitle:
        sf, slines = fit_subtitle(subtitle, max_tw, 2, layout["sub_size"],
                                  FONT_BODY_CANDIDATES)
        for ln in slines:
            draw_text_shadow(layer, (mx, y), ln, sf, SUB_WHITE, shadow_alpha=140,
                             shadow_off=3)
            y += int(sf.size * 1.35)

    out = Image.alpha_composite(out, layer)
    return out.convert("RGB")


LAYOUTS = {
    "douyin": dict(
        margin_x=96, safe_right=150,          # 避开右侧点赞评论栏
        kicker_y=190, kicker_size=40, kicker_track=6,
        title_size=172, title_size_min=110, title_max_lines=3,
        sub_size=44,
        grad_top=150, grad_bottom=70, centering=(0.5, 0.40),
    ),
    "xiaohongshu": dict(
        margin_x=88, safe_right=88,
        kicker_y=150, kicker_size=38, kicker_track=6,
        title_size=148, title_size_min=100, title_max_lines=3,
        sub_size=42,
        grad_top=140, grad_bottom=60, centering=(0.5, 0.42),
    ),
    "bilibili": dict(
        margin_x=120, safe_right=760,          # 文字只占左半，右下角标不碍事
        kicker_y=300, kicker_size=40, kicker_track=6,
        title_size=150, title_size_min=96, title_max_lines=2,
        sub_size=42,
        grad_top=130, grad_bottom=40, grad_horiz=True, centering=(0.5, 0.45),
    ),
}


def main():
    ap = argparse.ArgumentParser(description="生成平台封面")
    ap.add_argument("--root", default=None, help="项目根（缺省用当前目录/STORY_ROOT）")
    ap.add_argument("--episode-dir", required=True, help="如 episodes/139")
    ap.add_argument("--image", default=None, help="底图路径；不传则用 --image-index")
    ap.add_argument("--image-index", type=int, default=None, help="取 shots.json.images[N]")
    ap.add_argument("--title", default=None)
    ap.add_argument("--kicker", default=None)
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--platform", default="all",
                    choices=["all", "douyin", "xiaohongshu", "bilibili"])
    ap.add_argument("--out", default=None, help="输出目录（缺省 <episode-dir>/封面）")
    a = ap.parse_args()

    root = Path(a.root) if a.root else Path(__import__("os").environ.get("STORY_ROOT", "."))
    ep = (root / a.episode_dir) if not Path(a.episode_dir).is_absolute() else Path(a.episode_dir)
    meta = json.loads((ep / "meta.json").read_text(encoding="utf-8"))
    title = a.title or meta.get("title", "未命名")
    kicker = a.kicker
    if not kicker:
        vol = meta.get("volume", "")
        series = meta.get("series") or "中华上下五千年"
        kicker = f"{series} · {vol}" if vol else series
    subtitle = a.subtitle
    if subtitle is None:
        sp = ep / "script.json"
        if sp.exists():
            subtitle = json.loads(sp.read_text(encoding="utf-8")).get("needle", "")
        subtitle = subtitle or ""

    img_path = a.image
    if not img_path:
        shots = json.loads((ep / "shots.json").read_text(encoding="utf-8"))
        if a.image_index is None:
            raise SystemExit("请给 --image 或 --image-index")
        img_path = shots["images"][a.image_index]
    img_path = Path(img_path)
    if not img_path.is_absolute():
        # 路径口径：shots.json 的 images 是相对「本篇目录」记的（paths_relative_to=episode），
        # 老用法/手写 --image 可能相对项目根。先试本篇目录，不存在再退回项目根。
        if (ep / img_path).exists():
            img_path = ep / img_path
        else:
            img_path = root / img_path
    if not img_path.exists():
        raise SystemExit(f"底图不存在：{img_path}")

    out_dir = Path(a.out) if a.out else ep / "封面"
    out_dir.mkdir(parents=True, exist_ok=True)
    plats = list(PLATFORMS) if a.platform == "all" else [a.platform]
    for p in plats:
        w, h = PLATFORMS[p]
        cover = build_cover(Image.open(img_path), w, h, kicker, title, subtitle, LAYOUTS[p])
        dest = out_dir / OUT_NAMES[p]
        cover.save(dest, "PNG")
        print(f"[cover] {p:11s} {w}x{h}  <- {img_path.name}  -> {dest}")


if __name__ == "__main__":
    sys.exit(main())
