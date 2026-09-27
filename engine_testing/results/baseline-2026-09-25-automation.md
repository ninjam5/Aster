# Automation Baseline — 2026-09-25

Point-in-time baseline before the DOM-first automation stages. Do not rewrite
(this repo uses dated logs as its substitute for git history).

## Machine / environment

- Python 3.12.8 (global packages — no venv)
- Playwright **1.62.0** (client only for CDP attach; browser not launched by tests)
- Repo has no git commits (`master` unborn) — this log is the baseline record

## Measured

| Suite | Command | Result |
|---|---|---|
| Full test suite | `python -m pytest tests/ -q` | **287 passed**, 6 warnings, 37.2 s |
| DOM motor harness (new, stage 1) | `python dom-motor-test.py` | **23/23** |
| DOM motor unit tests (new, stage 1) | `python -m pytest tests/test_dom.py -q` | **28 passed** |

Note: `.claude/skills/aster-validation-and-qa` documents "118 passed / 1 known
fail" (2026-07-05). The live suite has grown to 287 and the previously known-red
`test_first_run_setup.py::TestUpdateIdentity::test_preserves_comments_and_untouched_fields`
now passes. The skill inventory is stale; this log is the current truth.

## Deferred (requires live desktop / models / owner present)

- `engine_testing/locator_eval.py` baseline (screen capture + tracks + VRAM)
- Track-2 transient VRAM measurement during a real YOLO fallback
- Live fixture-site task matrix

## Target matrix (fixed with owner)

- Playwright fixtures: `engine_testing/fixtures/site/` (index, form)
- Windows UIA: File Explorer, Notepad, Calculator
- Real web read-only: Wikipedia
- Gated stretch: Discord-web as the user's own account (owner-approved runs;
  send policy `confirm`)
