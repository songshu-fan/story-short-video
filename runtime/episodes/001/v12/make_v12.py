# v12：回应「巨人出镜太多 + 图片灵动效果不满意，再换种效果」。
# 不修改 v1~v11 的任何文件。零积分、纯本地生成。
#
# 两处改动：
# 1. 巨人退回 2 次露面：撤掉 v11 新增的句7 / 句13 动态镜，恢复 v10 的素材分发
#    （句7 换回 shot_06 顶天立地静图，句13 换回 bg2 石斧定格）。
#    全片只剩句5 挥斧、句10 压暗云雾两个动态镜，且 motion 只需要 split=2。
#
# 2. 换一种「灵动」做法。v8 的 displace 是像素级位移，人眼几乎看不出；
#    试过「柔和光雾平移/呼吸」，量化帧差只有 1.0x，同样是滤镜感。
#    现在只保留一种画面运动：平滑的上下浮动。
#      a) 浮尘流光 —— 本地生成一张柔光点（径向渐变+模糊），split 成 16 路，
#         每路用 overlay 的 y 时间表达式按各自速度上升、出屏循环，
#         是 16 个逐帧真实位移的光点，x 固定不左右摆动。
#      b) 画面主体 —— zoompan 的 zoom 固定、x 固定，y 只做线性方向性平移；
#         开镜缩放脉冲、叠加微抖、逐帧胶片颗粒全部去掉，避免看起来还在震。
#         平移改在 3x 超采样分辨率上做：zoompan 会把 y 取整到源像素，
#         1920x1080 上慢速平移每帧不足 1 像素，会变成「停十几帧跳一格」的阶梯；
#         3x 源域每帧 >=1 像素，缩回 1080p 后是连续亚像素位移，慢且顺滑。
#
#   镜头运动、转场、字幕、片头、音频链路全部沿用 v9/v10。
#
#   python make_v12.py --test 45   只看前 45 秒（未定版前用这个）
#   python make_v12.py --sec 30    出 30 秒正式短版
#   python make_v12.py             出完整片

import hashlib
import json
import pathlib
import random
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).parent
V1 = ROOT.parent
V2 = V1 / "v2"
V4 = V1 / "v4"
RUNTIME = ROOT.parent.parent.parent          # runtime/
SERIES = RUNTIME / "series"
ASSETS = RUNTIME / "assets"                  # 精简包不含该目录；保留仅为兼容旧路径
sys.path.insert(0, str(SERIES))
from ffpath import ffmpeg, ffprobe            # noqa: E402  （不依赖 shell 的 PATH）

W, H, FPS = 1920, 1080, 25
HOST_W = 766
HOST_X = W - HOST_W
# 画幅规格：16:9 横版（默认）/ 9:16 竖版。竖版默认不出现说书人（片头自动走 title 卡），
# 因此竖版 host_w=0；字幕字号与安全边距也不同（竖屏要避开底部交互区与右侧点赞栏）。
FORMAT_SPECS = {
    "16:9": dict(w=1920, h=1080, host_w=766,
                 vol=34, title=66, body=54,
                 vol_lr=140, vol_mv=72, title_lr=140, title_mv=128,
                 body_lr=220, body_mv=115, needle=62, credit=28, end_lr=120, wrap=23),
    "9:16": dict(w=1080, h=1920, host_w=0,
                 vol=28, title=56, body=48,
                 vol_lr=54, vol_mv=200, title_lr=54, title_mv=300,
                 body_lr=54, body_mv=300, needle=52, credit=26, end_lr=48, wrap=18),
}
FORMAT = "16:9"
HEAD_LEN = 5.0
HEAD_MODE = "host"                    # host=说书人 5 秒口播（默认）；title=只有标题卡，无人像
HEAD_VOICE_ON = True                  # 片头是否有口播与字幕（title 模式可由 head.voice 关闭）
HEAD_SUB_S = 0.30                     # 片头口播字幕起止（host 沿用旧值；title+口播按语音实测收紧）
HEAD_SUB_E = 3.80
JOIN_XF = 0.8
EXIT_AT = HEAD_LEN - JOIN_XF          # 4.2：正片在成片时间轴上的起点
BODY_SHIFT = HEAD_LEN - EXIT_AT        # 0.8：句子起点换算到正片内部时间
X_FADE = 0.22

# 重音句（0 起算）：句3 帝江之死 / 句6 天地分开 / 句13 收束判词
ACCENTS = {2, 5, 12}

# 画面运动幅度系数：1.0=原速；0.5=半速。只缩放 y 的垂直平移幅度。
# 画面只做上下浮动：zoom 固定、x 固定，无推近脉冲、无叠加微抖、无逐帧颗粒。
# 不改镜头数/字幕/切镜点；片头说书人与片尾针不在此链。
MOTION_SCALE = 0.75
# 超采样倍数：zoompan 的 y 按整数像素取裁剪窗，慢速下 1080p 每帧位移不足 1px
# 会卡顿。先把静帧放大 3x 作为**源域**（每帧位移 >=1px），再让 zoompan 直接输出
# 1920x1080——量化粒度仍是 1 源像素 = 1/3 输出像素，与旧做法（zoompan 输出 5760
# 再 lanczos 缩回）完全一致，但每帧少算 18.7MP，实测单镜滤镜从 11 fps 提到 88 fps。
SUPERSAMPLE = 3
# True：zoompan 直接输出 W×H（推荐）；False：回退旧行为（输出 W*SS×H*SS 再缩放）。
ZOOM_OUT_SAME_RES = True
# 浮尘光点图层在 960x540 合成后放大（省 16 次全帧叠加）；False 回退全帧叠加。
SPARK_HALF_RES = True
# 两条雾渐变用 1200x540 生成再放大（柔和渐变看不出差别）；False 回退 2400x1080。
GRAD_HALF_RES = True
# 编码器：libx264（默认，画质最可控）/ qsv（Intel 硬件编码，快约 18%）。
ENCODER = "libx264"
# 分镜缓存的图形版本号：滤镜图结构有改动就加 1，保证旧缓存不再命中（否则「修了却没变」）。
# 2 = 一镜两图在缓存路径改用真实输入标签 0:v / 1:v（旧版标签不存在，第二张被吞）。
GRAPH_VERSION = 3
PRESET = "fast"
# 分镜缓存：把每镜各渲成一段，改动后只重跑合成层。默认关（首渲略慢一点点）。
SHOT_CACHE = False

