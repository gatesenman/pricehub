#!/usr/bin/env python3
"""Regenerate PriceHub brand icons (PNG + multi-size ICO)."""
from __future__ import annotations
import math
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "static" / "icons"
INST = ROOT / "windows" / "installer"
BLUE = (0, 122, 204, 255)

def make(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = max(1, size // 16)
    rad = max(2, size * 22 // 100)
    d.rounded_rectangle((pad, pad, size - 1 - pad, size - 1 - pad), radius=rad, fill=BLUE)
    cx, cy = size / 2, size / 2
    s = size * 0.62
    pts_local = [(-0.48, 0.0), (-0.22, -0.28), (0.42, -0.28), (0.42, 0.28), (-0.22, 0.28)]
    ang = math.radians(-18)
    cos_a, sin_a = math.cos(ang), math.sin(ang)
    def xf(x, y):
        x, y = x * s, y * s
        return cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a
    d.polygon([xf(x, y) for x, y in pts_local], fill=(255, 255, 255, 255))
    hx, hy = xf(-0.34, 0.0)
    hr = max(1.2, size * 0.055)
    d.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=BLUE)
    nodes_local = [(0.02, -0.10), (0.22, 0.00), (0.02, 0.10), (0.28, -0.14)]
    nodes = [xf(x, y) for x, y in nodes_local]
    lw = max(1, size // 28)
    for a, b in [(0, 1), (1, 2), (1, 3), (0, 2)]:
        d.line([nodes[a], nodes[b]], fill=BLUE, width=lw)
    nr = max(1.5, size * 0.045)
    for nx, ny in nodes:
        d.ellipse((nx - nr, ny - nr, nx + nr, ny + nr), fill=BLUE)
    return im

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    INST.mkdir(parents=True, exist_ok=True)
    make(512).save(OUT / "pricehub-512.png")
    icons = []
    for sz in (16, 32, 48, 64, 128, 256):
        im = make(sz)
        im.save(OUT / f"pricehub-{sz}.png")
        icons.append(im)
    icons[-1].save(OUT / "pricehub.ico", format="ICO", sizes=[(i.width, i.height) for i in icons], append_images=icons[:-1])
    (OUT / "favicon.ico").write_bytes((OUT / "pricehub.ico").read_bytes())
    (INST / "PriceHub.ico").write_bytes((OUT / "pricehub.ico").read_bytes())
    make(256).save(INST / "PriceHub.png")
    win = ROOT / "windows" / "PriceHub.ico"
    win.write_bytes((OUT / "pricehub.ico").read_bytes())
    ext = ROOT / "extension" / "pricehub-clipper"
    if ext.is_dir():
        make(16).save(ext / "icon16.png")
        make(48).save(ext / "icon48.png")
        make(128).save(ext / "icon128.png")
    print("wrote", OUT, "and", INST / "PriceHub.ico")

if __name__ == "__main__":
    main()
