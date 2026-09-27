# Aster sprite pipeline

One-time asset bake that turns the mood sheet (`2d-aster.png` at the repo root)
into the layered character sprites in `Aster-UI/src/assets/aster/`. Re-run it
only when the source art changes.

Dependencies (global pip, per project policy):

```
python -m pip install pillow numpy scipy rembg onnxruntime
```

Run from this directory, in order:

```
python extract_sprites.py   # slice the 5x2 grid, remove backgrounds (rembg/isnet-anime)
python cleanup_sprites.py   # drop stray background blobs, trim, write meta.json
python fix_music.py         # surgical fixes specific to the music bust sprite
python split_layers.py      # cut head/body puppet layers at the neck + QA sheet
```

Outputs land in `./sprites/` and `./sprites/layers/`. Then:

1. Eyeball `sprites/layers/_qa_sheet.png` — head tinted red, body blue, seam in
   green. Fix bad seams via `SEAM_OVERRIDE` (fraction of sprite height) or add
   poses with arms crossing the neck line to `UNSPLIT` in `split_layers.py`.
2. Copy `sprites/layers/*_head.png` / `*_body.png` into
   `Aster-UI/src/assets/aster/`.
3. Update the metadata table in `Aster-UI/src/character/sprites.ts` from
   `sprites/layers/meta.json` (pivot/anchor fractions + dimensions).

The hero image is separate: `hero-scene.jpg` is `3d-aster.png` downscaled to
1920px wide, JPEG quality 84.
