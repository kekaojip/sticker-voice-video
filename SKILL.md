---
name: sticker-voice-video
description: Turn text into a captioned sticker voiceover video: TTS narration, stickers that change every 2 clauses (animated GIFs first), clause-by-clause subtitles synced to the voice, 1280x720 horizontal. 文案 → 配音 → 表情包 → 字幕 → 横屏成片，一条命令出片。Use when the user asks for a sticker voiceover video, meme dub video, 表情包配音视频, 黑底视频, or 必剪同款.
---

# Sticker Voice Video

Turn copy into a finished horizontal video: black background, one sticker
in the centre that changes as the narration goes on, big white subtitles
at the bottom, a disclaimer at the top right. 1280x720, one command.

![Preview](assets/preview-1.jpg)

## Quick start

```bash
# 1. Get stickers (ChineseBQB, 41 categories, ~3250 images)
bash scripts/fetch-stickers.sh
python3 scripts/build-animated-index.py

# 2. Write a project file (see example-project.json)
# 3. Render
python3 meme_video.py project.json --out out.mp4
```

Requires: Python 3.10+, ffmpeg (libx264 + ASS subtitles), Noto Sans CJK.

## Project file

```json
{
  "size": "horizontal",
  "voice": "volc-sajiao",
  "disclaimer": "本视频仅供娱乐，无营销属性",
  "segments": [
    {"text": "第一句文案……", "sticker": "stickers/mine/panda.jpg"},
    {"text": "第二句文案……"}
  ]
}
```

- `segments[].text`: one sentence of copy. Each sentence gets its own
  audio, its own subtitle track, and its own stickers.
- `segments[].sticker` (optional): pin a sticker for that whole sentence.
  Without it, stickers are picked randomly (see below).
- `segments[].audio` (optional): bring your own narration for that
  sentence (recorded audio, or audio exported from another app). When
  present, no TTS is called for that sentence. This is the most reliable
  way to use a voice the TTS provider does not have.
- `sticker_every` (optional, default `2`): how many subtitle clauses
  share one sticker. Stickers change mid-sentence, about every 2 clauses,
  so one long sentence never sits on a single sticker.
- `seed` (optional): fixes the random sticker picks. `pool` (optional):
  restrict the sticker pool, e.g. `["chinesebqb"]`.

## Subtitles

- Split by punctuation into short clauses (，、。；：！？…); each clause
  is its own subtitle event, appearing when the narration reaches it.
  A whole sentence never appears at once and waits to be read.
- Clause timing is estimated by character count within the sentence's
  audio duration. Pauses at commas in TTS line up with the split, so it
  is roughly in sync. This is not word-level alignment.
- Each sentence lasts its narration duration + 0.4 s tail pause.

## Stickers

- Random picks are animated-first: true multi-frame GIFs first
  (preferably 10+ frames, which look lively), static images only as
  backup. `.gif` alone does not mean animated — single-frame GIFs exist.
- No repeats within one video. The log prints every pick, so a pick can
  be pinned later by copying its path into that sentence's `sticker`.
- Sticker images are NOT in this repo (copyright + size). Fetch them with
  `scripts/fetch-stickers.sh` or point `pool` at your own folder.
- Note: Tom & Jerry, Pikachu, SpongeBob and other characters in the
  sticker packs are copyrighted. A disclaimer does not remove the risk.

## Voiceover

- No `audio` on a sentence → TTS. Point your TTS command at an env var:
  `SVV_VOLC_TTS` (e.g. Volcengine seed-tts-2.0) or `SVV_EDGE_TTS`
  (edge-tts). The command must accept
  `--text-file <txt> --output <mp3> --speaker <id>`; it may include an
  interpreter, e.g. `python3 /path/to/tts.py`.
- Built-in voice names: `volc-sajiao`, `volc-tianmei`, `volc-liufei`,
  `edge-xiaomeng`. You can also pass a speaker ID directly as `voice`:
  an `S_` ID (your own cloned voice, seed-icl-2.0) or a `zh_` preset ID.
  A cloned voice only works with the key of the account that cloned it.
- If no TTS command is configured and no `audio` is given, the script
  fails loudly. It never silently swaps in another voice.

## Facts that save debugging

- GIF stickers need `-ignore_loop 0` (ffmpeg 8's gif decoder rejects
  `-loop`); static images use `-loop 1`.
- ASS alignment: 9 = top right (the disclaimer), 2 = bottom centre
  (subtitles). 3 is bottom right, not top right.
- Only horizontal 1280x720 is supported. This video style is horizontal
  only, by design.
