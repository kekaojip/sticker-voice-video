#!/usr/bin/env python3
"""sticker-voice-video：文案分句 -> 配音 -> 表情包每两句分句切 -> 底部字幕 -> 成片。

一句话一个表情包、黑底、底部大字字幕，横屏 1280x720。

用法：
  python3 meme_video.py project.json --out out.mp4
  python3 meme_video.py project.json --out out.mp4 --voice edge-xiaomeng

project.json 格式：
  {
    "size": "horizontal",          // 横屏 1280x720（默认；这个视频只做横屏）
    "voice": "volc-sajiao",        // 默认。见 VOICES；也可每句单独指定 voice
    "segments": [
      {"text": "凌晨三点深圳福田，三十块青旅床位，我悟了！",
       "sticker": "stickers/mine/panda.jpg",  // 相对 project.json，或相对本仓库
       "audio": "my-voice-s0.mp3"},           // 可选：自备配音，有它就不走 TTS
      {"text": "下一句……"}                    // 不写 sticker 就随机配（优先动态）
    ]
  }

配音：
- 每句可单独给 "audio"（自己录的、剪映导出的、别的工具合成的都行），
  有 audio 就不走 TTS，这是最稳的路。
- 没给 audio 时走 TTS：把你的 TTS 命令配到环境变量里再跑，
  SVV_VOLC_TTS（火山 seed-tts-2.0 等）或 SVV_EDGE_TTS（edge-tts）。
  命令要接受 `--text-file <txt> --output <mp3> --speaker <id>`。
  没配命令又没给 audio 时脚本直接报错，不静默换音色。
- 剪映里的克隆音色（比如“曼波”）锁在剪映 App 里导不出来，任何外部
  工具都拿不到它；想用它只能在剪映里生成音频后用 "audio" 接进来。

表情包：
- 先跑 scripts/fetch-stickers.sh 拉 ChineseBQB 的精选分类到 stickers/，
  再跑 scripts/build-animated-index.py 生成动图清单。
- 随机配图优先真动图（多帧 GIF）：BQB 没有单独的动态分类，分类按角色
  分，.gif 后缀也不能当动态用（很多只是单帧图），得按帧数筛。
- 表情包图片本身不进仓库（版权 + 体积），用的时候自己拉。

字幕对齐（可选，默认开）：
- 每句配音跑本地 Coli（SenseVoice）实测字音时间戳，分句按真实 onset
  出字幕；没装 Coli 或对不上的那句自动按字数比例估，日志里写明。
- 装 Coli：Node.js 20+，npm install -g @marswave/coli@0.0.20
  （首跑会下载约 155MB 识别模型）。--align off 关掉，--align on 则失败直接报错。
"""
import argparse
import difflib
import hashlib
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import textwrap

MEME_DIR = os.path.dirname(os.path.abspath(__file__))
# TTS 命令从环境变量来（见文件头 docstring）；没配就不走 TTS。
VOLC_TTS = os.environ.get("SVV_VOLC_TTS", "")
EDGE_TTS = os.environ.get("SVV_EDGE_TTS", "")
# 字幕对齐（可选）：本地 Coli 识别。装法见 README“字幕对齐”；
# COLI_BIN 环境变量可指定可执行文件位置，否则找 PATH 里的 coli。
COLI_BIN = os.environ.get("COLI_BIN", "")
COLI_MODEL = "sensevoice"

VOICES = {
    # 火山 seed-tts-2.0 预设（走 SVV_VOLC_TTS 命令）
    "volc-sajiao": ("volc", "zh_female_sajiaoxuemei_uranus_bigtts"),
    "volc-liufei": ("volc", "zh_male_liufei_uranus_bigtts"),
    "volc-tianmei": ("volc", "zh_female_tianmeixiaoyuan_uranus_bigtts"),
    # edge-tts 免费（走 SVV_EDGE_TTS 命令）
    "edge-xiaomeng": ("edge", "zh-CN-XiaomengNeural"),
    "edge-xiaoxiao": ("edge", "zh-CN-XiaoxiaoNeural"),
    "edge-yunyang": ("edge", "zh-CN-YunyangNeural"),
}

