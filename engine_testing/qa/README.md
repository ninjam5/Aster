# Stage QA Harness — DOM-First Automation Plan

One folder, one loop: after every stage, a runner executes the stage's tests,
dumps raw artifacts, and a subagent (DeepSeek V4.1-Flash) iterates on failures
against the stage checklist until the exit criteria are met.

## Layout

```
engine_testing/qa/
  README.md        <- this file
  stage0.md .. stage4.md   <- per-stage checklists: commands, predicted numbers,
                              artifacts, exit criteria, known-red allowance
  run_stage.py     <- executes a stage's suites, writes artifacts, exits nonzero on red
  artifacts/
    stageN/        <- raw logs + machine summary per run (timestamped)
```

## Runner

```powershell
python engine_testing/qa/run_stage.py 1
```

It runs each suite for the stage, tees stdout+stderr to
`artifacts/stageN/<suite>-<timestamp>.log`, writes `summary-<timestamp>.json`,
prints a PASS/FAIL table, and exits non-zero when any suite failed.

## Subagent protocol (per stage)

1. Runner executes; a failure is a fact, not a discussion.
2. Subagent reads the stage checklist + `artifacts/stageN/` and triages:
   - **Real regression** -> propose a minimal fix (or fix directly if executing).
   - **Pre-existing red** -> report as pre-existing with evidence; never absorb
     it into the stage's change (the known-red `test_first_run_setup.py` test is
     the canonical example).
3. Re-run after each fix. Hard caps:
   - 5 iterations per stage, then stop and report.
   - Unrelated suite suddenly red -> stop, report, do not "fix forward".
4. Exit criteria met -> append the measured numbers to the stage checklist and
   update `summary.md` (living doc).

## Vision tests

Used where a DOM/state assertion cannot prove the outcome (visual-only state,
styling, end-state of a web task). Fixture/synthetic screenshots may go to the
DeepSeek V4.1-Flash API — owner-approved dev-time exception to the runtime
"data never leaves the machine" claim (see README/summary privacy note).

## Baseline (measured 2026-09-25, this machine)

| Suite | Result |
|---|---|
| `python -m pytest tests/ -q` | **287 passed** |
| `python dom-motor-test.py` | **24/24** |
