"""Drop stray alpha blobs (background remnants) — keep components >= 2% of largest."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

OUT = Path(__file__).parent / "sprites"
NAMES = ["calm", "working", "alert", "music", "success",
         "pouty", "jumping", "thinking", "confused", "tired"]

meta = {}
for name in NAMES:
    p = OUT / f"{name}.png"
    img = Image.open(p).convert("RGBA")
    a = np.asarray(img).copy()
    solid = a[:, :, 3] > 0
    labels, n = ndimage.label(solid)
    if n > 1:
        sizes = ndimage.sum_labels(np.ones_like(labels), labels, range(1, n + 1))
        keep = {i + 1 for i, s in enumerate(sizes) if s >= sizes.max() * 0.02}
        drop_mask = ~np.isin(labels, list(keep)) & solid
        if drop_mask.any():
            a[drop_mask] = 0
            print(f"{name}: dropped {int(drop_mask.sum())} stray px in {n - len(keep)} blob(s)")
    img = Image.fromarray(a)
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    img.save(p)
    meta[name] = {"w": img.size[0], "h": img.size[1]}
    print(f"{name:10s} final {img.size}")

(OUT / "meta.json").write_text(json.dumps(meta, indent=2))
print("done")