_STYLES_TMPL = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Vol,Microsoft YaHei,{vol},&H009AB9C8,&H00000000,&H0010161A,&H00000000,0,0,0,0,100,100,2,0,1,1,0,7,{vol_lr},{vol_lr},{vol_mv},1
Style: Title,Microsoft YaHei,{title},&H00E0EFF5,&H00000000,&H0010161A,&H00000000,0,0,0,0,100,100,2,0,1,2,0,7,{title_lr},{title_lr},{title_mv},1
Style: Body,Microsoft YaHei,{body},&H00E0EFF5,&H00E8E0CC,&H0010161A,&H00000000,0,0,0,0,100,100,1,0,1,3,1,2,{body_lr},{body_lr},{body_mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
STYLES = _STYLES_TMPL.format(**FORMAT_SPECS["16:9"])

# ---- 结尾针片尾卡样式模板（ASS 颜色是 &HAABBGGRR&：暖金 #BA7517 -> &H001775BA&）----
_ENDING_TMPL = """Style: Needle,Microsoft YaHei,{needle},&H00E0EFF5,&H00000000,&H0010161A,&H00000000,0,0,0,0,100,100,2,0,1,2,0,5,{end_lr},{end_lr},0,1
Style: Credit,Microsoft YaHei,{credit},&H009AB9C8,&H00000000,&H0010161A,&H00000000,0,0,0,0,100,100,1,0,1,1,0,5,{end_lr},{end_lr},0,1
Style: Shade,Microsoft YaHei,20,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Pin,Microsoft YaHei,20,&H001775BA,&H00000000,&H0010161A,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
"""
ENDING_STYLES = _ENDING_TMPL.format(**FORMAT_SPECS["16:9"])


def apply_format(name):
    """切画幅：改 W/H、说书人宽度、字幕样式与片尾样式。默认 16:9 逐字不变。

    竖版（9:16）不出现说书人——host_w=0，片头由调用方强制走 title 卡。
    """
    global FORMAT, W, H, HOST_W, HOST_X, STYLES, ENDING_STYLES
    name = str(name or "16:9").strip()
    name = {"portrait": "9:16", "vertical": "9:16",
            "landscape": "16:9", "horizontal": "16:9"}.get(name, name)
    if name not in FORMAT_SPECS:
        raise RuntimeError(f"画幅只支持 16:9 / 9:16，收到：{name}")
    spec = FORMAT_SPECS[name]
    FORMAT, W, H = name, spec["w"], spec["h"]
    HOST_W = spec["host_w"]
    HOST_X = W - HOST_W
    STYLES = _STYLES_TMPL.format(**spec)
    ENDING_STYLES = _ENDING_TMPL.format(**spec)

# 默认（第 001 篇样板）13 个镜头的景别表：
# (起始缩放, 镜头内推进量, x 静态偏移, y 偏移, 保留字段, 是否镜像)
# --episode 时整张表由 shots.json 提供，长度随旁白句数变化。
# 恢复 v10 原值（v11 为动态镜改过的句7/句13 复原）。
LENS = [
    (1.00, 0.055,    0,   0, 260, False),   # 句1  混沌卵远景
    (1.22, 0.045,  120,  20, 220, False),   # 句2  凿窍中景偏右
    (1.45, 0.040, -110, -30, 180, False),   # 句3  七窍近景偏左（重音）
    (1.10, 0.060,  -70, -60, 240, False),   # 句4  沉睡轮廓特写偏上
    (1.00, 0.060,    0,   0, 210, False),   # 句5  挥斧全景（动态）
    (1.16, 0.050,  100,  15, 190, False),   # 句6  清升浊降
    (1.12, 0.055,  -90, -20, 230, False),   # 句7  顶天立地仰视（恢复静图）
    (1.30, 0.040,   80,  30, 200, False),   # 句8  撑天柱星河
    (1.00, 0.050,    0,   0, 270, False),   # 句9  山河远景
    (1.24, 0.045, -130,  20, 230, True),    # 句10 云雾雷光（动态，镜像）
    (1.14, 0.055,   90, -25, 250, False),   # 句11 洞穴遗址
    (1.22, 0.045, -100,  10, 240, False),   # 句12 篝火口传
    (1.06, 0.050,    0,   0, 300, False),   # 句13 石斧定格收束（恢复静图）
]

# 默认（第 001 篇样板）素材分发：11 张图 + 1 段动态素材切成 2 个动态镜。
# 这些数量只是样板篇参数，不是引擎结构：--episode 时全部由 shots.json 决定，
# 图片张数、动态素材段数、动态镜个数都可任意增减；镜头数随旁白句数变化。
SRC = {
    0: "[4:v]", 1: "[5:v]", 2: "[6:v]", 3: "[7:v]",
    4: "[mv_a]",
    5: "[8:v]", 6: "[9:v]", 7: "[10:v]", 8: "[11:v]",
    9: "[mv_b]",
    10: "[12:v]", 11: "[13:v]", 12: "[14:v]",
}
IS_MOTION = {4, 9}
DIM = {9}
# 相位按镜头号记（原来按 src 字符串记，多段动态素材会互相覆盖）
PHASE_BY_N = {4: 0.40, 9: 1.90}
# 3:2 素材要先裁 16:9 的镜头；16:9 素材可在 shots.json 里设 crop16x9=false
CROP_MOTION = {4, 9}

meta = json.loads((V1 / "meta.json").read_text(encoding="utf-8"))
title = meta["title"]


def parse_args():
    argv = sys.argv[1:]
    ep = None
    # --bgm / --no-bgm：CLI 覆盖 shots.json 的 bgm.enabled；不带时以 shots.json 为准。
    global BGM_CLI
    BGM_CLI = True if "--bgm" in argv else (False if "--no-bgm" in argv else None)
    # 提速开关（不改变默认画面观感；--legacy-render 可逐项回退到旧实现对照）
    global ENCODER, PRESET, SUPERSAMPLE, ZOOM_OUT_SAME_RES, SPARK_HALF_RES, GRAD_HALF_RES, SHOT_CACHE
    global CLI_RENDER, FMT_CLI
    CLI_RENDER = {}
    FMT_CLI = None
    for i, a in enumerate(argv):
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        if a == "--episode" and i + 1 < len(argv):
            ep = nxt
        elif a == "--format" and nxt:
            FMT_CLI = nxt
        elif a == "--encoder" and nxt in ("libx264", "qsv", "auto"):
            ENCODER = "qsv" if (nxt == "auto" and _has_qsv()) else ("libx264" if nxt == "auto" else nxt)
            CLI_RENDER["encoder"] = ENCODER
        elif a == "--ss" and re.fullmatch(r"\d+", nxt):
            SUPERSAMPLE = max(1, min(6, int(nxt)))
            CLI_RENDER["ss"] = SUPERSAMPLE
        elif a == "--veryfast":
            PRESET = "veryfast"
        elif a == "--medium":
            PRESET = "medium"
        elif a == "--legacy-render":
            ZOOM_OUT_SAME_RES = SPARK_HALF_RES = GRAD_HALF_RES = False
        elif a == "--cache":
            SHOT_CACHE = True
            CLI_RENDER["cache"] = True
        elif a == "--no-cache":
            SHOT_CACHE = False
            CLI_RENDER["cache"] = False
    for i, a in enumerate(argv):
        if a in ("--test", "--sec", "--tail"):
            nxt = argv[i + 1] if i + 1 < len(argv) else ""
            default = 45.0 if a == "--test" else (8.0 if a == "--tail" else 30.0)
            sec = float(nxt) if re.fullmatch(r"\d+(\.\d+)?", nxt) else default
            mode = {"--test": "test", "--sec": "short", "--tail": "tail"}[a]
            return mode, sec, ep
    return "full", None, ep


def _has_qsv():
    try:
        r = subprocess.run([ffmpeg(), "-hide_banner", "-encoders"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        return "h264_qsv" in (r.stdout or "")
    except Exception:
        return False


MODE, SEC, EPISODE_ARG = parse_args()

# ---- 增量：--episode <shots.json> 时，把「每集专属」全部改为读数据 ----
# 不传该参数时，下面所有常量与 v12 原版逐字一致，行为完全不变。
#
# 输入槽布局（数量由 shots.json 决定，默认算出来与原版 15~20 一模一样）：
#   0 片头底 / 1 第 0 段动态素材 / 2 片头 mask / 3 音频
#   4 .. 4+N-1        N 张本篇图片（N 可为 0）
#   4+N ..            第 1 段起的额外动态素材（可 0 段）
#   之后 6 个固定槽：fog_w / talk / fog_c / particles / shade / spark
#   固定槽起点 FIX0 = 4 + N + max(0, M-1)；默认 N=11、M=1 → 15。
EP = None
# BGM_CLI 由 parse_args() 设置（--bgm/--no-bgm 覆盖；None 时读 shots.json.bgm）
BGM_CONF = None              # shots.json 的 bgm 配置（enabled/theme/root/seed/file/volume）
AUDIO_SRC = None             # 最终混流用的音频：默认 vo_v12.wav，加 BGM 后指向 mix 文件
MATERIAL_ISSUE = None        # --episode 时检测：新篇是否还在用 001 占位画面
ENDING = None                # shots.json.ending；None 时引擎行为与 v12 原版逐字一致
CARD = None                  # 由 build_subtitle() 算出的片尾定帧卡窗口
IMG_SLOT0 = 4
MOTION_PATHS = [ASSETS / "motion.mp4"] if (ASSETS / "motion.mp4").is_file() else []
SRT_PATH = V1 / "caption.srt"
BODY_VO = None
HEAD_TEXT = (V4 / "ref_text.txt").read_text(encoding="utf-8").strip()
HEAD_VOICE = None        # 方案 A：换篇时指向该篇 head_voice.wav；默认 None 沿用 v4 原声
HEAD_IMAGE = None        # shots.json.head_image：本篇片头底图；None = 沿用 001 的 bg1.jpg
HEAD_VIDEO = None        # shots.json.head_video：本篇专属口型视频；None = 沿用当前说书人的默认口型
# 说书人两套：ancient=古装（默认，旧篇逐字不变）；modern=现代休闲学者。
# shots.json.host 切换：同时换遮罩与默认口型视频（两者必须同人同构图）。
HOST_MASK = V2 / "host_rgba.png"        # 片头遮罩（alphaextract 用）
TALK_DEFAULT = V4 / "talk_test.mp4"     # 默认口型视频（旧式换音轨）
# 说明：真同步方案，VideoGen 用本篇片头音轨驱动生成 5 秒口型视频，
# 音视频同源，不再做柔焦遮挡。旧篇不带 head_video 时逐字沿用原链路。
def probe(path):
    r = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8")
    return float(r.stdout.strip())


def speech_end(path):
    """片头音轨里语音真正结束的时刻。
    阈值与原说书人实测口径一致（-38dB / 0.12s），否则量出的终点会长短不一。
    voice 模式按这个时刻决定片头长度，不再写死 5 秒。
    """
    r = subprocess.run(
        [ffmpeg(), "-hide_banner", "-i", str(path), "-vn",
         "-af", "silencedetect=noise=-38dB:d=0.12", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", r.stderr)]
    total = probe(path)
    return next((x for x in reversed(starts) if 0.2 < x < total - 0.01), total)


IMG_PATHS = [
    ROOT / "shot_01.jpg", ROOT / "shot_02.jpg", ROOT / "shot_03.jpg",
    ROOT / "shot_04.jpg", ROOT / "shot_05.jpg", ROOT / "shot_06.jpg",
    ROOT / "shot_07.jpg", V2 / "bg3.jpg", ROOT / "shot_08.jpg",
    ROOT / "shot_09.jpg", V2 / "bg2.jpg",
]


def _auto_move(note, beat, last):
    """按 note/beat 的文字标签推断运镜。标签写法建议【景别·主体】，见 workflow.md。"""
    t = f"{note} {beat}"

    def kw(*words):
        return any(w in t for w in words)

    # 结尾空镜优先拉远，收束感最强
    if last and kw("空镜", "无人", "留白", "雪", "海", "城墙", "原野", "废墟"):
        return {"type": "zoom", "amount": 0.06, "dir": "out"}
    if kw("空镜", "无人", "留白", "远景", "海面", "海上", "山川", "群山", "城墙",
          "雪原", "天空", "荒原", "戈壁", "沙漠", "草原", "江面", "船队", "舰队"):
        return {"type": "pan_x", "amount": 0.3}
    if kw("人群", "群臣", "朝会", "市集", "码头", "营帐", "列阵", "军阵", "街道",
          "人流", "长街", "广场", "船坞", "宴席", "大殿"):
        return {"type": "pan_x", "amount": 0.26}
    if kw("战斗", "厮杀", "交战", "冲锋", "混战", "对峙", "攻城", "血战", "追击"):
        return {"type": "zoom", "amount": 0.08, "dir": "in"}
    if kw("特写", "手部", "器物", "奏报", "奏折", "金锭", "银箱", "账册", "罗盘",
          "烛", "朱笔", "玺", "印", "碗", "茶", "刀", "信"):
        return {"type": "zoom", "amount": 0.07, "dir": "in"}
    if kw("人物", "背影", "侧后", "中景", "近景", "独处", "对话", "跪", "站",
          "坐", "面孔", "书生", "官员", "主角", "主人翁"):
        return {"type": "zoom", "amount": 0.06, "dir": "in"}
    return {"type": "zoom", "amount": 0.05, "dir": "in"}


def resolve_moves(shots):
    """静态镜默认按 note/beat 标签自动推断运镜（v2.7.0）；`{"auto": false}` 回落旧版纵向浮动。"""
    last = len(shots) - 1
    out, autos = {}, set()
    for i, s in enumerate(shots):
        if s.get("motion"):
            out[i] = {"type": "none"}        # 动态镜永远不动
            continue
        mv = dict(s.get("move") or {})
        kind = str(mv.get("type", "")).strip().lower()
        if kind in ("legacy", "float", "pan_y_legacy") or mv.get("auto") is False:
            out[i] = {}                      # 显式退回旧版纵向浮动
        elif kind in ("auto", "auto_move") or mv.get("auto") is True or not kind:
            out[i] = _auto_move(str(s.get("note", "")), str(s.get("beat", "")), i == last)
            autos.add(i)
        else:
            out[i] = mv                      # 人工指定，不参与节奏改写
    if not autos:
        return out
    # ① 横移方向交替，连续同向不超过 2 镜
    run_dir, run_len = "", 0
    for i in sorted(out):
        if i not in autos:
            continue
        mv = out[i]
        if mv.get("type") != "pan_x":
            run_dir, run_len = "", 0
            continue
        d = str(mv.get("dir", "")).strip().lower()
        if not d:
            d = "right" if run_dir == "left" else "left"
        if d == run_dir:
            run_len += 1
            if run_len >= 2:                   # 已经两连，第三镜强制反向
                d = "right" if d == "left" else "left"
                run_len = 1
        else:
            run_len = 1
        run_dir = d
        mv["dir"] = d
    # ② 每 5 镜至少留一镜不动（hold），给眼睛休息点；末镜豁免（收尾拉远更有价值）
    for w0 in range(0, len(shots), 5):
        win = [i for i in range(w0, min(w0 + 5, len(shots)))
               if i in autos and i != last and out[i].get("type") != "none"]
        if not win or any(out[i].get("type") == "hold" for i in win):
            continue
        out[win[len(win) // 2]] = {"type": "hold"}
    return out


MOVE_BY_N = {}          # --episode 篇由 resolve_moves() 覆盖；默认样板篇没有 move 配置

if EPISODE_ARG:
    EP = pathlib.Path(EPISODE_ARG).resolve()
    cfg = json.loads(EP.read_text(encoding="utf-8"))
    base = EP.parent
    meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    title = meta["title"]
    shot_cfg = cfg["shots"]                    # 长度 >= 旁白句数即可
    LENS = [tuple(s["lens"]) for s in shot_cfg]
    SRC = {s["n"]: s["src"] for s in shot_cfg}
    IS_MOTION = {s["n"] for s in shot_cfg if s.get("motion")}
    DIM = {s["n"] for s in shot_cfg if s.get("dim")}
    PHASE_BY_N = {s["n"]: float(s.get("phase") or 0.0)
                  for s in shot_cfg if s.get("motion")}
    CROP_MOTION = {s["n"] for s in shot_cfg
                   if s.get("motion") and s.get("crop16x9", True)}
    # 静态镜默认按 note 标签自动选运镜；写 {"auto": false} 或 type=legacy 退回纵向浮动。
    MOVE_BY_N = resolve_moves(shot_cfg)
    _moves = [i for i in sorted(MOVE_BY_N)
              if (MOVE_BY_N[i] or {}).get("type") not in (None, "", "none")]
    if _moves:
        txt = " | ".join(
            f"镜{i} {MOVE_BY_N[i].get('type')}"
            + (f" {MOVE_BY_N[i].get('amount')}" if MOVE_BY_N[i].get("amount") else "")
            + (f" {MOVE_BY_N[i].get('dir')}" if MOVE_BY_N[i].get("dir") else "")
            for i in _moves)
        print(f"[运镜] {txt}")
    ACCENTS = set(cfg.get("accents", []))
    # 本篇可自带渲染档（start_story --profile 写入）：只对命令行没显式指定的项生效。
    _r = cfg.get("render") or {}
    if "encoder" not in CLI_RENDER and _r.get("encoder"):
        ENCODER = ("qsv" if _r["encoder"] == "auto" and _has_qsv()
                   else ("libx264" if _r["encoder"] == "auto" else str(_r["encoder"])))
    if "ss" not in CLI_RENDER and _r.get("ss"):
        SUPERSAMPLE = max(1, min(6, int(_r["ss"])))
    if "cache" not in CLI_RENDER and "cache" in _r:
        SHOT_CACHE = bool(_r["cache"])
    BGM_CONF = cfg.get("bgm")            # 可选：{enabled, theme, root, seed, file, volume}
    # images 任意张数；motions 任意段数（兼容旧字段 motion 单字符串）
    IMG_PATHS = [base / p if p else None for p in cfg.get("images", [])]
    raw_motions = cfg.get("motions")
    if raw_motions is None:
        raw_motions = [cfg["motion"]] if cfg.get("motion") else []
    MOTION_PATHS = [base / p for p in raw_motions]
    SRT_PATH = base / "caption.srt"
    BODY_VO = base / "vo.wav"
    # 片头方案 A：视频流（AI 口型）全系列复用，只把音轨换成按篇合成的新词。
    # 文案与原声一致时（001）直接沿用 v4 原音轨，零改动。
    HEAD_TEXT = cfg.get("head_text", HEAD_TEXT)
    # 说书人随篇：modern=现代学者，ancient/缺省=古装（旧篇逐字不变）。
    if str(cfg.get("host", "ancient")).strip().lower() == "modern":
        HOST_MASK = V2 / "host_modern_rgba.png"
        TALK_DEFAULT = V4 / "talk_modern.mp4"
    # 片头底图随篇：给了 head_image 就用本篇图；不给时（001 及旧篇）沿用 bg1.jpg。
    HEAD_IMAGE = cfg.get("head_image")
    # 片头口型随篇：给了 head_video 就改为本篇专属口型视频（与本篇音轨同源，真同步）。
    # 不给时（001 及所有旧篇）沿用 v4 的 talk_test.mp4，下面所有片头滤镜逐字不变。
    HEAD_VIDEO = cfg.get("head_video")
    if HEAD_VIDEO:
        hv = base / HEAD_VIDEO
        if not hv.exists():
            raise RuntimeError(f"shots.json.head_video 指向的文件不存在：{hv}")
        HEAD_VIDEO = hv
    # 片头只有标题卡、且明确关掉口播时，跳过合成（省一次 TTS）
    _hmode = str((cfg.get("head") or {}).get("mode", "host")).strip().lower()
    _hvoice = bool((cfg.get("head") or {}).get("voice", True))
    if (_hmode == "title" and not _hvoice):
        pass
    elif HEAD_TEXT.strip() != (V4 / "ref_text.txt").read_text(encoding="utf-8").strip():
        sys.path.insert(0, str(ASSETS.parent / "series"))
        import head_voice
        HEAD_VOICE = head_voice.ensure(base, HEAD_TEXT)

    # ---- 素材归属校验：新故事必须使用本篇生成的画面 ----
    # 001 是样板篇，它的图就是本篇画面；换篇后若仍指向 001 或留空，
    # 只允许 --test 工程验证，正式片/短版一律拒绝，避免误把占位图当成品交付。
    status = str(cfg.get("status", ""))
    if "占位" in status or "缺本篇画面" in status:
        MATERIAL_ISSUE = f"shots.json 标记未完成：{status}"
    elif base.name != "001":
        imgs = cfg.get("images") or []
        foreign = [p for p in imgs
                   if p and re.search(r"(^|/)001/", str(p).replace("\\", "/"))]
        if foreign:
            MATERIAL_ISSUE = f"images 仍指向第 001 篇素材：{foreign[:3]}"
        elif not imgs:
            MATERIAL_ISSUE = "images 为空：本篇画面尚未生成"

    # ---- 结尾针：只有带 ending 才启用片尾定帧卡 ----
    # 不写该字段时，下面的字幕与滤镜链与 v12 原版逐字一致。
    ENDING = cfg.get("ending")

    # ---- 画幅（增量）：--format 优先，其次 shots.json 的 format，默认 16:9 ----
    # 16:9 与旧版逐像素一致；9:16 换分辨率、字幕安全边距与片尾字号。
    apply_format(FMT_CLI or cfg.get("format") or "16:9")

    # ---- 片头模式（增量，v12 之后追加）----
    # host ：说书人 5 秒口播（默认，旧篇行为逐字不变）
    # title：片头不出现人像，只留标题卡 + 底图；时长不再写死 5 秒
    #        —— 缺省按篇名字数自适应 0.9+0.21×字数（夹在 2.0~3.6 秒），也可用 head.len 指定
    #        voice 默认 true：保留片头口播与字幕（只是没有人像）；false 则片头纯静音无字幕
    head_cfg = cfg.get("head") or {}
    HEAD_MODE = str(head_cfg.get("mode", "host")).strip().lower()
    if HEAD_MODE not in ("host", "title"):
        raise RuntimeError(f"shots.json.head.mode 只能是 host / title，收到：{HEAD_MODE}")
    # 竖版不出现说书人：host 自动降级为 title 卡（口播照旧保留），不再依赖人像素材。
    if FORMAT == "9:16" and HEAD_MODE == "host":
        print("[画幅] 9:16 竖版：片头不出现说书人，自动改为标题卡 + 保留口播")
        HEAD_MODE = "title"
    HEAD_VOICE_ON = True if HEAD_MODE == "host" else bool(head_cfg.get("voice", True))
    if HEAD_MODE == "title":
        hlen = head_cfg.get("len")
        if hlen is None and HEAD_VOICE_ON:
            # 保留片头口播与字幕时，片头长度不再按篇名猜字数，而是跟着语音实测走：
            # 语音终点 + 0.15s 换气 + 0.8s 交叉溶解；文字长就长、文字短就短。
            spk = speech_end(HEAD_VOICE or TALK_DEFAULT)
            hlen = min(4.2, max(2.6, spk + 0.95))
            HEAD_SUB_S = 0.20
            HEAD_SUB_E = min(hlen - 0.12, spk + 0.22)
        if hlen is None:
            hlen = min(3.6, max(2.0, 0.9 + 0.21 * len(meta.get("title", ""))))
        HEAD_LEN = max(1.6, float(hlen))     # 片头至少留 1.6s，否则标题卡看不清
        EXIT_AT = max(0.0, HEAD_LEN - JOIN_XF)
        BODY_SHIFT = HEAD_LEN - EXIT_AT


# 运行时产物目录：001 自身用引擎目录；其他篇写 episodes/<id>/_render，
# 避免渲染某篇时把 001 的 vo_v12.wav / sub_v12.ass / still 覆盖掉。
WORK = (EP.parent / "_render") if EP else ROOT
WORK.mkdir(parents=True, exist_ok=True)
# 系列名（片头卷名行与片尾落款）：按 meta.series 覆盖；缺省沿用《中华上下五千年》。
SERIES_NAME = meta.get("series") or "中华上下五千年"


def run(cmd):
    # 统一把命令名换成解析到的绝对路径（Windows 上 ffmpeg 常不在 PATH）。
    cmd = [ffmpeg() if cmd[0] == "ffmpeg" else (ffprobe() if cmd[0] == "ffprobe" else cmd[0])] + list(cmd[1:])
    r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 失败：\n" + r.stderr[-20000:])
    return r

def ass_time(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def parse_srt(path):
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        ls = [l for l in block.splitlines() if l.strip()]
        if len(ls) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", ls[1])
        if not m:
            continue
        a = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        b = int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8]) / 1000
        cues.append((a, b, "\n".join(ls[2:])))
    return cues


def wrap_cn(text, width=24):
    out, buf = [], ""
    for ch in text:
        buf += ch
        if len(buf) >= width and ch in "，。；：？！、":
            out.append(buf)
            buf = ""
        elif len(buf) >= width + 2:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return r"\N".join(out)


def karaoke(text, seconds):
    parts = text.split(r"\N")
    visible = sum(len(p) for p in parts)
    step = max(1, int(seconds * 100 / max(visible, 1)))
    return r"\N".join("".join(f"{{\\kf{step}}}{c}" for c in p) for p in parts)


def build_particles():
    """本机生成柔光微尘透明层。"""
    pw, ph = 2000, 1160
    random.seed(2026)
    im = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for _ in range(130):
        x, y = random.randint(0, pw), random.randint(0, ph)
        r = random.choice([1, 1, 2, 2, 3])
        a = random.randint(60, 165)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(248, 226, 190, a))
    im.filter(ImageFilter.GaussianBlur(1.3)).save(WORK / "particles.png")


def build_shade():
    """字幕下方居中：底部对称羽化渐变，左右两端也淡出，避免硬矩形。"""
    yy = np.arange(H, dtype=float)[:, None]
    xx = np.arange(W, dtype=float)[None, :]
    va = np.clip((yy - 745) / 300, 0, 1) ** 1.35 * 155
    hl = np.clip(xx / 260, 0, 1)
    hr = np.clip((W - xx) / 260, 0, 1)
    a = (va * hl * hr).clip(0, 255).astype("uint8")
    rgba = np.zeros((H, W, 4), dtype="uint8")
    rgba[..., 3] = a
    Image.fromarray(rgba, "RGBA").save(WORK / "caption_shade.png")


def build_spark():
    """v12 新增：一枚柔和光点（径向渐变 + 模糊），供 16 路独立飘动。
    中心暖白、边缘透明；不是颗粒噪点，是能看出形状的浮尘/火星。"""
    sz = 96
    yy, xx = np.mgrid[0:sz, 0:sz].astype(np.float32)
    d = np.sqrt((xx - sz / 2) ** 2 + (yy - sz / 2) ** 2)
    core = np.clip(1.0 - d / 9.0, 0, 1) ** 1.5          # 亮核
    halo = np.clip(1.0 - d / 38.0, 0, 1) ** 2.2         # 柔光晕
    a = (core * 185 + halo * 70).clip(0, 235)
    rgb = np.stack([255 * np.ones_like(a), 222 * np.ones_like(a),
                    178 * np.ones_like(a)], axis=2)
    rgba = np.dstack([rgb, a]).astype("uint8")
    Image.fromarray(rgba, "RGBA").filter(
        ImageFilter.GaussianBlur(1.2)).save(WORK / "spark.png")


def build_audio(body_vo=None):
    # body_vo 给 --episode 用（该篇自己的 vo.wav）；默认仍是样板篇的语音。
    body = body_vo if body_vo else V1 / "vo.wav"
    # HEAD_VOICE 已按原音轨窗口对齐好，但响度归一仍在这里做，
    # 保证两条路径（原声 / 按篇合成）走完全相同的 loudnorm 链。
    if not HEAD_VOICE_ON:
        # 片头明确关掉口播：留出与片头等长的静音，旁白仍从 HEAD_LEN 起。
        run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", f"{HEAD_LEN:.3f}", "-c:a", "pcm_s16le", "head.wav"])
    else:
        head_src = str(HEAD_VOICE) if HEAD_VOICE else str(TALK_DEFAULT)
        run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
             "-i", head_src, "-t", f"{HEAD_LEN:.3f}", "-vn",
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:linear=true",
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "head.wav"])
    (WORK / "concat_v12.txt").write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in [WORK / "head.wav", body]),
        encoding="utf-8")
    run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
         "-f", "concat", "-safe", "0", "-i", "concat_v12.txt",
         "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "vo_v12.wav"])

    global AUDIO_SRC
    AUDIO_SRC = WORK / "vo_v12.wav"
    # ---- 背景音乐（可选）：默认不开；--bgm 或 shots.json.bgm.enabled 启用 ----
    conf = dict(BGM_CONF or {})
    enabled = conf.pop("enabled", False)
    if BGM_CLI is not None:
        enabled = BGM_CLI
    if enabled:
        dur = probe(AUDIO_SRC)
        real_file = conf.pop("file", None)          # 用户提供真实 BGM 时直接用它
        if real_file:
            bed = (base / real_file) if EP else pathlib.Path(real_file)
            if not bed.exists():
                raise RuntimeError(f"bgm.file 指向的音频不存在：{bed}")
        else:
            sys.path.insert(0, str(ASSETS.parent / "series"))
            import bgm as bgm_mod
            theme = conf.pop("theme", "神话")
            bed = WORK / "bgm_bed.wav"
            bgm_mod.generate(bed, dur + 0.5,
                             seed=int(conf.pop("seed", 7)),
                             root=float(conf.pop("root", 220.0)),
                             theme=str(theme))
        volume = float(conf.pop("volume", 0.18))    # BGM 在旁白下的基础音量
        # 侧链压缩：旁白响时把 BGM 压低（ducking），保证字字清楚；末尾 alimiter 防爆音。
        duck = (f"[1:a]volume={volume}[bg];"
                f"[0:a]asplit=2[vomain][voside];"
                f"[bg][voside]sidechaincompress="
                f"threshold=0.02:ratio=6:attack=20:release=400[ducked];"
                f"[vomain][ducked]amix=inputs=2:duration=first:normalize=0,"
                f"alimiter=limit=0.95[aout]")
        mixed = WORK / "vo_v12_bgm.wav"
        run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
             "-i", str(AUDIO_SRC), "-i", str(bed),
             "-filter_complex", duck, "-map", "[aout]",
             "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(mixed)])
        AUDIO_SRC = mixed
        print(f"[BGM] 已混入：{'用户音频 ' + str(real_file) if real_file else '合成古风氛围床（' + str(conf.get('theme', '神话')) + '）'}"
              f"，基础音量 {volume}，侧链闪避旁白")
    return probe(AUDIO_SRC)


# ---- 结尾针：片尾定帧卡（只在 shots.json 带 ending 时才加入样式与事件）----
# 压暗底用 ASS 绘图矩形，跟随事件 \fad 淡入淡出，避免 drawbox 硬切闪一下。
# 样式模板 _ENDING_TMPL / ENDING_STYLES 与 apply_format() 已提到文件开头（画幅相关）。


def build_styles():
    """默认路径返回原 STYLES，逐字不变；有片尾卡时才把新样式插进 Styles 段。"""
    if not ENDING:
        return STYLES
    out = STYLES.replace("\n\n[Events]", "\n" + ENDING_STYLES + "\n[Events]")
    if out == STYLES:
        raise RuntimeError("片尾卡样式插入失败：STYLES 结构与预期不一致")
    return out


def needle_lines(text):
    """金句折两行：优先在靠近中点的标点断开，否则按字数对半。"""
    if len(text) <= 11:
        return text
    mid = len(text) // 2
    for i in range(mid, 0, -1):
        if text[i] in "，。；：？！、":
            return text[:i + 1] + r"\N" + text[i + 1:]
    return text[:mid] + r"\N" + text[mid:]


def ending_events(s, e, needle, credit):
    """片尾定帧卡的 ASS 事件。s/e 是成片时间（--tail 预览时传已偏移的时间）。

    位置全部按当前画幅算：16:9 时等于原来的 960/500、920/584、960/650（逐像素不变）。
    """
    a, b = ass_time(s), ass_time(e)
    fade = r"\fad(500,650)"
    cx = W // 2                                  # 水平居中（16:9 = 960）
    ny = int(round(H * 0.463))                   # 金句基线（16:9 = 500）
    py = int(round(H * 0.541))                   # 短横线（16:9 = 584）
    cy = int(round(H * 0.602))                   # 落款（16:9 = 650）
    pin_w = max(40, int(round(W * 0.0417)))      # 「针」长度（16:9 = 80）
    return [
        # 半透明压暗底：绘图矩形覆盖全屏，随事件淡入淡出
        (f"Dialogue: 0,{a},{b},Shade,,0,0,0,,"
         r"{\an7\pos(0,0)" + fade
         + r"\p1\1c&H000000&\1a&H5A&\3a&HFF&\bord0\shad0"
         + f"}}m 0 0 l {W} 0 l {W} {H} l 0 {H}{{\\p0}}"),
        # 居中金句
        (f"Dialogue: 0,{a},{b},Needle,,0,0,0,,"
         f"{{\\an5\\pos({cx},{ny})" + fade + "}" + needle_lines(needle)),
        # 一记短横线（「针」）
        (f"Dialogue: 0,{a},{b},Pin,,0,0,0,,"
         f"{{\\an7\\pos({cx - pin_w // 2},{py})" + fade
         + r"\p1\1c&H001775BA&\bord0\shad0"
         + f"}}m 0 0 l {pin_w} 0 l {pin_w} 3 l 0 3{{\\p0}}"),
        # 落款
        (f"Dialogue: 0,{a},{b},Credit,,0,0,0,,"
         f"{{\\an5\\pos({cx},{cy})" + fade + "}" + credit),
    ]


def render_tail(sec):
    """--tail N：只渲片尾针预览片，用压暗底图 + 真实 ASS 字卡，不渲全片。"""
    if not CARD:
        raise RuntimeError(
            "--tail 需要 shots.json 带 ending，且 vo.wav 尾部留白足够（重跑建篇刷新）")
    start = max(0.0, CARD["start"] - 0.8)
    needed = CARD["end"] - start + 0.7
    dur = min(sec, max(2.0, needed))
    off = start
    (WORK / "sub_tail.ass").write_text(
        build_styles() + "\n".join(
            ending_events(CARD["start"] - off, CARD["end"] - off,
                          CARD["needle"], CARD["credit"])) + "\n",
        encoding="utf-8")
    bg = None
    for p in reversed(IMG_PATHS):
        if p and pathlib.Path(p).exists():
            bg = pathlib.Path(p)
            break
    # 工程验证时占位图不是本篇画面：预览改用中性暗底，避免把 001 画面当成品展示。
    # 正式片仍压暗定格本篇最后一镜，--tail 只是用来验收字卡的轻量预览。
    if bg is not None and MATERIAL_ISSUE and "/001/" in str(bg).replace("\\", "/"):
        bg = None
    suffix = f"_{meta['id']}" if EP else ""
    out = WORK / f"tail_v12{suffix}.mp4"
    if bg:
        v_in = ["-loop", "1", "-framerate", str(FPS), "-i", str(bg)]
        chain = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                 f"crop={W}:{H},eq=brightness=-0.14:contrast=1.05,")
    else:
        v_in = ["-f", "lavfi", "-i",
                f"color=c=0x10161A:s={W}x{H}:r={FPS}:d={dur:.3f}"]
        chain = "[0:v]"
    graph = chain + "subtitles=sub_tail.ass,format=yuv420p[v]"
    run(["ffmpeg", "-y", "-hide_banner", "-v", "error",
         *v_in,
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", graph,
         "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out)])
    print(f"片尾针预览：{out.name}  {probe(out):.1f}s | "
          f"金句「{CARD['needle']}」 | 卡窗口 {CARD['start']:.2f}~{CARD['end']:.2f}s")


def build_subtitle(cues, full_len):
    global CARD
    CARD = None
    if ENDING and cues:
        needle = ENDING.get("needle") or cues[-1][2]
        credit = (ENDING.get("credit")
                  or f"{SERIES_NAME} · {meta['volume']} · {title}")
        gap = float(ENDING.get("gap", 0.45))
        fade_out = float(ENDING.get("fade_out", 0.25))
        cs = cues[-1][1] + HEAD_LEN + gap
        ce = full_len - fade_out
        if ce - cs < 1.2:
            print(f"[提示] 片尾留白不足（卡窗口 {ce - cs:.2f}s < 1.2s），跳过结尾针；"
                  "重跑建篇刷新 vo.wav 尾部留白即可启用")
        else:
            CARD = {"start": cs, "end": ce, "needle": needle, "credit": credit}
    # 常驻卷名/标题：有片尾卡时提前收尾，给字卡让出画面
    end_all = ass_time(CARD["start"] if CARD else full_len)
    ev = [
        f"Dialogue: 0,0:00:00.00,{end_all},Vol,,0,0,0,,"
        + r"{\fad(1000,900)}" + f"{SERIES_NAME} · {meta['volume']}",
        f"Dialogue: 0,0:00:00.00,{end_all},Title,,0,0,0,,"
        + r"{\fad(1000,900)}" + title,
    ]
    # 字幕事件的边距按画幅取（16:9 仍是 220,220,115，逐字不变；竖版走竖屏安全区）
    _sp = FORMAT_SPECS[FORMAT]
    body_margins = f"{_sp['body_lr']},{_sp['body_lr']},{_sp['body_mv']}"
    wrap_w = _sp["wrap"]        # 折行字数：按 (画宽-2*边距)/字号 推，16:9=23，竖版=18
    # 重音句的加粗放大字号按正文同比（正文×50/46：16:9 ≈59，竖版 ≈52）
    accent_fs = int(round(_sp["body"] * 50 / 46))
    head_text = HEAD_TEXT
    if HEAD_VOICE_ON:     # 片头没有口播（title 且 head.voice=false）时不挂片头字幕
        ev.append(
            f"Dialogue: 0,{ass_time(HEAD_SUB_S)},{ass_time(HEAD_SUB_E)},Body,,{body_margins},,"
            + r"{\fad(140,300)}"
            + karaoke(wrap_cn(head_text, wrap_w), HEAD_SUB_E - HEAD_SUB_S))

    for i, (start, end, text) in enumerate(cues):
        shown = end - start + 0.12
        effect = (r"{\fad(140,220)"
                  r"\t(0,160,\fscx88\fscy88)"
                  r"\t(160,380,\fscx106\fscy106)"
                  r"\t(380,600,\fscx100\fscy100)}")
        if i in ACCENTS:
            effect = f"{{\\fs{accent_fs}\\bord4}}" + effect
        s, e = start + HEAD_LEN, end + HEAD_LEN
        ev.append(
            f"Dialogue: 0,{ass_time(s)},{ass_time(e + 0.12)},"
            f"Body,,{body_margins},,{effect}{karaoke(wrap_cn(text, wrap_w), shown)}")
    if CARD:
        ev.extend(ending_events(CARD["start"], CARD["end"],
                                CARD["needle"], CARD["credit"]))
    (WORK / "sub_v12.ass").write_text(
        build_styles() + "\n".join(ev) + "\n", encoding="utf-8")


def srt_time(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def build_srt(cues, limit):
    head_text = HEAD_TEXT
    head_items = [(HEAD_SUB_S, HEAD_SUB_E, head_text)] if HEAD_VOICE_ON else []
    items = head_items + [(s + HEAD_LEN, e + HEAD_LEN, t) for s, e, t in cues]
    items = [(s, min(e, limit), t) for s, e, t in items if s < limit]
    return "\n".join(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n"
                     for i, (s, e, t) in enumerate(items, 1))


def shot_plan(cues, main_len):
    """按句子起点切镜头，精确计算 xfade 的 offset / 每段时长（沿用 v9/v10）。"""
    bounds = [0.0] + [cues[i][0] + BODY_SHIFT for i in range(1, len(cues))] + [main_len]
    n = len(bounds) - 1
    T = [bounds[i] - X_FADE / 2 for i in range(1, n)]
    D = []
    if n == 1:                          # 单镜头：没有切点，整段就是一镜
        return bounds, T, [main_len + 0.10]
    for i in range(n):
        if i == 0:
            D.append(T[0] + X_FADE + 0.10)
        elif i < n - 1:
            D.append(T[i] - T[i - 1] + X_FADE + 0.10)
        else:
            D.append(main_len - T[-1])
    return bounds, T, D


def resolve_srcs(n):
    """把每镜 src 解析成 ffmpeg 标签，并生成图片/动态素材的输入节点。

    推荐写法：img:i（第 i 张图） / motion:j（第 j 段动态素材）。
    兼容旧式：[N:v]（输入槽 N 的图） / [mv_a]、[mv_b]（第 0 段动态素材）。
    同一段动态素材被多个镜引用时自动 split，各镜可各有 phase。
    图片张数与动态素材段数都来自 shots.json，没有任何固定数量。
    """
    nodes = []
    used_imgs = set()
    refs = {j: [] for j in range(len(MOTION_PATHS))}
    labels = {}
    for i in range(n):
        raw = SRC[i]
        if raw.startswith("img:") or re.fullmatch(r"\[\d+:v\]", raw):
            idx = (int(raw[4:]) if raw.startswith("img:")
                   else int(raw[1:-3].split(":")[0]) - IMG_SLOT0)
            if not 0 <= idx < len(IMG_PATHS):
                raise RuntimeError(
                    f"镜 {i} 引用了不存在的图片 {idx}（共 {len(IMG_PATHS)} 张）")
            labels[i] = f"img{idx}"
            used_imgs.add(idx)
        elif raw.startswith("img2:"):
            # 一镜两图：src 形如 "img2:0,1"，前半镜用图0、后半镜用图1，中间短溶解。
            # 镜头数仍=句数，字幕与切镜点不变；只有这一镜内部多一次画面切换。
            try:
                a, b = (int(x) for x in raw[5:].split(","))
            except (ValueError, TypeError):
                raise RuntimeError(f"镜 {i} 的 img2 src 格式错，应为 img2:i,j：{raw}")
            if not (0 <= a < len(IMG_PATHS) and 0 <= b < len(IMG_PATHS)):
                raise RuntimeError(
                    f"镜 {i} 的 img2 引用了不存在的图片（共 {len(IMG_PATHS)} 张）：{a},{b}")
            labels[i] = ("img2", f"img{a}", f"img{b}")
            used_imgs.add(a)
            used_imgs.add(b)
        else:
            j = int(raw[7:]) if raw.startswith("motion:") else 0
            if j not in refs:
                raise RuntimeError(
                    f"镜 {i} 引用了不存在的动态素材段 {j}（共 {len(MOTION_PATHS)} 段）")
            refs[j].append(i)
    # 只为真正被镜头引用的图片建 null 节点；动态镜替换掉的图若仍建节点会输出悬空，
    # ffmpeg 报 "null output unconnected"。输入命令行仍保留全部图片以固定槽号。
    nodes = [f"[{IMG_SLOT0 + i}:v]null[img{i}]" for i in sorted(used_imgs)] + nodes
    for j, ns in sorted(refs.items()):
        if not ns:
            continue        # 该段素材没有镜头用，输入仍占位但不进滤镜图
        slot = 1 if j == 0 else IMG_SLOT0 + len(IMG_PATHS) + (j - 1)
        if len(ns) == 1:
            nodes.append(f"[{slot}:v]null[mv{j}_0]")
        else:
            outs = "".join(f"[mv{j}_{k}]" for k in range(len(ns)))
            nodes.append(f"[{slot}:v]split={len(ns)}{outs}")
        for k, i in enumerate(ns):
            labels[i] = f"mv{j}_{k}"
    return labels, nodes


def shot_chain(label, i, dur, phase, is_motion, crop16x9, half=0, tag=None):
    """单个镜头：静态景别 + 方向性垂直浮动。
    label 由 resolve_srcs() 解析得到，不再绑定固定输入槽。
    画面运动：奇数镜（第1/3/5…镜）从上往下浮动，偶数镜从下往上浮动；
    zoom 固定、x 固定，无推近脉冲、无左右震动、无叠加微抖。
    片头/片尾针不在此链。
    **动态镜不参与位移**：素材自带运镜，再叠浮动＝双重运动，还要白走一遍 3x 超采样把画面糊软；
    所以 is_motion 走独立分支，只取帧/裁切/压暗/镜像/定时长。
    half=0 正常单图；half=1/2 表示一镜两图的前/后半段（各自走完整浮动，用 tag 命名输出）。"""
    tag = tag or f"shot{i}"
    # 一镜两图时每段各自走完整平移，段长 = 半镜 + 半个溶解（两段重叠 XF2）。
    seg_dur = (dur / 2 + 0.15) if half else dur
    z0, dz, dx, dy, per, mirror = LENS[i]

    # ---- 动态镜：不浮动、不超采样，原速取帧后只做必要的裁切与调色 ----
    if is_motion:
        pre = f"fps={FPS},"
        if phase > 0:
            pre += f"trim=start={phase:.3f},setpts=PTS-STARTPTS,"
        if crop16x9:
            # 3:2 素材先裁 16:9（这一刀也切掉「AI生成」水印）；16:9 素材写 crop16x9:false 跳过。
            # 竖版没有 16:9 可裁，改为等比铺满 9:16（会裁掉左右两侧）。
            if FORMAT == "16:9":
                pre += "crop=1152:648:0:120,"
            else:
                pre += (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                        f"crop={W}:{H},")
        if i in DIM:
            pre += "eq=brightness=-0.13:contrast=1.22:saturation=0.88,"
        if mirror:
            pre += "hflip,"
        # 等比铺满再居中裁：16:9 源不损失画面，非 16:9 源宁可裁边也不拉伸。
        pre += f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        return (f"[{label}]{pre}"
                f"trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS,"
                f"format=yuv420p[{tag}]")
    # 每镜至少放大到 1.12：zoom=1 时 ih-ih/zoom=0，没有富余空间就无法上下平移。
    # dz / per 为旧镜头表保留字段，已停用：zoom 固定，不再推近、不再开镜脉冲。
    z0 = max(z0, 1.12)
    # ---- 镜头运动模式（shots[].move，可选；不写＝原来的纵向浮动，逐字不变）----
    #   {"type": "pan_y | pan_x | zoom", "amount": 0.2, "dir": "down|up | left|right | in|out"}
    #   pan_x.amount = 可用横向余量的比例（0.05~0.5，默认 0.2）；技能层建议 ≤0.3（约 3% 画宽）
    #   zoom.amount  = 绝对 zoom 增量（0.01~0.08，默认 0.04）
    mv = MOVE_BY_N.get(i) or {}
    kind = str(mv.get("type", "pan_y")).strip().lower()
    direction = str(mv.get("dir", "")).strip().lower()
    prog = f"min(1,on/{seg_dur * FPS:.1f})"
    if kind in ("hold", "none", "static", "still"):
        # 完全不动：给眼睛留休息点，也用于「每 5 镜一静」的节奏约束
        z = f"{z0}"
        x = f"iw/2-(iw/zoom/2)+{dx * SUPERSAMPLE}"
        y = "ih/2-(ih/zoom/2)"
    elif kind in ("zoom", "zoom_ramp"):
        amt = max(0.01, min(0.08, float(mv.get("amount", 0.04) or 0.04)))
        z_in = direction != "out"
        z = f"{z0}*(1{'+' if z_in else '-'}{amt:.4f}*{prog})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif kind == "pan_x":
        amt = max(0.05, min(0.5, float(mv.get("amount", 0.2) or 0.2)))
        z = f"{z0}"
        span_x = f"(iw-iw/zoom)*{amt:.3f}"
        left = direction == "left" or (not direction and i % 2 == 0)
        x = (f"iw/2-(iw/zoom/2)-{span_x}*(1-{prog})" if left
             else f"iw/2-(iw/zoom/2)+{span_x}*{prog}")
        y = "ih/2-(ih/zoom/2)"
    else:  # pan_y（默认）：与旧行为逐字一致
        z = f"{z0}"
        # 超采样后源宽变成 W*SUPERSAMPLE，dx 同比放大才能在成片里保持原构图偏移。
        x = f"iw/2-(iw/zoom/2)+{dx * SUPERSAMPLE}"
        span = f"(ih-ih/zoom)*{MOTION_SCALE}"
        down = direction in ("down", "top") or (not direction and i % 2 == 0)
        y = f"{span}*(1-{prog})" if down else f"{span}*{prog}"
    pre = f"fps={FPS},"
    if is_motion and phase > 0:
        pre += f"trim=start={phase:.3f},setpts=PTS-STARTPTS,"
    if is_motion and crop16x9:
        # 3:2 素材先裁 16:9 再缩放，避免拉伸；这一刀也切掉「AI生成」水印。
        # 16:9 素材在 shots.json 里写 "crop16x9": false 即可跳过。
        # 竖版画幅下没有 16:9 可裁，改为等比铺满 9:16（会裁掉左右两侧，动态素材建议先竖拍）。
        if FORMAT == "16:9":
            pre += "crop=1152:648:0:120,scale=1920:1080,"
        else:
            pre += (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},")
    if i in DIM:
        pre += "eq=brightness=-0.13:contrast=1.22:saturation=0.88,"
    if mirror:
        pre += "hflip,"
    # 超采样：先把静帧放大到 3x 画布（源域，保证每帧位移 >=1 源像素），zoompan 只
    # 输出 1920x1080——量化粒度仍折算成 1/3 输出像素，与旧做法成像一致，但每帧
    # 不再生成 18.7MP。ZOOM_OUT_SAME_RES=False 时回退旧的「5760 输出 + lanczos 缩回」。
    sw, sh = W * SUPERSAMPLE, H * SUPERSAMPLE
    pre += f"scale={sw}:{sh}:flags=lanczos,"
    if ZOOM_OUT_SAME_RES:
        zp_size, zp_tail = f"{W}x{H}", ""
    else:
        zp_size, zp_tail = f"{sw}x{sh}", f"scale={W}:{H}:flags=lanczos,"
    return (f"[{label}]{pre}"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={zp_size}:fps={FPS},"
            f"{zp_tail}"
            f"trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS,"
            f"format=yuv420p[{tag}]")


def _asset_stamp(p):
    q = pathlib.Path(p)
    if not q.is_file():
        return [str(q), 0, 0]
    st = q.stat()
    return [str(q), st.st_size, int(st.st_mtime)]


def build_shot_cache(n, D, labels):
    """把每一镜单独渲成一段 mp4 并复用（--cache）。

    只有「素材文件 + lens + 时长 + 相位 + 运动参数」完全一致才命中；改字幕、改叠加层、
    换片头底图、换片头音轨都不会失效，所以返工只重跑最后一遍合成。中间片 crf15。
    """
    cache = (base / "缓存" / "镜头") if EP else (ROOT / "缓存" / "镜头")
    cache.mkdir(parents=True, exist_ok=True)
    stamps = {f"img{k}": (_asset_stamp(p) if p else [])
              for k, p in enumerate(IMG_PATHS)}
    stamps.update({f"mv{k}": _asset_stamp(p) for k, p in enumerate(MOTION_PATHS)})
    files, reused, built = [], 0, 0
    for i in range(n):
        lab = labels[i]
        if isinstance(lab, tuple) and lab[0] == "img2":
            srcs = {"a": stamps.get(lab[1], []), "b": stamps.get(lab[2], [])}
        elif str(lab).startswith("mv"):
            j = int(str(lab)[2:].split("_")[0])
            srcs = {f"mv{j}": stamps.get(f"mv{j}", [])}
        else:
            srcs = {str(lab): stamps.get(lab, [])}
        key = hashlib.sha1(json.dumps({
            "i": i, "srcs": srcs, "lens": list(LENS[i]),
            "dur": round(D[i], 3), "phase": PHASE_BY_N.get(i, 0.0),
            "motion": i in IS_MOTION, "crop": i in CROP_MOTION, "dim": i in DIM,
            "move": MOVE_BY_N.get(i) or {},
            "ss": SUPERSAMPLE, "zp": ZOOM_OUT_SAME_RES, "wh": [W, H, FPS],
            "scale": MOTION_SCALE,
            # 图形版本：一镜两图在缓存路径改用真实输入标签（gv=2），旧缓存一律失效，
            # 否则修了 bug 也会命中旧片、看起来「没修」。
            "gv": GRAPH_VERSION,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
        out = cache / f"{i:02d}_{key}.mp4"
        if out.is_file() and out.stat().st_size > 0:
            reused += 1
            files.append(out)
            continue
        if isinstance(lab, tuple) and lab[0] == "img2":
            ai, bi = int(lab[1][3:]), int(lab[2][3:])
            if ai == bi:
                print(f"[注意] 镜 {i} 的 img2 前后两张指向同一张图（{IMG_PATHS[ai]}），"
                      "该镜内部不会有画面切换，请换一张")
            args = ["-loop", "1", "-framerate", str(FPS), "-i", str(IMG_PATHS[ai]),
                    "-loop", "1", "-framerate", str(FPS), "-i", str(IMG_PATHS[bi])]
            # 这一镜是独立 ffmpeg 进程，只有 0:v / 1:v 两路输入；
            # 不能沿用主图里的 imgN 过滤标签（不存在，会被静默丢成同一张）。
            g = (shot_chain("0:v", i, D[i], 0.0, False, False, half=1, tag=f"c{i}_a")
                 + ";" + shot_chain("1:v", i, D[i], 0.0, False, False, half=2, tag=f"c{i}_b")
                 + f";[c{i}_a][c{i}_b]xfade=transition=fade:duration=0.30:"
                 f"offset={D[i] / 2 - 0.15:.3f}[shot{i}]")
        elif str(lab).startswith("mv"):
            j = int(str(lab)[2:].split("_")[0])
            args = ["-stream_loop", "-1", "-i", str(MOTION_PATHS[j])]
            g = shot_chain("0:v", i, D[i], PHASE_BY_N.get(i, 0.0), True,
                           i in CROP_MOTION, tag=f"shot{i}")
        else:
            idx = int(str(lab)[3:])
            args = ["-loop", "1", "-framerate", str(FPS), "-i", str(IMG_PATHS[idx])]
            g = shot_chain("0:v", i, D[i], 0.0, False, False, tag=f"shot{i}")
        run([ffmpeg(), "-y", "-hide_banner", "-v", "error", *args,
             "-filter_complex", g, "-map", f"[shot{i}]", "-t", f"{D[i]:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "15",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
        built += 1
        files.append(out)
    print(f"[缓存] 分镜 {n} 段：复用 {reused} / 新渲 {built} -> {cache}")
    return files


def main():
    # 不带 --episode（渲染 001 样板/工程复核）时，画幅只能由命令行给，默认仍是 16:9。
    if not EPISODE_ARG:
        apply_format(FMT_CLI or "16:9")
    # 新篇素材未完成时不许出正式片；--test / --tail 只作工程验证，明确提示。
    if MATERIAL_ISSUE and MODE not in ("test", "tail"):
        raise RuntimeError(
            f"拒绝出正式片：{MATERIAL_ISSUE}\n"
            "新故事必须使用本篇生成的画面。先用 fill_shots.py 替换本篇图，"
            "或仅用 --test / --tail 做工程验证。")
    if MATERIAL_ISSUE:
        print(f"[工程验证] 素材非本篇画面：{MATERIAL_ISSUE}")
        if not IMG_PATHS:
            raise RuntimeError(
                "images 为空，--test / --tail 也至少需要一张画面；"
                "请用 start_story.py --images-dir / --images-paths 补本篇图"
                "（精简包已无内置占位素材，--placeholder 不可用）")
    # 前置校验：素材缺文件时立刻失败，别等滤镜跑一半才报错（--cache 也一样）。
    missing = [str(p) for p in list(IMG_PATHS) + list(MOTION_PATHS)
               if p is None or not pathlib.Path(p).is_file()]
    if missing:
        raise RuntimeError(
            "以下画面素材文件不存在（shots.json 里引用了不存在的路径）：\n  "
            + "\n  ".join(missing[:8])
            + f"\n共 {len(missing)} 个。请先补图或改 shots.json 的 images/motions。")
    build_particles()
    build_shade()
    build_spark()
    total = build_audio(BODY_VO)
    main_audio = probe(BODY_VO if BODY_VO else V1 / "vo.wav")
    cues = parse_srt(SRT_PATH)
    build_subtitle(cues, total)
    if MODE == "tail":
        render_tail(SEC)
        return
    limit = min(total, SEC) if SEC else total

    main_len = main_audio + JOIN_XF
    bounds, T, D = shot_plan(cues, main_len)
    n = len(D)                          # 镜头数 = 旁白句数，不是固定 13
    if n > len(LENS) or any(i not in SRC for i in range(n)):
        raise RuntimeError(
            f"旁白 {n} 句需要至少 {n} 组镜头配置（shots.lens / shots.src），"
            f"当前只有 {len(LENS)} 组")
    if SHOT_CACHE:
        # 分镜缓存：先把每镜渲成独立文件（命中复用），最后只做一遍叠加合成。
        labels, _ = resolve_srcs(n)          # 只为解析素材引用并做越界校验
        SHOT_FILES = build_shot_cache(n, D, labels)
        # 缓存文件走 mp4，时间基可能是 1/12800；xfade 要求所有输入同一时间基，先统一。
        src_nodes = [f"[{IMG_SLOT0 + i}:v]fps={FPS},settb=1/{FPS},"
                     f"setpts=PTS-STARTPTS,format=yuv420p[shot{i}]" for i in range(n)]
        chains, two_img_chains = [], []
    else:
        SHOT_FILES = []
        labels, src_nodes = resolve_srcs(n)
        # 一镜两图：把该镜拆成前后两段各自平移，中间短溶解；对外仍只输出一个 [shotN]。
        XF2 = 0.30
        two_img_chains = []
        chains = []
        for i in range(n):
            lab = labels[i]
            if isinstance(lab, tuple) and lab[0] == "img2":
                chains.append(shot_chain(lab[1], i, D[i], 0.0, False, False,
                                         half=1, tag=f"shot{i}_a"))
                chains.append(shot_chain(lab[2], i, D[i], 0.0, False, False,
                                         half=2, tag=f"shot{i}_b"))
                two_img_chains.append(
                    f"[shot{i}_a][shot{i}_b]xfade=transition=fade:"
                    f"duration={XF2}:offset={D[i]/2 - XF2/2:.3f}[shot{i}]")
            else:
                chains.append(shot_chain(lab, i, D[i], PHASE_BY_N.get(i, 0.0),
                                         i in IS_MOTION, i in CROP_MOTION))
        chains.extend(two_img_chains)   # 溶解必须排在各段之后，保证节点已定义

    # n-1 次 xfade 串联；重音句用圆形展开，其余按节奏交替溶解/淡入
    xf = []
    prev = "shot0"
    for i in range(1, n):
        out = f"x{i}" if i < n - 1 else "bg_main"
        trans = "circleopen" if (i - 1) in ACCENTS else ("dissolve" if i % 3 == 0 else "fade")
        xf.append(f"[{prev}][shot{i}]xfade=transition={trans}:"
                  f"duration={X_FADE}:offset={T[i - 1]:.3f}[{out}]")
        prev = out
    if n == 1:                          # 单镜头时也要给出 bg_main 标签
        xf.append("[shot0]null[bg_main]")

    # 固定素材槽随图片/动态素材数量后移；--cache 时镜头段直接占 4..4+n-1。
    FIX0 = (IMG_SLOT0 + len(SHOT_FILES) if SHOT_FILES
            else IMG_SLOT0 + len(IMG_PATHS) + max(0, len(MOTION_PATHS) - 1))
    S_FOG_W, S_TALK, S_FOG_C, S_PARTS, S_SHADE, S_SPARK = range(FIX0, FIX0 + 6)

    # ---- 片头（说完即退场，正片完全无人像；沿用 v9/v10）----
    # 真同步：本篇专属口型视频的音视频同源，嘴保持清晰，不做任何柔焦遮挡。
    # 旧篇（无 head_video）沿用 v4 talk_test.mp4，这一行与原版逐字一致。
    talk = (
        f"[{S_TALK}:v]fps={FPS},delogo=x=6:y=6:w=130:h=62,"
        f"scale={HOST_W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={HOST_W}:{H},"
        f"trim=duration={HEAD_LEN:.3f},setpts=PTS-STARTPTS,format=rgba[talk_plain]")
    mask = (f"[2:v]alphaextract,trim=duration={HEAD_LEN:.3f},"
            "setpts=PTS-STARTPTS[mask]")
    # 片头底图任意尺寸都铺满 1920x1080（本篇场景图多为 3:2，不裁会与 xfade 尺寸不符）
    head_bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{H},"
               f"trim=duration={HEAD_LEN:.3f},setpts=PTS-STARTPTS,"
               "format=yuv420p[head_bg]")
    # ---- 片头滤镜：host 叠说书人；title 只保留标题卡底图 ----
    if HEAD_MODE == "host":
        head_extra = [
            talk, mask, head_bg,
            "[talk_plain][mask]alphamerge,format=rgba,"
            f"fade=t=out:st={EXIT_AT:.2f}:d={JOIN_XF:.2f}:alpha=1[talk_rgba]",
            f"[head_bg][talk_rgba]overlay=x={HOST_X}:y=0:format=auto[head]",
        ]
    else:
        head_extra = [head_bg, "[head_bg]null[head]"]

    # 雾层横向宽度按画幅同比取（16:9 = 1920×1.25 = 2400 原值），竖版跟着变窄。
    FW = int(round(W * 1.25))
    fog_w = f"[{S_FOG_W}:v]format=rgba,colorchannelmixer=aa=0.10,scale={FW}:{H}[fog_w]"
    fog_c = f"[{S_FOG_C}:v]format=rgba,colorchannelmixer=aa=0.035,scale={FW}:{H}[fog_c]"
    parts = f"[{S_PARTS}:v]format=rgba,colorchannelmixer=aa=0.32[parts]"

    # ---- 16 枚浮尘光点，各自独立上升 ----
    # overlay 的 eval 默认是 frame，y 表达式里的 t 每帧重新求值。
    # 每枚：固定 x 基准位置 + 各自速度的上升 + 出屏循环；不做左右摆动。
    # 提速：先在一张 960x540 的透明画布上合成 16 枚（光点缩到 48x48、坐标速度同比
    # 减半），再整体放大后只叠一次——16 次全帧叠加变成 16 次半分辨率叠加 + 1 次全帧。
    n_spark = 16
    if SPARK_HALF_RES:
        SP_W, SP_H, SP_S = W // 2, H // 2, 48
        SP_K = 0.5
        spark_canvas = (f"color=c=black@0.00:s={SP_W}x{SP_H}:r={FPS},format=rgba,"
                        "colorchannelmixer=aa=0.0[spbase]")
        prev = "spbase"
    else:
        SP_W, SP_H, SP_S = W, H, 96
        SP_K = 1.0
        spark_canvas = None
        prev = "with_parts"
    spark_split = (f"[{S_SPARK}:v]format=rgba,scale={SP_S}:{SP_S},split={n_spark}"
                   + "".join(f"[s{i}]" for i in range(n_spark)))
    spark_chains = []
    # 光点基准位置按画幅同比换算（16:9 时与原公式逐字相同）。
    for i in range(n_spark):
        bx = (150 + (i * 233) % 1600) * (W / 1920) * SP_K
        by = (90 + (i * 157) % 900) * (H / 1080) * SP_K
        vy = (20 + (i % 4) * 8) * SP_K          # 上升速度 20~44 px/s（半分辨率同比减半）
        x = f"{bx:.1f}"                        # 固定横向位置，只上下走
        y = f"mod({by:.1f}-t*{vy:.1f}+{SP_H},{SP_H})"
        label = f"sp{i + 1}" if i < n_spark - 1 else ("sparked_layer" if SPARK_HALF_RES else "sparked")
        spark_chains.append(
            f"[{prev}][s{i}]overlay=x='{x}':y='{y}':format=auto[{label}]")
        prev = label
    if SPARK_HALF_RES:
        # 半分辨率图层放大后与主画面做唯一一次全帧叠加
        spark_tail = [f"[sparked_layer]scale={W}:{H}:flags=bicubic[spfull]",
                      "[with_parts][spfull]overlay=x=0:y=0:format=auto[sparked]"]
    else:
        spark_tail = []

    # 每次切镜一记 0.14 秒暖白闪；重音句再叠一记更亮的
    switch_at = [t + EXIT_AT for t in T]
    # 没有切点 / 没有重音句时给常量 0，避免 drawbox 收到空表达式
    switches = "+".join(f"between(t,{a:.2f},{a + 0.14:.2f})" for a in switch_at) or "0"
    accents = ("+".join(
        f"between(t,{cues[i][0] + HEAD_LEN:.2f},{cues[i][0] + HEAD_LEN + 0.28:.2f})"
        for i in sorted(ACCENTS) if i < len(cues)) or "0")
    cut_box = (f"drawbox=x=0:y=0:w={W}:h={H}:color=0xFFE2B8@0.085:t=fill:"
               f"enable='{switches}'")
    accent_box = (f"drawbox=x=0:y=0:w={W}:h={H}:color=0xFFE2B8@0.06:t=fill:"
                  f"enable='{accents}'")

    finish = (
        "[shaded]vignette=PI/4.5,"  # 去掉逐帧胶片颗粒，避免静止处看起来在抖
        f"{cut_box},{accent_box},"
        "subtitles=sub_v12.ass,format=yuv420p[v]"
    )

    graph = ";".join([
        *src_nodes,                    # 图片与动态素材的输入节点，数量由 shots.json 决定
        *chains,
        *xf,
        *head_extra,                   # 片头：说书人（host）或纯标题卡（title）
        fog_w, fog_c, parts,
        *([spark_canvas] if spark_canvas else []),
        spark_split, *spark_chains,
        *spark_tail,
        f"[bg_main][fog_w]overlay=x=-240:y=0:format=auto[glow1]",
        f"[glow1][fog_c]overlay=x=-480:"
        f"y='20*sin(t/21)':format=auto[glow2]",
        f"[head][glow2]xfade=transition=fade:duration={JOIN_XF}:"
        f"offset={EXIT_AT:.3f}[all]",
        "[all][parts]overlay=x=-40:"
        f"y='-40+30*sin(t/23)':format=auto[with_parts]",
        f"[sparked][{S_SHADE}:v]overlay=x=0:y=0:format=auto[shaded]",
        finish,
    ])

    # 图片张数完全由 shots.json 决定；不用某张图就从 images 里删掉，不要留 null。
    if any(p is None for p in IMG_PATHS):
        raise RuntimeError("shots.json 的 images 不能有 null；不用的图请直接从列表删掉")
    img_args = [arg for p in IMG_PATHS
                for arg in ("-loop", "1", "-framerate", str(FPS), "-i", str(p))]
    # 槽 1 必须存在才能固定后面的槽号；没有动态素材时用片头底图占位、不进滤镜图
    slot1_args = (["-stream_loop", "-1", "-i", str(MOTION_PATHS[0])]
                  if MOTION_PATHS else
                  ["-loop", "1", "-framerate", str(FPS), "-i", str(V2 / "bg1.jpg")])
    extra_motion_args = [arg for p in MOTION_PATHS[1:]
                         for arg in ("-stream_loop", "-1", "-i", str(p))]
    shot_args = [arg for p in SHOT_FILES for arg in ("-i", str(p))]

    # 雾渐变：1200x540 生成再放大 2x（软渐变看不出差别），原为 2400x1080 逐帧生成。
    if GRAD_HALF_RES:
        gw, gh = 1200, 540
        grad_w = (f"gradients=s={gw}x{gh}:c0=0x101820:c1=0xC8A46A:x0=0:y0=0:"
                  f"x1={gw}:y1={gh}:nb_colors=2:rate={FPS}:duration={limit:.3f}")
        grad_c = (f"gradients=s={gw}x{gh}:c0=0x0C1620:c1=0x6A7E96:x0={gw}:y0=0:"
                  f"x1=0:y1={gh}:nb_colors=2:rate={FPS}:duration={limit:.3f}")
    else:
        grad_w = (f"gradients=s=2400x1080:c0=0x101820:c1=0xC8A46A:x0=0:y0=0:"
                  f"x1=2400:y1=1080:nb_colors=2:rate={FPS}:duration={limit:.3f}")
        grad_c = (f"gradients=s=2400x1080:c0=0x0C1620:c1=0x6A7E96:x0=2400:y0=0:"
                  f"x1=0:y1=1080:nb_colors=2:rate={FPS}:duration={limit:.3f}")

    # 编码器：默认 libx264；--encoder qsv 走 Intel 硬件编码（快约 18%，CPU 占用低）。
    if ENCODER == "qsv":
        qp = PRESET if PRESET in ("veryfast", "faster", "fast", "medium", "slow", "slower") else "medium"
        enc_v = ["-c:v", "h264_qsv", "-preset", qp, "-global_quality", "22", "-pix_fmt", "nv12"]
    else:
        enc_v = ["-c:v", "libx264", "-preset", PRESET, "-crf", "20", "-pix_fmt", "yuv420p"]

    suffix = f"_{meta['id']}" if EP else ""
    out = WORK / (f"test_v12{suffix}.mp4" if MODE == "test" else f"out_v12{suffix}.mp4")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-v", "error",
        "-loop", "1", "-framerate", str(FPS), "-i",
        str((base / HEAD_IMAGE) if HEAD_IMAGE else (V2 / "bg1.jpg")),          # 0 片头底
        *slot1_args,                                                             # 1 第 0 段动态
        "-loop", "1", "-framerate", str(FPS), "-i", str(HOST_MASK),              # 2 片头mask
        "-i", str(AUDIO_SRC if AUDIO_SRC else WORK / "vo_v12.wav"),              # 3 音频（可能已混 BGM）
        *(shot_args if SHOT_FILES else img_args),                                # 4.. 本篇场景图 / 分镜缓存
        *([] if SHOT_FILES else extra_motion_args),                              #    额外动态素材
        "-f", "lavfi", "-i", grad_w,                                            # FIX0 fog_w
        "-i", str(HEAD_VIDEO if HEAD_VIDEO else TALK_DEFAULT),                 # 片头口型：本篇专属 or 当前说书人默认
        "-f", "lavfi", "-i", grad_c,                                            # FIX0+2 fog_c
        "-loop", "1", "-framerate", str(FPS), "-i", str(WORK / "particles.png"),    # FIX0+3
        "-loop", "1", "-framerate", str(FPS), "-i", str(WORK / "caption_shade.png"), # FIX0+4
        "-loop", "1", "-framerate", str(FPS), "-i", str(WORK / "spark.png"),        # FIX0+5
        "-filter_complex", graph,
        "-map", "[v]", "-map", "3:a",
        *enc_v,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{limit:.3f}", "-shortest", "-movflags", "+faststart", str(out),
    ]
    print(f"合成中：{meta['title']} | {limit:.1f}s / 全片 {total:.1f}s | 模式 {MODE} "
          f"| {n} 镜 / {len(IMG_PATHS)} 图 / "
          f"{len(IS_MOTION & set(range(n)))} 动态镜 | "
          f"{'分镜缓存' if SHOT_FILES else '单遍合成'} / {ENCODER} {PRESET}")
    run(cmd)

    if MODE == "test":
        print(f"测试片完成：{out.name}  {probe(out):.1f}s")
        return

    dest = (EP.parent / "成片") if EP else ROOT
    dest.mkdir(parents=True, exist_ok=True)
    # 成片名 = 主题名，不加任何风格后缀；短版仅保留秒数用于区分文件，避免覆盖全片。
    final = (dest / f"{title}_短版{int(SEC)}秒.mp4" if MODE == "short"
             else dest / f"{title}.mp4")
    shutil.move(out, final)
    final.with_suffix(".srt").write_text(build_srt(cues, limit), encoding="utf-8")
    real = probe(final)

    # 逐镜头抽一帧，确保每个镜头的画面真的不同（一次 ffmpeg 批量抽，19 个进程变 1 个）
    stills = [1.0, 4.6] + [b + EXIT_AT + 1.2 for b in bounds[1:-1]] + [max(limit - 1.0, 1.0)]
    stills = [s for s in stills if s < limit - 0.2]
    if stills:
        for old in WORK.glob("still_v12_*.png"):    # 先清旧帧，避免上次多出来的帧混进归档
            old.unlink()
        frames = sorted({int(round(s * FPS)) for s in stills})
        sel = "+".join(f"eq(n,{f})" for f in frames)
        run(["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(final),
             "-vf", f"select='{sel}'", "-fps_mode", "vfr",
             "-start_number", "1", "still_v12_%02d.png"])
        # 自动归档（v2.9.0）：静帧直接跟着成片落在 成片/，不用再手工 cp 出 _render/
        if dest.resolve() != WORK.resolve():
            for f in sorted(WORK.glob("still_v12_*.png")):
                shutil.move(str(f), str(dest / f.name))

    # 评论区钩子（可选）：本篇 script.json 的 discussion；没有则不加该键，产物与旧版一致
    discussion = None
    if EP:
        _sp = EP.parent / "script.json"
        if _sp.exists():
            try:
                discussion = (json.loads(_sp.read_text(encoding="utf-8")) or {}).get("discussion")
            except Exception:
                discussion = None          # 脚本崩坏不影响出片，只是不自带钩子问题

    info = {
        "id": meta["id"], "title": title, "version": "v12",
        "mode": MODE, "duration_sec": round(real, 2),
        "full_duration_sec": round(total, 2), "resolution": f"{W}x{H}", "fps": FPS,
        "changes": meta.get("changes") or [
            "成片直接用主题名命名，不加风格后缀",
            "场景图按统一风格生成：写实电影剧照 / 电影级自然光 / 浅景深 / 写实历史剧质感，画面无文字字幕、避免人物正脸大特写",
            "静态镜默认按 note 的「【景别·主体】」标签自动选运镜（缓推/横移/收尾拉远），并套节奏约束；显式 move 的镜人工优先，动态镜不叠位移",
            "字幕按画幅定字号（横版正文 54px 折行 23 字 / 竖版 48px 折行 18 字），平移在 3x 超采样画布上做再缩回，消除阶梯卡顿",
            "浮尘光点固定 x、只做上升；雾层水平位置固定，仅保留纵向浮动",
            "镜头运动、转场、字幕、片头、音频链路全部沿用既有实现",
        ],
        "shot_cuts_abs_sec": [round(t + EXIT_AT, 3) for t in T],
        "shot_durations_sec": [round(d, 3) for d in D],
        "iteration": "python make_v12.py --test 45 / --sec 30 / 无参数出全片",
        "extra_cost": "0 积分（Pillow/numpy 本地生成纹理 + ffmpeg）",
        "output": final.name, "tools": ["ffmpeg", "ffprobe", "Pillow", "numpy"],
    }
    if discussion:
        info["discussion"] = discussion      # 评论区钩子问题：发布时直接抄进评论首条
    # 项目说明随成片落到 成片/（v2.9.0）；changes 可由 meta.json 的 changes 逐篇覆盖
    info_name = f"项目说明_v12{'_短版' if MODE == 'short' else ''}.json"
    (dest / info_name).write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    if dest.resolve() != WORK.resolve():             # 001 回归时 dest==WORK，不能删刚写的文件
        for old in WORK.glob("项目说明_v12*.json"):   # 清掉 _render/ 里上一版的残留
            old.unlink()
    print(f"完成：{final.name}  {real:.1f}s | {len(stills)} 张逐镜静帧"
          f"（静帧与项目说明已归档到 {dest.name}/）")


if __name__ == "__main__":
    main()