SIZES = {
    # 横屏是这套模板的唯一默认（2026-10-27 用户亲口定：这个视频只能做横屏）。
    # vertical 只是代码里留着的备用，不主动用。
    # 版式 v2（2026-10-28 用户定：表情包放大居中、字幕紧贴在表情包下面、
    # 字放大）：表情包按 box_w x box_h 等比适配、垂直居中；字幕用 Alignment 8
    # 顶对齐，每句的 MarginV 按该句表情包底部 + 16 算（见 main 里的 sub_top）。
    "horizontal": {"w": 1280, "h": 720,  "box_w": 680, "box_h": 500,
                   "font": 64, "wrap": 16},
    "vertical":   {"w": 1080, "h": 1920, "box_w": 700, "box_h": 900,
                   "font": 72, "wrap": 10},
}
TAIL = 0.4  # 每句结束后留的停顿（秒），贴近剪映里句子间的自然停顿
# 字幕字体：环境变量 SVV_FONT 优先，其次常见的 Noto Sans CJK Bold 位置。
FONT = os.environ.get("SVV_FONT", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")

# 随机配图池（2026-10-27 从 ChineseBQB 拉到本地的 33 个分类 + 原有包）。
# 不写 sticker 的句子就从这里随机挑，一条视频内不重复。真人、名人、政治类
# 分类没拉下来，不会随机到。想指定某句的图，照旧在那句写 "sticker"。
# 随机配图池：stickers/ 下的目录。先跑 scripts/fetch-stickers.sh 拉 BQB，
# 自己再往 huaji / user / mine 里放图。真人、名人、政治类分类不要拉。
POOL_DIRS = {
    "chinesebqb": os.path.join(MEME_DIR, "stickers", "chinesebqb"),
    "huaji": os.path.join(MEME_DIR, "stickers", "huaji"),
    "user": os.path.join(MEME_DIR, "stickers", "user"),
    "mine": os.path.join(MEME_DIR, "stickers", "mine"),
}
POOL_EXTS = (".gif", ".jpg", ".jpeg", ".png", ".webp")


def build_pool(names):
    pool = []
    for name in names:
        base = POOL_DIRS[name]
        if not os.path.isdir(base):
            continue
        for root, _ds, files in os.walk(base):
            if ".git" in root:
                continue
            for fn in files:
                if fn.lower().endswith(POOL_EXTS):
                    p = os.path.join(root, fn)
                    if os.path.getsize(p) >= 1024:
                        pool.append(p)
    return sorted(pool)


def animated_pool(pool, min_frames=0):
    """从候选池里筛出真动图（多帧 GIF）。BQB 那 831 张有现成清单
    （animated-stickers.json）；其它包现场用 PIL 验帧数，验不了就按
    .gif 扩展名算。注意：很多 .gif 只是单帧图，扩展名不能当动态用。
    min_frames>0 时只留帧数够多的（动作幅度大、看着更灵动）。"""
    pool_set = set(pool)
    anim = []
    cache = os.path.join(MEME_DIR, "animated-stickers.json")
    if os.path.exists(cache):
        try:
            anim = [os.path.join(MEME_DIR, p)
                    for p in json.load(open(cache, encoding="utf-8"))]
            anim = [p for p in anim if p in pool_set]
        except Exception:
            anim = []
    try:
        from PIL import Image
        for p in pool:
            if p in anim or not p.lower().endswith(".gif"):
                continue
            try:
                if getattr(Image.open(p), "n_frames", 1) > 1:
                    anim.append(p)
            except Exception:
                pass
    except ImportError:
        anim += [p for p in pool if p.lower().endswith(".gif")
                 and p not in anim]
    if min_frames > 0:
        try:
            from PIL import Image
            lively = []
            for p in anim:
                try:
                    if getattr(Image.open(p), "n_frames", 1) >= min_frames:
                        lively.append(p)
                except Exception:
                    pass
            return sorted(set(lively))
        except ImportError:
            return sorted(set(anim))
    return sorted(set(anim))


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path], text=True).strip()
    return float(out)


