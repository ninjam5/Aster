# Stage 0 — Baseline & QA Scaffolding

**Status:** COMPLETE (2026-09-25)
**Behavior change:** none (scaffolding only)

## What was done

- Baseline `pytest` recorded.
- Playwright 1.62.0 confirmed present (client for CDP attach; no browser launch).
- QA runner + artifact structure created (`engine_testing/qa/`).
- Static fixture site created (`engine_testing/fixtures/site/`): index.html,
  form.html — accessible names are the test surface, not pixels.
- Target matrix fixed: fixtures + File Explorer/Notepad/Calculator (UIA) +
  Wikipedia read-only (web) + Discord-web (gated stretch, owner-approved runs).

## Commands & baseline numbers

```powershell
python -m pytest tests/ -q      # 287 passed  (skill docs said 118 — stale)
python engine_testing/qa/run_stage.py 0
```

Artifacts: `engine_testing/qa/artifacts/stage0/`.

## Exit criteria

- [x] pytest baseline recorded (287 passed, 0 failed)
- [x] runner + artifact pipeline proven on unchanged code
- [x] fixture site present
- [x] flags added to `config.py` + `self_config.example.yaml` **default OFF / current behavior**

## Notes / deferred to live-system QA

- `engine_testing/locator_eval.py` requires a live desktop + models; its
  baseline run is deferred to the Stage 1 live QA (owner-present) and will be
  appended here when captured. Static code paths are covered offline.
