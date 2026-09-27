"""Surgical cleanup of music.png: grass strip (bottom-left) + note remnant (right of face)."""
from pathlib import Path

import numpy as np
from PIL import Image

p = Path(__file__).parent / "sprites" / "music.png"
img = Image.open(p).convert("RGBA")
a = np.asarray(img).astype(np.int16)
h, w = a.shape[:2]
r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]

# 1) grass/tan strip in bottom-left: kill non-blue-dominant pixels there
#    (hoodie is strongly blue: b > r and b > g)
region = np.zeros((h, w), bool)
region[460:, :120] = True
grass = region & (al > 0) & ~((b > r + 10) & (b > g + 5))
a[grass] = 0
print("grass px killed:", int(grass.sum()))

# 2) translucent white note remnant right of the face: semi-alpha, desaturated
region2 = np.zeros((h, w), bool)
region2[200:360, 390:] = True
sat = np.max(a[..., :3], axis=2) - np.min(a[..., :3], axis=2)
smudge = region2 & (al > 0) & (al < 235) & (sat < 40) & (np.min(a[..., :3], axis=2) > 140)
a[smudge] = 0
print("smudge px killed:", int(smudge.sum()))

out = Image.fromarray(a.astype(np.uint8))
bbox = out.getbbox()
if bbox:
    out = out.crop(bbox)
out.save(p)
print("final size:", out.size)
