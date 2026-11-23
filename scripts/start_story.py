# 一键建篇：为任意故事生成「每集专属」数据层，全系列规则沿用技能自带 runtime/series/template.json。
# 复用 runtime 内 new_episode.py 与 head_voice.py 已跑通的链路，不重写渲染引擎。
#
#   python start_story.py --id 001 --title "精卫填海" --lines my_lines.json \
#       --structure 1,3,7,2 --images-dir "D:/workspace/story1/scenes"
#   python start_story.py --id 002 --title "夸父追日" --lines my_lines.json \
#       --images-paths a.jpg b.jpg c.jpg --root "D:/workspace"
#
# 工作目录：--root 或环境变量 STORY_ROOT 指定；两者都没有时用当前工作目录。
# 不依赖《中华上下五千年》项目或任何 manifest；任意故事只需 --title + --lines。
#
# 镜头数 = 旁白句数；图片张数、动态素材段数、动态镜个数全部自由。
# 剧本固定四段顺序：① 3 秒钩子 → ② 背景起因 → ③ 核心冲突 → ④ 结局升华，
# 段内句数自由；script.json 记录四段归属与结尾针，shots.json 带 ending 片尾定帧卡。
#
# 新故事必须使用本篇画面。没给本篇图时 images 留空并标记缺图，引擎会拒绝出正式片。
# 场景图必做：用 --images-dir / --images-paths 给本篇画面（精简包无内置占位图）。
#
# 产出 <root>/episodes/NNN/：meta.json / lines.json / script.json / shots.json /
#                            caption.srt / vo.wav / head_voice.wav / 场景图/ / 成片/

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
RUNTIME = HERE.parent / "runtime"
EXTS = (".jpg", ".jpeg", ".png", ".webp")

# 仅 --placeholder（工程验证）时用，取自 runtime 内置素材，绝不作为成品画面。
# 精简包已删除 001 样例图（shot_*/bg2/bg3），占位模式随之不可用：
# 场景图现为必做投入，请用 --images-dir / --images-paths 给本篇画面。
PLACEHOLDER_IMAGES = []
# 技能包不内置动态素材（对出片无用，且要花积分生成）——动态镜必须自带本篇素材。


def story_root(arg_root):
    """工作目录：命令行 --root > 环境变量 STORY_ROOT > 当前工作目录。"""
    p = arg_root or os.environ.get("STORY_ROOT") or str(pathlib.Path.cwd())
    return pathlib.Path(p).resolve()


def to_cfg_path(path, ep):
    """写进 shots.json 的路径：优先相对本篇目录，否则绝对路径（引擎都支持）。"""
    q = pathlib.Path(path)
    if q.is_absolute():
        q = q.resolve()
    else:
        c = (pathlib.Path.cwd() / q).resolve()
        q = c if c.exists() else (ep / q).resolve()
    try:
        r = q.relative_to(ep.resolve())
        if not str(r).startswith(".."):
            return r.as_posix()
    except ValueError:
        pass
    return q.as_posix()


def collect_own_images(a, ep):
    """本篇场景图：目录按文件名排序，或显式路径列表。返回绝对路径列表。"""
    if a.images_dir:
        d = pathlib.Path(a.images_dir)
        if not d.is_dir():
            raise SystemExit(f"场景图目录不存在：{d}")
        files = sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS)
    elif a.images_paths:
        files = [pathlib.Path(p) for p in a.images_paths]
    else:
        return []
    out = []
    for p in files:
        q = p if p.is_absolute() else (
            pathlib.Path.cwd() / p if (pathlib.Path.cwd() / p).exists() else ep / p)
        q = q.resolve()
        if not q.exists():
            raise SystemExit(f"本篇场景图不存在：{q}")
        out.append(q)
    return out


