#!/usr/bin/env python3
"""按帧数筛出 stickers/ 里的真动图（多帧 GIF），写 animated-stickers.json。

BQB 没有单独的动态分类，.gif 后缀也不能当动态用（很多只是单帧图），
所以随机配图优先动态时靠这份清单。路径存相对仓库根目录的形式。
用法：python3 scripts/build-animated-index.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STICKERS = os.path.join(ROOT, "stickers")
OUT = os.path.join(ROOT, "animated-stickers.json")
EXTS = (".gif", ".jpg", ".jpeg", ".png", ".webp")


def main():
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("需要 Pillow：pip install Pillow")
    animated, single_gif, static = [], 0, 0
    for base, _ds, files in os.walk(STICKERS):
        if ".git" in base:
            continue
        for fn in files:
            if not fn.lower().endswith(EXTS):
                continue
            p = os.path.join(base, fn)
            if os.path.getsize(p) < 1024:
                continue
            if fn.lower().endswith(".gif"):
                try:
                    n = getattr(Image.open(p), "n_frames", 1)
                except Exception:
                    n = 1
                if n > 1:
                    animated.append(os.path.relpath(p, ROOT))
                else:
                    single_gif += 1
            else:
                static += 1
    animated.sort()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(animated, f, ensure_ascii=False, indent=0)
    print(f"真动图：{len(animated)} 张；单帧 GIF：{single_gif} 张；静态：{static} 张")
    print(f"已写 {OUT}")


if __name__ == "__main__":
    main()
