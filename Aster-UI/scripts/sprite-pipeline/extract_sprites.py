"""Slice 2d-aster.png (5x2 mood grid) into panels, remove backgrounds, save sprites.

Panel detection: the grid gutter is a flat slate-blue; within each nominal cell,
find the contiguous block of rows/cols that are mostly non-gutter -> panel rect.
Background removal: rembg (isnet-anime), then alpha cleanup + bbox trim.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SRC = Path(__file__).resolve().parents[3] / "2d-aster.png"
OUT = Path(__file__).parent / "sprites"
PANELS = OUT / "panels"
OUT.mkdir(exist_ok=True)
PANELS.mkdir(exist_ok=True)

NAMES = [
    ["calm", "working", "alert", "music", "success"],
    ["pouty", "jumping", "thinking", "confused", "tired"],
]

img = Image.open(SRC).convert("RGB")
arr = np.asarray(img).astype(np.int16)
H, W, _ = arr.shape
print(f"source {W}x{H}")

gutter = arr[2, 2].copy()
print("gutter color:", gutter)

def non_gutter_mask(a):
    return (np.abs(a - gutter).sum(axis=2) > 60)

mask = non_gutter_mask(arr)

rows, cols = 2, 5
cell_h, cell_w = H / rows, W / cols

from scipy import ndimage

panel_boxes = {}
for r in range(rows):
    for c in range(cols):
        y0, y1 = int(r * cell_h), int((r + 1) * cell_h)
        x0, x1 = int(c * cell_w), int((c + 1) * cell_w)
        sub = mask[y0:y1, x0:x1]
        # largest connected non-gutter blob = the panel illustration
        # (caption letters are small separate blobs; in-panel gutter-ish
        # pixels are just holes inside the big blob)
        labels, n = ndimage.label(sub)
        if n == 0:
            raise RuntimeError(f"no panel found in cell {r},{c}")
        sizes = ndimage.sum_labels(np.ones_like(labels), labels, range(1, n + 1))
        biggest = int(np.argmax(sizes)) + 1
        ys, xs = np.where(labels == biggest)
        box = (x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1)
        name = NAMES[r][c]
        panel_boxes[name] = box
        panel = img.crop(box)
        panel.save(PANELS / f"{name}.png")
        print(f"{name:10s} box={box} size={panel.size}")

# --- background removal ---
from rembg import remove, new_session

try:
    session = new_session("isnet-anime")
    print("using isnet-anime")
except Exception as e:
    print("isnet-anime failed, falling back to u2net:", e)
    session = new_session("u2net")

meta = {}
for r in range(rows):
    for c in range(cols):
        name = NAMES[r][c]
        panel = Image.open(PANELS / f"{name}.png")
        cut = remove(panel, session=session).convert("RGBA")
        a = np.asarray(cut).copy()
        # kill faint halo pixels, solidify confident ones
        alpha = a[:, :, 3].astype(np.float32)
        alpha[alpha < 40] = 0
        a[:, :, 3] = alpha.astype(np.uint8)
        cut = Image.fromarray(a)
        bbox = cut.getbbox()
        if bbox:
            cut = cut.crop(bbox)
        cut.save(OUT / f"{name}.png")
        meta[name] = {"w": cut.size[0], "h": cut.size[1]}
        print(f"cut {name:10s} -> {cut.size}")

(OUT / "meta.json").write_text(json.dumps(meta, indent=2))

# contact sheet for visual QA (checker background to reveal alpha)
tile = 280
sheet = Image.new("RGB", (tile * 5, tile * 2), (40, 40, 48))
checker = Image.new("RGB", (tile, tile), (60, 60, 70))
for y in range(0, tile, 20):
    for x in range(0, tile, 20):
        if (x // 20 + y // 20) % 2 == 0:
            checker.paste((85, 85, 95), (x, y, min(x + 20, tile), min(y + 20, tile)))
for r in range(rows):
    for c in range(cols):
        name = NAMES[r][c]
        s = Image.open(OUT / f"{name}.png")
        s.thumbnail((tile - 16, tile - 16))
        cell = checker.copy()
        cell.paste(s, ((tile - s.size[0]) // 2, (tile - s.size[1]) // 2), s)
        sheet.paste(cell, (c * tile, r * tile))
sheet.save(OUT / "_contact_sheet.png")
print("done")
