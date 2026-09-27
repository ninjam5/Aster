# Stage 2 — Demote Pixel Fallback (OmniParser/YOLO) + Florence Cleanup

**Status:** CODE COMPLETE + QA rounds clean (2026-09-25); A/B evidence and the
default flip are deferred to a live run (see `stage4.md` promotion mechanics)
**Flags:** `USE_PIXEL_FALLBACK` (default **true** until evidence, then false)

## Scope

- Gate Track-2 (`_yolo_track`, `_yolo_candidates`, `_release_omniparser` call
  sites) behind `config.USE_PIXEL_FALLBACK` in `tools/vision.py`.
- Delete the dead Florence reservation (`config.py` `ICON_CAPTIONER` value +
  `tools/vision.py` notice branch); it was never wired.
- A/B harness: `engine_testing/locator_eval.py` with the fallback ON vs OFF
  across the target matrix.

## Predicted numbers

| Metric | Prediction |
|---|---|
| Pixel track fires (matrix) | < 10% of targets |
| DOM+UIA+OCR coverage | ≥ 90% |
| Time saved per avoided YOLO call | 2–8 s |
| Transient VRAM avoided | ~1.5 GB |

## QA checklist

```powershell
python engine_testing/qa/run_stage.py 2
python -m pytest tests/test_locator.py -v
```

- [ ] pytest green (incl. the `_YOLO_SCORE_THRESHOLD` regression guards)
- [ ] locator_eval A/B report written to `engine_testing/results/`
- [ ] no behavior change on DOM-addressable targets
- [ ] residual no-DOM target list documented
- [ ] Florence references removed repo-wide (grep sweep)
- [ ] owner sign-off before flipping the default

## Exit criteria

- [ ] A/B evidence meets predictions
- [ ] fallback default flipped to false (or owner keeps true with rationale)
- [ ] Track-2 code still present, lazy, VRAM-released (deletion only in stage 4)
