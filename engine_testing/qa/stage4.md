# Stage 4 — Calibration, Promotion, Cleanup Sweep

**Status:** TOOLING + DOCS COMPLETE (2026-09-25); live calibration and flag
promotion intentionally NOT executed (needs live QA evidence + owner sign-off)

## Promotion mechanics (deliberately not flipped in this session)

The plan gates promotion on live measurements the offline environment cannot
produce (fixture pass rate, locator_eval A/B, real Laya latency/accuracy,
calibration ECE). Defaults therefore remain: `USE_DOM_MOTOR=false`,
`USE_LAYA_KERNEL=false`, `USE_PIXEL_FALLBACK=true`.

To promote, the owner sets in `self_config.yaml` (one boolean each, one-line
revert):

```yaml
automation:
  dom_motor: true        # stage 1 exit met on the target matrix
  laya_kernel: true      # stage 3 exit met + stage 4 calibration done
  pixel_fallback: false  # stage 2 A/B shows DOM+UIA+OCR coverage
```

Preconditions, in order:
1. Stage 1 live QA: fixture + Explorer/Notepad/Calculator matrix ≥ 95%,
   `locator_eval live` provenance sane, no background-browser misfires.
2. Stage 2 A/B: `locator_eval run` vs `run --no-pixel` — document the residual
   no-DOM target list; only then flip `pixel_fallback`.
3. Stage 3: `python -m pip install laya`; ambiguous-fixture battery meets
   ≥ 80% vs argmax; CPU latency measured; RAM measured; escalation path seen
   live at least once.
4. Stage 4 calibration: collect `Aster_Vault/system1_log.jsonl` with `outcome`
   fields, run `python engine_testing/calibrate_system1.py`, set
   `laya_margin_threshold` from the fitted precision curve; re-run the stage-4
   QA checklist.

## Scope

- Collect stage-3 logs + QA runs → auto-label outcomes (deterministic
  verification of the following step) → fine-tune via the Laya notebook →
  temperature fit per (question type × option count) → ECE on held-out.
- Set margin/confidence gates from the precision curve (documented tradeoffs).
- Cleanup sweep (change-control class G **only on evidence**): remove Track 2 +
  ultralytics/OmniParser imports + archived YOLO block + remaining Florence
  references; sweep living docs (`CLAUDE.md`, `summary.md`, `AGENTS.md`,
  config examples). Dated logs are never rewritten.
- Privacy-claim amendment: README/summary scope "data never leaves the machine"
  to runtime Aster; QA subagent screenshots to the DeepSeek API are the
  owner-approved dev-time exception.

## Predicted numbers

| Metric | Prediction |
|---|---|
| ECE (held-out) | ≤ 0.10 after temperature fit |
| Discord-web stretch | "message Tolba" completes via the motor loop, send gated |
| Full suite | green; engine_test battery vs golden baseline unchanged |

## QA findings — final end-to-end review (applied 2026-09-25)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1 | BLOCKER (promotion) | Chrome ≥136 ignores `--remote-debugging-port` on the default profile without an explicit `--user-data-dir`; the launch path could never open the port | `_launch_browser_with_debug` now passes the real profile dir; test asserts the arg; setup note added to stage1.md + config comment |
| F2 | MED | `stage2.md` still said PLANNED after completion | status updated |
| F3 | MED (pre-existing) | `tests/test_migration.py` / `test_auto_compact.py` write into `Aster_Vault` (memory.md, conversations, emotion log) — predates this plan, not touched | reported as pre-existing; new Stage suites are isolated to tmp/`%TEMP%` |
| F4 | MED | Operation/state verdicts logged semantic `choice` while distributions are keyed A..G, so calibration silently skipped them | `key` field added to verdicts; `calibrate_system1.py` prefers it; tests added |
| F5 | LOW-MED | Summary implied `check_state` was wired to production | wording corrected (API exists, no caller yet) |
| F6 | LOW | `artifacts/stage0/` was claimed but missing | `run_stage.py 0` executed; artifact created |
| F7 | LOW | Archived block comment referenced Florence-2 | comment updated |
| F8 | LOW | `vision.icon_captioner` documented but absent from the example yaml | `vision:` section added |
| F9 | LOW | `close_web_context()` unused (worker lives for process lifetime) | accepted by design; daemon thread |
| F10 | LOW | Empty UIA walk could trigger a second Track-0 walk | `uia_done` now set when the walk succeeded with an empty tree |

## Completed in this session

- [x] calibration tooling + tests (`engine_testing/calibrate_system1.py`, 13 tests, selftest ECE 0.400 → 0.054)
- [x] docs sweep: `CLAUDE.md`, `summary.md`, `AGENTS.md`, `tools/self_knowledge.py`
- [x] QA rounds 1–2 per stage + final end-to-end review (findings above)
- [x] default-off promise verified (380 pytest, harnesses green, ADMIN_TOOLS 68)

## Live enablement (2026-09-25, owner request)

`self_config.yaml` → `automation`: `dom_motor: true`, `laya_kernel: true`,
`laya_margin_threshold: 0.01`, `laya_keep_resident: false`, `web_browse: true`,
`own_browser: true`
(`pixel_fallback` left `true`; Stage-2 A/B still pending). Laya 0.3.20 installed
via global pip — **it downgraded `huggingface_hub` 1.29.0 → 0.36.2**; verified
compatible (transformers 4.57.3, `hf_hub_download` OK, full suite green).
`laya_keep_resident: false` unloads the checkpoint when idle (`release()` runs
`gc.collect()` + `torch.cuda.empty_cache()`), so RAM/VRAM is returned; Laya is
CPU by default (RAM only). **Restart Aster** to apply.

Remaining before promotion is "done": collect outcome-labeled decisions and run
`calibrate_system1.py`, raise `laya_margin_threshold` from the fitted curve, and
run the `locator_eval` A/B to flip `pixel_fallback`.

## QA checklist

```powershell
python engine_testing/qa/run_stage.py 4
python engine_testing/run_engine_test.py     # vs frozen golden baseline
```

- [ ] calibration report (ECE before/after) in `engine_testing/results/`
- [ ] final `locator_eval` report across the matrix
- [ ] full-matrix vision QA via DeepSeek subagent (fixture screenshots)
- [ ] removal sweep verified (grep for removed symbols is clean in live code)
- [ ] `summary.md` + README amended
- [ ] rollback flags documented (each stage is one boolean)
