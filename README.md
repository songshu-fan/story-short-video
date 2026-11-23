# story-short-video · 故事短片生产线

把一份中文旁白稿，直接变成可发布的**横屏 / 竖屏**故事短片。

**「故事转视频」里成本最低的做法**：除本篇场景图与一张片头/封面专属图要花 ImageGen 积分，TTS、四段式分镜、一句一镜、卡拉 OK 字幕、片头（说书人 / 无人像标题卡）、结尾金句定帧卡、三平台封面**全链路本地零积分**。画面运动默认按 `note` 标签自动推断（缓推缓拉 / 单向横移 / 完全不动，带节奏约束），也可逐镜手选，动态镜独立处理不叠位移；字幕正文按画幅置为 54 / 48px；写稿自带**去 AI 味体检**（`check_ai_patterns.py`，有硬伤建篇直接退出）；宫格出图自带**格位×镜号×旁白句对照清单**（`panel_map.py`，防「画面整体滞后一句」的静默错位），出片时静帧与项目说明自动归档到 `成片/`。改文案只重烧字幕，分镜缓存命中即秒级复用。

## 它做什么

一份旁白稿进来，一支成片出去：

1. **写稿**：四段式（钩子 → 起因 → 冲突 → 升华），钩子 ≤15 字抛结果，末句 ≤20 字当片尾金句；配套 12 条文案自检（钩子双轨、反转密度、蝴蝶效应细节、选题三点法）。
2. **建篇**：TTS 三路并发合成旁白与片头音轨，自动生成镜头表（一句一镜）、字幕、导演稿。
3. **出图**：按镜头情节出本篇场景图；支持单图 / 两宫格 / 四格，宫格自动切分去白边；另有片头/封面专属横图（竖版则竖图）。
4. **出片**：1920x1080 / 1080x1920、25fps，字幕自动折行，片头 + 正片 + 金句定帧卡 + 三平台封面。

## 画幅两档

| 画幅 | 尺寸 | 片头 |
|---|---|---|
| 横版 16:9 | 1920x1080 | 说书人口播 / 无人像标题卡 可选 |
| 竖版 9:16 | 1080x1920 | 固定无人像标题卡（口播保留），字幕按竖屏安全区重排 |

## 环境要求

- Windows / macOS + Python 3
- `edge-tts`、`Pillow`、`numpy`
- `ffmpeg` / `ffprobe`：在 PATH 即可，也可用 `FFMPEG_BIN` 指定；Windows 常见位置 `C:\ffmpeg\bin` 会自动探测

先跑自检：

```bash
python scripts/check_env.py            # 加 --check-voice 可联网复核片头音色
```

## 三步上手

```bash
# 1. 写旁白稿：纯字符串 JSON 数组，见 assets/lines_example.json
# 2. 建篇
python scripts/start_story.py --id jingwei --title "精卫填海" --volume "神话" \
    --lines my_lines.json --structure 1,4,8,3 \
    --images-dir "scenes/" --root "D:/work" --profile quality

# 3. 补画面 → 出片
python scripts/split_panels.py --grids-dir "原图/" --panels 2 --out "episodes/jingwei/场景图"
python scripts/fill_shots.py --id jingwei --images-dir "episodes/jingwei/场景图" --root "D:/work"
python runtime/episodes/001/v12/make_v12.py --episode ".../shots.json" --test 45   # 45 秒验片
python runtime/episodes/001/v12/make_v12.py --episode ".../shots.json"            # 全片
```

一键串完（建篇 → 填图 → 测试 → 尾针 → 全片 → 封面）：

```bash
python scripts/run_episode.py --id jingwei --title "精卫填海" --lines my_lines.json \
    --images-dir "scenes/" --root "D:/work" --profile fast --full --cover
```

## 目录

```
SKILL.md                 技能正文：开工确认、五步流程、硬约束、验收清单
CHANGELOG.md             版本说明
assets/                  旁白稿样例
scripts/                 check_env / start_story / fill_shots / split_panels / head_cover / run_episode / make_cover
references/              workflow · shot_schema · pitfalls · copywriting
runtime/                 自包含运行时：v12 渲染引擎、series 脚本、说书人素材、景别模板
```

## 出图提示词（技能内置三段串）

```
写实电影剧照，电影级自然光，浅景深，写实历史剧质感，无文字，无字幕，避免人物正脸大特写；
画面自带电影光影层次，拒绝过度干净、空洞廉价感，氛围感直接拉满；
[有人物时] 自带原生真实肌理，保留毛孔、轻微细纹、自然瓷感，拒绝塑胶假面，还原真人原生皮肤；
[人物锚定句]；[本镜情节]
```

## 已知边界

- 不内置场景图与动态素材：场景图必须用本篇画面，否则引擎拒绝出正式片。
- 说书人口型默认「内置视频换音轨」近似同步；需要真同步要用 VideoGen 生成专属口型视频（花积分）。
- 画面默认只做平滑上下浮动，不做手持抖动 / 逐帧颗粒（曾被否，别再加回来）。需要换运镜时逐镜写 `shots[].move`：`zoom` 缓推缓拉 / `pan_x` 单向横移（限远景空镜、≤3% 画宽）/ `pan_y` 可调幅度。
- **动态镜不做任何位移**：素材自带运镜，再叠引擎浮动会双重运动；动态镜只取帧、裁 16:9、压暗、镜像、定时长，也不走 3x 超采样。

## 版本

当前 **v2.9.0**（2026-11-24）。版本规则：主版本＝能力范围变化、次版本＝流程/脚本功能增改、修订号＝文档修正。