def parse_dynamic(text, n):
    """把 '2,5' 解析成合法镜头序号列表。"""
    if not text:
        return []
    idx = [int(x.strip()) for x in text.split(",") if x.strip()]
    bad = [i for i in idx if not 0 <= i < n]
    if bad:
        raise SystemExit(f"动态镜序号越界：{bad}（共 {n} 镜，序号 0 起算）")
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="本故事编号/短名，如 001、jingwei；用作 episodes/ 下的目录名")
    ap.add_argument("--title", required=True, help="故事标题（任意故事必填）")
    ap.add_argument("--volume", default="自定义", help="卷名/分类，可自定义，如「远古时代」「寓言」")
    ap.add_argument("--series", default="中华上下五千年",
                    help="系列名（片头卷名行与片尾落款），非本系列故事记得改，如「现代人物志」")
    ap.add_argument("--host", choices=["ancient", "modern"], default="ancient",
                    help="片头说书人：ancient=古装（默认，古代/神话/历史题材）；modern=现代休闲学者（近现代/当代/现实题材）")
    ap.add_argument("--head-mode", choices=["host", "title"], default="host",
                    help="片头形式：host=说书人 5 秒口播（默认）；"
                         "title=只留标题卡+底图、不出现人像，时长不写死 5 秒")
    ap.add_argument("--format", choices=["16:9", "9:16"], default="16:9",
                    help="画幅：16:9 横版（默认）/ 9:16 竖版。竖版片头不出现说书人，"
                         "自动改为标题卡 + 保留口播；字幕按竖屏安全区重排")
    ap.add_argument("--head-len", type=float,
                    help="片头时长（秒）；不给时 host=5.0、title 按文字自适应（有口播跟语音实测、无口播按篇名估）")
    ap.add_argument("--no-head-voice", action="store_true",
                    help="title 模式下关闭片头口播与字幕（默认保留口播与字幕，只是不出现人像）")
    ap.add_argument("--lines", required=True, help="旁白稿 JSON 字符串数组路径；句数自定，一句一镜")
    ap.add_argument("--structure", help="四段各自句数，如 1,3,7,2；不给则按比例推断并标记待复核")
    ap.add_argument("--needle", help="结尾针字卡金句；不给则自动取 lines.json 末句")
    ap.add_argument("--discussion",
                    help="评论区钩子问题（如「换成你，放不放刘邦？」）；写入 script.json 的 discussion，"
                         "出片后随项目说明输出。不给则不写该键")
    ap.add_argument("--tail-hold", type=float, default=2.5,
                    help="片尾定帧卡停留秒数（默认 2.5）")
    ap.add_argument("--cast", nargs="*", default=[],
                    help='人物锚定句（锁造型不锁脸），可多个人物，格式 "名字=造型描述"：'
                         '--cast "郑和=同一人物：深青官袍、黑色幞头、革带、身形清瘦、四十上下"。'
                         '写入 shots.json 的 cast，供写每镜提示词时前置')
    ap.add_argument("--draft-max", type=int, default=13, help="保留参数兼容，通用模式忽略")
    ap.add_argument("--skip-ai-check", action="store_true",
                    help="跳过旁白稿 A 层体检（emoji/弯引号/markdown/套话/半角破折号）。"
                         "默认有硬伤就拒绝建篇：这些字符会进 TTS 与字幕")
    ap.add_argument("--root", help="工作目录；也可用环境变量 STORY_ROOT；默认当前目录")
    ap.add_argument("--images-dir", help="本篇场景图目录，按文件名排序取全部图片")
    ap.add_argument("--images-paths", nargs="*", default=[],
                    help="本篇场景图路径列表（任意张数）")
    ap.add_argument("--images", type=int,
                    help="保留参数兼容；精简包无占位图，实际图片张数以 --images-dir/--images-paths 为准")
    ap.add_argument("--dynamic-at", default="",
                    help="哪些镜用动态素材，0 起算逗号分隔，如 '2,5'；默认无动态镜")
    ap.add_argument("--motions", nargs="*", default=[],
                    help="本篇动态素材路径（可多段）")
    ap.add_argument("--placeholder", action="store_true",
                    help="已废弃：精简包删除了内置样例图，占位模式会报错；请用本篇画面")
    ap.add_argument("--profile", choices=["fast", "quality"], default=None,
                    help="渲染档位：fast=分镜缓存+QSV(可用时)更快；quality=4x 超采样+libx264。"
                         "不给则按默认（3x 超采样 + libx264 + 每次整片重渲）")
    ap.add_argument("--cache", action="store_true",
                    help="开启分镜缓存（改字幕/改片头不用重渲镜头）")
    ap.add_argument("--no-cache", action="store_true", help="关闭分镜缓存")
    ap.add_argument("--encoder", choices=["libx264", "qsv", "auto"],
                    help="编码器：libx264(默认) / qsv(Intel 硬件) / auto(有 QSV 就用)")
    ap.add_argument("--ss", type=int, help="超采样倍数：1~6，默认 3（越大越顺滑也越慢）")
    a = ap.parse_args()

    # 竖版片头不出现说书人：head-mode 自动降级为 title（口播照旧保留），与引擎口径一致。
    if a.format == "9:16" and a.head_mode == "host":
        print("[画幅] 9:16 竖版：片头不出现说书人，自动改为标题卡 + 保留口播")
        a.head_mode = "title"

    # 渲染档位：写进 shots.json 的 render 块，引擎按篇生效（命令行开关仍可覆盖）
    render = {"cache": False, "encoder": "libx264", "ss": 3}
    if a.profile == "fast":
        # 分镜缓存是真正的大头；QSV 只省编码那几秒（实测与 libx264 差别在噪声级），
        # 所以 fast 档不动编码器，画质与默认完全一致，需要时另加 --encoder qsv。
        render = {"cache": True, "encoder": "libx264", "ss": 3}
    elif a.profile == "quality":
        render = {"cache": False, "encoder": "libx264", "ss": 4}
    if a.cache:
        render["cache"] = True
    if a.no_cache:
        render["cache"] = False
    if a.encoder:
        render["encoder"] = a.encoder
    if a.ss:
        render["ss"] = max(1, min(6, a.ss))

    if not (RUNTIME / "series/new_episode.py").exists():
        raise SystemExit(f"技能不完整：缺少 runtime/series/new_episode.py（{RUNTIME}）")
    sys.path.insert(0, str(RUNTIME / "series"))
    import new_episode as ne
    import head_voice as hv

    root = story_root(a.root)
    lines_path = pathlib.Path(a.lines)
    if not lines_path.is_absolute() and not lines_path.exists():
        # 相对路径也允许相对 --root 找
        alt = root / lines_path
        if alt.exists():
            lines_path = alt
    lines = json.loads(pathlib.Path(lines_path).read_text(encoding="utf-8"))
    if not isinstance(lines, list) or not lines:
        raise SystemExit("旁白稿必须是非空 JSON 字符串数组")

    # ---- 旁白稿 A 层体检（emoji/弯引号/markdown/套话/半角破折号）----
    # 这些字符会同时污染 TTS（念乱码）与 ASS 字幕（缺字形出方块），所以默认硬拦；
    # B 层（排比/称呼/情感标签）与 C 层（判断锋利度）机器判不了，仍靠人工。
    if not a.skip_ai_check:
        sys.path.insert(0, str(HERE))
        import check_ai_patterns as aic
        hard_n, warn_n, reports_ai = aic.run(lines, a.format)
        for i, hard, warn, text in reports_ai:
            for h in hard:
                print(f"[稿检·硬伤] 句{i + 1}：{h}  ← {text[:32]}")
            for w in warn:
                print(f"[稿检·提示] 句{i + 1}：{w}")
        if hard_n:
            raise SystemExit(
                f"旁白稿体检未通过：{hard_n} 处硬伤。先改稿再建篇"
                "（emoji / markdown / 弯引号 / 套话 / 半角破折号会进 TTS 与字幕）；"
                "确实要强行建篇加 --skip-ai-check。")
        print(f"[稿检] 0 处硬伤 / {warn_n} 处提示，通过（提示项人工判断）"
              if warn_n else "[稿检] 0 处硬伤，通过")

    meta = {"id": a.id, "volume": a.volume, "title": a.title,
            "series": a.series, "source": "用户提供"}
    ep = root / "episodes" / a.id
    (ep / "场景图").mkdir(parents=True, exist_ok=True)
    (ep / "成片").mkdir(parents=True, exist_ok=True)

    n = len(lines)                                   # 镜头数由旁白句数决定
    dyn = parse_dynamic(a.dynamic_at, n)
    own_images = collect_own_images(a, ep)
    placeholder = a.placeholder and not own_images

    # ---- 四段式剧本 + 结尾针金句 ----
    script = ne.build_script(ep, meta, lines, a.structure, a.needle,
                             getattr(a, "discussion", None))
    for w in script["warnings"]:
        print(f"[剧本提示] {w}")

    # ---- 本篇画面 ----
    if own_images:
        images = [to_cfg_path(p, ep) for p in own_images]
        n_img = len(images)
        if n_img < n:
            print(f"[提示] {n} 个镜头只有 {n_img} 张本篇图，会复用画面；建议补到一镜一图")
        img_note = "本篇画面，待按情节补写提示词"
        status = f"本篇素材已就位：{n_img} 张本篇场景图"
        todo_img = []
    elif placeholder:
        # 精简包已删除内置 001 样例图，占位模式无素材可用。
        raise SystemExit(
            "占位模式（--placeholder）在精简包中不可用：内置样例图已删除。\n"
            "场景图是必做投入，请用 --images-dir 或 --images-paths 给本篇画面后重新建篇。")
    else:
        n_img = 0
        images = []
        img_note = "本篇画面待生成"
        status = "缺本篇画面：images 为空，正式渲染会被拦截"
        todo_img = [f"生成 {n} 张本篇场景图（需授权花积分），放入 场景图/ 后用 fill_shots.py 填入"]
        print(f"[待补素材] 本篇画面尚未生成，已留出 {n} 个镜头位；正式渲染会被拦截")

    # ---- 本篇动态素材：技能包不内置，必须自带 ----
    motions = [to_cfg_path(p, ep) for p in a.motions]
    if dyn and not motions:
        raise SystemExit(
            f"指定动态镜 {dyn} 却没有本篇动态素材：请给 --motions 指向本篇素材，"
            "或去掉 --dynamic-at 做纯静态片（推荐）")
    todo_dyn = []

    (ep / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (ep / "lines.json").write_text(
        json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")

    chars = sum(len(s.strip()) for s in lines)
    img_word = "本篇图" if own_images else ("占位图" if placeholder else "待补图位")
    print(f"《{meta['title']}》（{a.id}）：{n} 句 / {chars} 字"
          f" / {n_img} 张{img_word} / {len(dyn)} 个动态镜")
    print(f"  四段 {script['structure']}"
          f"{'（按比例推断，待人工复核）' if script['auto'] else ''}"
          f" / 钩子 {script['hook_chars']} 字 / 金句 {script['needle_chars']} 字")
    total, cues = ne.build_audio_and_caption(ep, lines)

    # 景别默认模板取自 runtime/series/lens_default.json（内容与 001 的表一致）；
    # 001 样板图已随精简包移除，不再读它的 shots.json。循环复用，可在 shots.json 逐镜改。
    lens_file = RUNTIME / "series/lens_default.json"
    if lens_file.exists():
        base_lens = json.loads(lens_file.read_text(encoding="utf-8"))["lens"]
    else:
        base_lens = [s["lens"] for s in json.loads(
            (RUNTIME / "episodes/001/shots.json").read_text(encoding="utf-8"))["shots"]]

    shots = []
    for i in range(n):
        lens = list(base_lens[i % len(base_lens)])
        lens[5] = False
        item = {
            "n": i,
            "beat": ne.beat_for(script["segments"], i),
            "src": f"img:{i}" if n_img == 0 else f"img:{i % n_img}",
            "lens": lens,
            "note": img_note,
        }
        if i in dyn:
            k = dyn.index(i)
            item.update({
                "src": f"motion:{k % len(motions)}",
                "motion": True,
                "phase": round(0.4 + (k % 4) * 1.5, 2),
                "crop16x9": True,
                "note": "本篇动态素材",
            })
        shots.append(item)

    accents = sorted({i for i in (2, 5, n - 1) if 0 <= i < n})
    # 片头底图默认取本篇中间那张（避开钩子镜与结尾镜，构图通常最开阔）；
    # 没给图时留空，等 fill_shots.py 填图后自动补，或用 --head-image 指定。
    head_image = images[len(images) // 2] if images else None
    cfg = {
        "id": meta["id"], "title": meta["title"], "volume": meta["volume"],
        "host": a.host,
        "format": a.format,
        "head": {"mode": a.head_mode, "len": a.head_len,
                 "voice": (a.head_mode == "host") or (not a.no_head_voice)},
        "engine": "v12",
        "render": render,
        "status": status,
        "paths_relative_to": "本文件所在目录",
        "motions": motions,
        "images": images,
        "head_image": head_image,
        "accents": accents,
        "shots": shots,
        "ending": ne.default_ending(a.needle, a.tail_hold),
        "head_text": hv.TEMPLATE.format(title=meta["title"]),
        "todo": [
            "渲染前确认旁白稿已定稿",
            *todo_img,
            *todo_dyn,
            "按情节填每个镜头的 note（beat 已按四段归属生成）",
            "复核 script.json 的四段区间" if script["auto"] else "确认 script.json 四段区间无误",
            "本篇素材就位后渲 --test 45 与 --tail 8 抽帧检查",
        ],
    }
    # 人物锚定句：可选。给了就写进 cast，供写每镜提示词时前置（锁造型不锁脸，
    # 详见 references/workflow.md「主人翁出镜节奏」）。
    if a.cast:
        casts = []
        for s in a.cast:
            name, _, anchor = s.partition("=")
            name = name.strip() or "主人翁"
            anchor = anchor.strip() or s.strip()
            casts.append({"name": name, "anchor": anchor})
        cfg["cast"] = casts
    (ep / "shots.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    # 片头只有标题卡且关掉口播时没有音轨，不必合成片头 TTS
    hv_path = None
    if a.head_mode == "host" or not a.no_head_voice:
        hv_path = hv.ensure(ep, cfg["head_text"])
    # 导演稿.md：把分镜/旁白/素材/备注汇成一份可读文档（零积分，可随时重跑刷新）。
    ne.write_director_doc(ep)

    engine = RUNTIME / "episodes/001/v12/make_v12.py"
    helper = HERE / "fill_shots.py"
    # 片头时长：显式给了就用；host 默认 5.0；title 有口播时按语音实测（与引擎同一口径）
    if a.head_len is not None:
        head_est = a.head_len
    elif a.head_mode == "host":
        head_est = 5.0
    elif not a.no_head_voice:
        spk = hv.speech_span(pathlib.Path(hv_path))[1] if hv_path else 3.0
        head_est = min(4.2, max(2.6, spk + 0.95))
    else:
        head_est = min(3.6, max(2.0, 0.9 + 0.21 * len(meta["title"])))
    print(f"\n  音频 {total:.1f}s / {cues} 条字幕 / 预计成片 {total + head_est:.1f}s"
          f"（片头 {head_est:.2f}s）")
    print(f"  产出：{ep}")
    print(f"  画幅：{a.format}"
          + ("（横版 1920x1080，说书人可选）" if a.format == "16:9"
             else "（竖版 1080x1920，片头无人像、字幕按竖屏安全区重排）"))
    print(f"  状态：{status}")
    print(f"  结尾针：金句「{script['needle']}」+ 片尾定帧卡 {a.tail_hold}s")
    if a.head_mode == "host":
        print("  片头：说书人 5 秒口播（head_voice.wav，原说书人音色、默认不变速）")
    elif not a.no_head_voice:
        print(f"  片头：标题卡+底图，无人像；保留口播与字幕（{head_est:.2f}s 跟着语音走，不再固定 5 秒）")
    else:
        print(f"  片头：只有标题卡+底图，无人像、无口播（{head_est:.2f}s，配 head.len 可改）")
    if head_image:
        print(f"  片头底图 -> {head_image}（默认中间那张本篇图，可改 shots.json.head_image）")
    if a.head_mode == "host":
        print(f"  片头说书人：{a.host}（{'古装' if a.host == 'ancient' else '现代休闲学者'}）")
    print("\n  下一步：")
    flag_txt = ""
    if render["cache"]:
        flag_txt += " --cache"
    if render["encoder"] != "libx264":
        flag_txt += f' --encoder {render["encoder"]}'
    if render["ss"] != 3:
        flag_txt += f' --ss {render["ss"]}'
    print(f"  渲染档：{a.profile or '默认'}（缓存 {'开' if render['cache'] else '关'} / "
          f"{render['encoder']} / {render['ss']}x 超采样）")
    if not own_images:
        print(f'    python "{helper.as_posix()}" --id {a.id} --images-dir <本篇场景图目录> --root "{root.as_posix()}"')
    print(f'    python "{engine.as_posix()}" --episode "{(ep / "shots.json").as_posix()}" --test 45{flag_txt}')
    print(f'    python "{engine.as_posix()}" --episode "{(ep / "shots.json").as_posix()}" --tail 8{flag_txt}')


if __name__ == "__main__":
    main()