def probe_size(path):
    """图片/动图原始宽高（第一路视频流）。"""
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0",
        path], text=True).strip()
    w, h = out.split(",")
    return int(w), int(h)


def fit_box(w, h, bw, bh):
    """等比放进 bw x bh 的盒子，返回缩放后宽高。"""
    s = min(bw / w, bh / h)
    return max(1, int(w * s)), max(1, int(h * s))


def find_coli():
    for cand in (COLI_BIN, shutil.which("coli")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _extract_json(text):
    """coli 首跑会在 stdout 混进模型下载进度；只取第一个 { 到最后一个 }。"""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("coli 没返回 JSON")
    return json.loads(text[a:b + 1])


def transcribe_coli(audio, cache_dir):
    """跑本地 Coli 得字音时间戳（tokens + onset），按音频 sha256 缓存。
    返回 (asr_dict, None)；失败返回 (None, 原因)，调用方按字数估兜底，
    这里不抛错。"""
    try:
        h = hashlib.sha256()
        with open(audio, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        jf = os.path.join(cache_dir, f"align-{h.hexdigest()[:16]}.json")
        if os.path.exists(jf) and os.path.getsize(jf) > 0:
            asr = json.load(open(jf, encoding="utf-8"))
        else:
            coli = find_coli()
            if not coli:
                return None, "coli 未安装"
            r = subprocess.run([coli, "asr", "-j", "--model", COLI_MODEL,
                                "--language", "zh", audio],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return None, f"coli 退出码 {r.returncode}：{r.stderr.strip()[-200:]}"
            asr = _extract_json(r.stdout)
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(asr, f, ensure_ascii=False)
        toks, times, dur = asr.get("tokens"), asr.get("timestamps"), asr.get("duration")
        if (not isinstance(toks, list) or not toks
                or not isinstance(times, list) or len(toks) != len(times)
                or type(dur) not in (int, float) or dur <= 0
                or any(type(t) not in (int, float) for t in times)
                or any(t < 0 or t >= dur for t in times)
                or any(times[i] < times[i - 1] for i in range(1, len(times)))):
            return None, "coli 返回的时间戳无效"
        return asr, None
    except Exception as e:  # noqa: BLE001 - 对齐失败一律兜底，不拦出片
        return None, f"对齐异常：{e}"


def align_clauses(text, clauses, asr):
    """把分句边界对到 ASR token 的 onset 时间戳上（相对音频开头，秒）。
    返回 [(start, end)]（末句 end 是音频时长，调用方再延到分段末尾）；
    对不上返回 None，调用方按字数估兜底。ASR 偶有同音错字（轮->伦）和
    丢标点，用 difflib 按字对齐，不要求逐字全等。"""
    toks, times, dur = asr["tokens"], asr["timestamps"], asr["duration"]
    ref = "".join(text.split())
    hyp = "".join(toks)
    if not ref or not hyp:
        return None
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    if sm.ratio() < 0.75:
        return None
    ref2tok = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                ref2tok[i1 + k] = j1 + k
    if not ref2tok:
        return None
    # 每个分句在 ref 里的起始位置（去空格后的字数累计）
    starts, pos = [], 0
    for c in clauses:
        starts.append(pos)
        pos += len("".join(c.split()))
    if pos > len(ref):
        return None
    mapped = sorted(ref2tok)

    def tok_at(p):
        if p in ref2tok:
            return ref2tok[p]
        nxt = [m for m in mapped if m > p]
        return ref2tok[nxt[0]] if nxt else None

    bounds, prev = [], -1.0
    for k, s in enumerate(starts):
        ti = tok_at(s)
        if ti is None:
            return None
        st = float(times[ti])
        if st < prev or st >= dur:
            return None
        if k == len(starts) - 1:
            en = float(dur)
        else:
            ti2 = tok_at(starts[k + 1])
            if ti2 is None:
                return None
            en = float(times[ti2])
            if en <= st:
                return None
        bounds.append((st, en))
        prev = st
    return bounds


def wrap_text(text, width):
    """按字符数硬断行（中文无空格，按字切即可），返回 ASS 用的 \\N 连接串。"""
    lines = []
    for para in text.split("\n"):
        lines.extend(textwrap.wrap(para, width=width) or [""])
    return r"\N".join(lines)


def synth_volc(text, speaker, cache_dir):
    key = hashlib.sha256(f"volc|{speaker}|{text}".encode()).hexdigest()[:16]
    mp3 = os.path.join(cache_dir, f"volc-{key}.mp3")
    if os.path.exists(mp3) and os.path.getsize(mp3) > 0:
        print("cached", mp3, flush=True)
        return mp3
    txt = os.path.join(cache_dir, f"volc-{key}.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write(text)
    if not VOLC_TTS:
        raise RuntimeError("没配火山 TTS 命令：设环境变量 SVV_VOLC_TTS 指向你的 "
                           "TTS 命令，或给这句配 audio 自备配音")
    run(shlex.split(VOLC_TTS) + ["--text-file", txt,
         "--output", mp3, "--speaker", speaker])
    return mp3


def synth_edge(text, voice, cache_dir):
    key = hashlib.sha256(f"edge|{voice}|{text}".encode()).hexdigest()[:16]
    mp3 = os.path.join(cache_dir, f"edge-{key}.mp3")
    if os.path.exists(mp3) and os.path.getsize(mp3) > 0:
        print("cached", mp3, flush=True)
        return mp3
    if not EDGE_TTS:
        raise RuntimeError("没配 edge-tts 命令：设环境变量 SVV_EDGE_TTS 指向你的 "
                           "TTS 命令，或给这句配 audio 自备配音")
    txt = os.path.join(cache_dir, f"edge-{key}.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write(text)
    run(shlex.split(EDGE_TTS) + ["--text-file", txt,
         "--output", mp3, "--speaker", voice])
    if not os.path.exists(mp3) or os.path.getsize(mp3) == 0:
        raise RuntimeError("edge-tts 没出音频，改用别的音色或自备 audio")
    return mp3


def synth(text, voice_name, cache_dir):
    # 除了 VOICES 里的名字，也可以直接填 speaker ID：
    # S_ 开头的是火山复刻音色（走 seed-icl-2.0），zh_ 开头的是火山预设音色。
    # 复刻音色只在克隆它的那个火山账号上可用，别人的 key 调了会报未授权。
    if voice_name not in VOICES:
        if voice_name.startswith("S_") or voice_name.startswith("zh_"):
            return synth_volc(text, voice_name, cache_dir)
        raise ValueError(f"未知的 voice：{voice_name}（见 VOICES，或直接填 speaker ID）")
    kind, voice = VOICES[voice_name]
    if kind == "volc":
        return synth_volc(text, voice, cache_dir)
    return synth_edge(text, voice, cache_dir)


def resolve(path, base):
    if os.path.isabs(path):
        return path
    for cand in (os.path.join(base, path),
                 os.path.join(MEME_DIR, path)):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(f"找不到表情包：{path}（相对 {base} 找过）")


CLAUSE_SEP = "，、。；：！？…"


def split_clauses(text, max_len):
    """把一句话按标点切成短分句（标点留前一句），再把超长的分句按字数切短。
    字幕一句只显示一个分句，跟着配音一段一段出，不整屏一次全显示。"""
    clauses, buf = [], ""
    for ch in text:
        buf += ch
        if ch in CLAUSE_SEP:
            clauses.append(buf)
            buf = ""
    if buf:
        clauses.append(buf)
    out = []
    for c in clauses:
        while len(c) > max_len:
            out.append(c[:max_len])
            c = c[max_len:]
        if c:
            out.append(c)
    return out or [text]


def group_bounds(groups, adur, dur):
    """按字数比例算每组分句在分段里的起止（秒）；最后一组留到分段结束（含尾停）。"""
    total = sum(len(c) for g in groups for c in g) or 1
    bounds, acc = [], 0
    for gi, g in enumerate(groups):
        st = adur * acc / total
        acc += sum(len(c) for c in g)
        en = adur * acc / total
        if gi == len(groups) - 1:
            en = dur
        bounds.append((st, en))
    return bounds


def build_ass(segments, timing, audio_durs, cfg, ass_path,
              clause_bounds=None, sub_tops=None):
    """timing: [(start, end)] 每句分段的起止（秒，含 0.4 秒尾停）；
    audio_durs: 每句配音本身的时长（不含尾停）。
    clause_bounds: 每句的实测分句起止 [(start, end)]（相对配音开头，秒），
    有它就按实测出字幕，没有的那句按字数比例估（老路）。最后一句分句
    两种路都留到分段结束（含尾停）。
    sub_tops: 每句字幕顶边的 y（像素，相对画面顶）；字幕用 Alignment 8
    顶对齐，逐句贴在该句表情包下面。不给则贴底（MarginV 0 的老位置）。"""
    w, h = cfg["w"], cfg["h"]
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Sub,Noto Sans CJK SC,%d,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2.5,1,8,40,40,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n" % (w, h, cfg["font"])
    )
    def ts(s):
        cs = int(round(s * 100))
        return "%d:%02d:%02d.%02d" % (cs // 360000, (cs // 6000) % 60,
                                      (cs // 100) % 60, cs % 100)
    evs = []
    for si, ((st, en), seg, adur) in enumerate(zip(timing, segments, audio_durs)):
        mv = sub_tops[si] if sub_tops and si < len(sub_tops) else 0
        clauses = split_clauses(seg["text"], cfg["wrap"])
        cb = clause_bounds[si] if clause_bounds and si < len(clause_bounds) else None
        if cb and len(cb) == len(clauses):
            # 实测对齐：分句起止来自 ASR token onset；末句仍留到分段结束
            for k, c in enumerate(clauses):
                cst, cen = st + cb[k][0], st + cb[k][1]
                if k == len(clauses) - 1:
                    cen = en
                evs.append("Dialogue: 0,%s,%s,Sub,,0,0,%d,,%s\n"
                           % (ts(cst), ts(cen), mv, wrap_text(c, cfg["wrap"])))
            continue
        total = sum(len(c) for c in clauses) or 1
        acc = 0
        for k, c in enumerate(clauses):
            cst = st + adur * acc / total
            acc += len(c)
            cen = st + adur * acc / total
            if k == len(clauses) - 1:
                cen = en  # 最后一句分句留到分段结束（含 0.4 秒尾停）
            evs.append("Dialogue: 0,%s,%s,Sub,,0,0,%d,,%s\n"
                       % (ts(cst), ts(cen), mv, wrap_text(c, cfg["wrap"])))
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(head + "".join(evs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", choices=list(SIZES))
    ap.add_argument("--voice", choices=list(VOICES))
    ap.add_argument("--work", default=None)
    ap.add_argument("--seed", type=int, default=None,
                    help="随机配图的种子；不给则每次随机不同")
    ap.add_argument("--align", choices=["auto", "on", "off"], default="auto",
                    help="字幕对齐：auto=默认用 Coli 实测时间戳，失败按字数估并打日志；"
                         "on=失败直接报错；off=只按字数估")
    args = ap.parse_args()

    proj_path = os.path.abspath(args.project)
    proj_dir = os.path.dirname(proj_path)
    proj = json.load(open(proj_path, encoding="utf-8"))
    size = args.size or proj.get("size", "horizontal")
    cfg = SIZES[size]
    default_voice = args.voice or proj.get("voice", "volc-sajiao")
    segments = proj["segments"]
    assert segments, "segments 为空"

    # 表情包：每 sticker_every（默认 2）句分句换一张，句内也换，不让一张
    # 表情包顶着一整句长文案。没写 sticker 的句子按组随机挑，一条视频内
    # 不重复，打印出来；下次想固定某句就把打印出来的路径抄回那句的
    # "sticker"（整句都用它）。
    group_n = max(1, int(proj.get("sticker_every", 2)))
    for seg in segments:
        clauses = split_clauses(seg["text"], cfg["wrap"])
        seg["_groups"] = [clauses[i:i + group_n]
                          for i in range(0, len(clauses), group_n)]
        seg["_stickers"] = ([seg["sticker"]] * len(seg["_groups"])
                            if seg.get("sticker")
                            else [None] * len(seg["_groups"]))
    missing_groups = sum(s["_stickers"].count(None) for s in segments)
    if missing_groups:
        pool = build_pool(proj.get("pool", ["chinesebqb", "huaji", "user", "mine"]))
        if len(pool) < missing_groups:
            raise RuntimeError(f"表情包池只有 {len(pool)} 张，不够 {missing_groups} 组随机用")
        rng = random.Random(proj.get("seed", args.seed))
        # 优先动态：先从真动图里抽，动图不够再拿静态补，最后打乱顺序
        picks = []
        if proj.get("prefer_animated", True):
            anim = animated_pool(pool)
            # 先抽帧数多的（>=10 帧，动作幅度大、看着灵动），不够再拿其它动图补
            lively = animated_pool(pool, min_frames=10)
            n_lively = min(missing_groups, len(lively))
            picks += rng.sample(lively, n_lively)
            rest_anim = [p for p in anim if p not in picks]
            picks += rng.sample(rest_anim,
                                min(missing_groups - len(picks), len(rest_anim)))
            print(f"动态池：{len(anim)} 张真动图（其中 {len(lively)} 张 10 帧以上），"
                  f"本次用 {len(picks)} 张", flush=True)
        rest = [p for p in pool if p not in picks]
        picks += rng.sample(rest, missing_groups - len(picks))
        rng.shuffle(picks)
        it = iter(picks)
        for seg in segments:
            for gi, st in enumerate(seg["_stickers"]):
                if st is None:
                    pick = next(it)
                    seg["_stickers"][gi] = pick
                    print(f"随机配图：{''.join(seg['_groups'][gi])[:16]}…… -> {pick}",
                          flush=True)

    work = args.work or os.path.join(proj_dir, "work")
    cache = os.path.join(work, ".cache")
    os.makedirs(cache, exist_ok=True)
    clips_dir = os.path.join(work, f"clips-{size}")  # 按尺寸分开，换尺寸不复用错版分段
    os.makedirs(clips_dir, exist_ok=True)

    # 1. 配音（每句独立合成，时长 = 该句字幕/表情包的时长，不用对齐模型）
    timing, audio_durs, clip_paths, clause_bounds, sub_tops = [], [], [], [], []
    t = 0.0
    for i, seg in enumerate(segments):
        voice = seg.get("voice", default_voice)
        if seg.get("audio"):
            audio = resolve(seg["audio"], proj_dir)
        else:
            audio = synth(seg["text"], voice, cache)
        adur = probe_duration(audio)
        audio_durs.append(adur)
        dur = adur + TAIL
        timing.append((t, t + dur))
        t += dur

        # 字幕对齐（默认开）：用 Coli 实测每句分句的起止，失败则按字数估
        seg["_align"] = None
        if args.align != "off":
            asr, err = transcribe_coli(audio, cache)
            if asr is None:
                msg = f"seg{i}: 字幕对齐=按字数估（{err}）"
                if args.align == "on":
                    raise RuntimeError(msg)
                print(msg, flush=True)
            else:
                bounds = align_clauses(seg["text"],
                                       split_clauses(seg["text"], cfg["wrap"]), asr)
                if bounds is None:
                    msg = f"seg{i}: 字幕对齐=按字数估（ASR 文本与文案对不上）"
                    if args.align == "on":
                        raise RuntimeError(msg)
                    print(msg, flush=True)
                else:
                    seg["_align"] = bounds
                    print(f"seg{i}: 字幕对齐=实测（Coli，{len(bounds)} 分句）",
                          flush=True)
        clause_bounds.append(seg["_align"])

        stickers = [resolve(s, proj_dir) for s in seg["_stickers"]]
        bounds = group_bounds(seg["_groups"], adur, dur)
        # 版式 v2：表情包等比放进 box_w x box_h 的盒子，整段（表情包+字幕）
        # 垂直居中，字幕贴在表情包下面 16px。先算每组表情包的显示尺寸，
        # 再算整段的盒顶和字幕顶边，字幕顶边留给 build_ass 用。
        sizes = [fit_box(*probe_size(s), cfg["box_w"], cfg["box_h"])
                 for s in stickers]
        hmax = max(h for _, h in sizes)
        line_h = cfg["font"] + 6
        lmax = max(wrap_text(c, cfg["wrap"]).count("\\N") + 1
                   for g in seg["_groups"] for c in g)
        sub_h = lmax * line_h
        box_top = (cfg["h"] - (hmax + 16 + sub_h)) // 2
        sub_tops.append(box_top + hmax + 16)
        # 分段文件名带上表情包+分界+时长+版式 hash：换图/换文案/换版式不复用旧分段
        clip_key = hashlib.sha256(
            ("|".join(stickers + [f"{a:.3f}-{b:.3f}" for a, b in bounds])
             + f"|{dur:.3f}|{size}|{cfg['box_w']}x{cfg['box_h']}|"
               f"{cfg['font']}|{cfg['wrap']}").encode()).hexdigest()[:10]
        clip = os.path.join(clips_dir, f"seg{i:02d}-{clip_key}.mp4")
        if not os.path.exists(clip):
            # 每组分句一个表情包输入，用 enable 按时间段叠上去；配音只有
            # 一个输入不切，换图不断音。
            inputs = []
            for s in stickers:
                # GIF 用 gif 解码器的 -ignore_loop 循环播放；静态图用 -loop 1。
                # （ffmpeg 8 的 gif 解码器不认 -loop，静态图那套会直接报错。）
                if s.lower().endswith(".gif"):
                    inputs += ["-ignore_loop", "0", "-i", s]
                else:
                    inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", s]
            n = len(stickers)
            parts = [f"[{k}:v]fps=30,scale={sizes[k][0]}:{sizes[k][1]}:"
                     f"flags=lanczos[st{k}]" for k in range(n)]
            parts.append(f"color=c=black:s={cfg['w']}x{cfg['h']}:r=30:d={dur:.3f}[bg]")
            prev = "bg"
            for k, (a, b) in enumerate(bounds):
                y = box_top + (hmax - sizes[k][1]) // 2
                parts.append(f"[{prev}][st{k}]overlay=(W-w)/2:{y}:"
                             f"enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
                prev = f"v{k}"
            parts.append(f"[{prev}]format=yuv420p[v]")
            run(["ffmpeg", "-y", "-v", "error"] + inputs + [
                 "-i", audio,
                 "-filter_complex", ";".join(parts),
                 "-map", "[v]", "-map", f"{n}:a",
                 "-af", f"apad=whole_dur={dur:.3f}",
                 "-t", f"{dur:.3f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                 clip])
        clip_paths.append(clip)
        print(f"seg{i}: {dur:.2f}s {seg['text'][:20]}... 表情包x{len(stickers)}",
              flush=True)

    # 2. 拼接
    concat_list = os.path.join(work, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for c in clip_paths:
            f.write(f"file '{c}'\n")
    joined = os.path.join(work, "joined.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", concat_list, "-c", "copy", joined])

    # 3. 烧字幕（底部大字），一次重编码出片
    ass = os.path.join(work, "subs.ass")
    build_ass(segments, timing, audio_durs, cfg, ass, clause_bounds, sub_tops)
    run(["ffmpeg", "-y", "-v", "error", "-i", joined,
         "-vf", f"ass={ass}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "copy", "-movflags", "+faststart", args.out])

    total = probe_duration(args.out)
    print(f"OK: {args.out}  {cfg['w']}x{cfg['h']}  {total:.1f}s  "
          f"{len(segments)} 句，配音={default_voice}")


if __name__ == "__main__":
    main()
