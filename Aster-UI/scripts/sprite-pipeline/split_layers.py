"""Split each mood sprite into head/body layers at the neck for puppet animation.

Neck detection: per-row alpha width restricted to the central column band
(arms are lateral; the neck dip is central). Feathered alpha ramp across the
seam so small head rotations show no gap. Outputs full-frame layer PNGs +
sprite metadata (pivot, head bbox) as a generated TypeScript module.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image

SRC = Path(__file__).parent / "sprites"
OUT = SRC / "layers"
OUT.mkdir(exist_ok=True)

MOODS = ["calm", "working", "alert", "music", "success",
         "pouty", "jumping", "thinking", "confused", "tired"]

# Manual seam overrides (fraction of sprite height) for poses where the
# central-band minimum is ambiguous. Filled in after QA if needed.
SEAM_OVERRIDE: dict[str, float] = {"confused": 0.40}

# Celebration poses (arms raised through the neck line) animate as whole-sprite
# bounces instead of head/body puppets — no clean seam exists.
UNSPLIT = {"success", "jumping"}

FEATHER = 14  # px, alpha ramp half-width across the seam

meta = {}
for mood in MOODS:
    img = Image.open(SRC / f"{mood}.png").convert("RGBA")
    a = np.asarray(img).astype(np.float32)
    H, W = a.shape[:2]
    alpha = a[..., 3]
    solid = alpha > 10

    # central band width profile
    x0, x1 = int(W * 0.30), int(W * 0.70)
    profile = solid[:, x0:x1].sum(axis=1).astype(np.float32)
    # smooth
    k = 9
    kern = np.ones(k) / k
    smooth = np.convolve(profile, kern, mode="same")

    if mood in UNSPLIT:
        # whole-sprite pose: body = full sprite, no head layer
        Image.fromarray(a.astype(np.uint8)).save(OUT / f"{mood}_body.png")
        ys_s, xs_s = np.where(solid)
        meta[mood] = {
            "w": W, "h": H, "split": False,
            "pivotX": 0.5, "pivotY": 1.0,
            "headTop": round(float(ys_s.min()) / H, 4),
            "headCx": round(float(xs_s.mean()) / W, 4),
        }
        print(f"{mood:10s} unsplit (whole-sprite animation)")
        continue

    if mood in SEAM_OVERRIDE:
        seam = int(SEAM_OVERRIDE[mood] * H)
    else:
        lo, hi = int(H * 0.35), int(H * 0.72)
        seam = lo + int(np.argmin(smooth[lo:hi]))

    # pivot x = centroid of solid pixels around the seam row
    band = solid[max(0, seam - 4): seam + 4, :]
    xs = np.where(band.any(axis=0))[0]
    pivot_x = float(xs.mean()) if xs.size else W / 2

    # feathered ramp: 1 above seam-FEATHER -> 0 below seam+FEATHER
    y = np.arange(H, dtype=np.float32)[:, None]
    t = np.clip((y - (seam - FEATHER)) / (2 * FEATHER), 0, 1)
    head_w = 1 - (t * t * (3 - 2 * t))  # smoothstep, 1 at top -> 0 below seam
    body_w = 1 - head_w

    head = a.copy()
    head[..., 3] = alpha * head_w
    body = a.copy()
    body[..., 3] = alpha * body_w

    Image.fromarray(head.astype(np.uint8)).save(OUT / f"{mood}_head.png")
    Image.fromarray(body.astype(np.uint8)).save(OUT / f"{mood}_body.png")

    # head bbox top + centroid x for particle anchoring
    head_solid = head[..., 3] > 10
    ys_h, xs_h = np.where(head_solid)
    head_top = int(ys_h.min()) if ys_h.size else 0
    head_cx = float(xs_h.mean()) if xs_h.size else W / 2

    meta[mood] = {
        "w": W, "h": H, "split": True,
        "seam": round(seam / H, 4),
        "pivotX": round(pivot_x / W, 4),
        "pivotY": round(seam / H, 4),
        "headTop": round(head_top / H, 4),
        "headCx": round(head_cx / W, 4),
    }
    print(f"{mood:10s} seam={seam}/{H} ({seam/H:.2f})  pivotX={pivot_x/W:.2f}")

(OUT / "meta.json").write_text(json.dumps(meta, indent=2))

# QA sheet: head tinted red, body tinted blue, seam line drawn
tile_h = 300
cols = len(MOODS)
sheet = Image.new("RGB", (220 * cols, tile_h + 20), (30, 30, 36))
for i, mood in enumerate(MOODS):
    b_img = np.asarray(Image.open(OUT / f"{mood}_body.png")).astype(np.float32)
    H, W = b_img.shape[:2]
    comp = np.zeros((H, W, 3), np.float32)
    comp[..., 2] += b_img[..., 3] * 0.9          # body -> blue
    comp[..., 1] += b_img[..., 3] * 0.15
    if meta[mood]["split"]:
        h_img = np.asarray(Image.open(OUT / f"{mood}_head.png")).astype(np.float32)
        comp[..., 0] += h_img[..., 3] * 0.9      # head -> red
        comp[..., 1] += h_img[..., 3] * 0.15
        seam_row = int(meta[mood]["seam"] * H)
        comp[seam_row - 1: seam_row + 1, :, 1] = 255
    tile = Image.fromarray(np.clip(comp, 0, 255).astype(np.uint8))
    tile.thumbnail((210, tile_h))
    sheet.paste(tile, (i * 220 + (220 - tile.size[0]) // 2, 10))
sheet.save(OUT / "_qa_sheet.png")
print("QA sheet written")
