# Stage 3 — Laya Decision Kernel (System-1)

**Status:** CODE COMPLETE + QA round 1 applied (2026-09-25); real-model
battery + live integration pending (needs `pip install laya` + checkpoint)
**Flags:** `USE_LAYA_KERNEL` (default false), `LAYA_MODEL_ID`, `LAYA_DEVICE`
(cpu), `LAYA_MARGIN_THRESHOLD` (0.25), `LAYA_KEEP_RESIDENT` (false),
`LAYA_LOG_PATH`

## Scope

- `core/system1.py`: lazy + ref-counted Laya singleton (copy `tools/audio.py`
  acquire/release shape), CPU-first.
- Decisions: `pick_operation` (choice ≤7: click/type/press/scroll/wait/done/
  blocked) · `pick_element` (choice over the ≤18 shortlist, neutral keys with
  role+name criteria — avoids the `noul` label-bias bug #156) · `check_state`
  (neutral-key 2-option choice).
- Margin gate (top1−top2); below threshold or `blocked` → escalate to Gemma,
  respecting `core/loop_guard.py`.
- Calibration log: `Aster_Vault/system1_log.jsonl` (schema, full distribution,
  margin, deterministic outcome).

## Predicted numbers

| Metric | Prediction |
|---|---|
| Decision latency (CPU) | 200–500 ms |
| Ambiguous-fixture accuracy vs lexical argmax | ≥ 80% vs ~40–60% |
| Idle VRAM change | 0 (CPU) |

## QA checklist

```powershell
python engine_testing/qa/run_stage.py 3
python -m pytest tests/test_system1.py -v
python system1-test.py          # mock-distribution battery
```

- [ ] offline unit tests (mocked kernel) green — margin logic, escalation,
      blocked path, JSONL shape
- [ ] mock battery green (canned distributions, no model download)
- [ ] slow real-model battery: ambiguous fixtures, latency, accuracy vs argmax
      (requires `pip install laya` + checkpoint download; marked slow)
- [ ] ≥ 1 forced-blocked case escalates to Gemma and logs
- [ ] RAM measured; idle VRAM unchanged
- [ ] end-to-end fixture tasks with vision verdicts

## Exit criteria

- [x] real-model battery: Laya installed (0.3.20) and **enabled live**; on
      amazon.eg it picked the `Go` submit button (margin 0.79) and answered the
      results-page state gate True (margin 0.82); operation gate picked `click`
      (margin 0.39). CPU predict ~1 s, load ~1 s cached.
- [x] escalation path proven (mock + failure modes + live near-tie → lexical)
- [ ] accuracy vs lexical argmax on a labeled ambiguous set (needs the
      calibration dataset; checkpoint warns its temperatures are uncalibrated)

## QA findings — subagent round 1 (applied 2026-09-25)

| # | Finding | Resolution |
|---|---|---|
| MED | Concurrent `_log` appends corrupted JSONL (389/400 parseable under 8 threads) | `_LOG_LOCK` serializes appends; 200-write/8-thread regression test asserts every line parses |
| MED | "Never raises" contract false on garbage input (non-dict answer, unhashable choice, non-iterable shortlist, non-dict model response) | Entry points hardened (`_extract_choice`, `_ask`, `pick_element`); 6 hardening tests added |
| MED | Confidence-only answers bypassed the margin gate (0.55 confidence → margin 0.55 vs true 0.10 margin) | Binary-only mapping `margin = 2c-1`; >2 options with no distribution → margin None → escalate |
| LOW | NaN margin failed open (`nan < threshold` is False) | `math.isfinite` gate → escalate; test added |
| LOW | `lay a_device` flag was inert | `laya.load(..., device=...)` with TypeError fallback; status reports it |
| LOW | `_log` dropped entries for bare filenames | `makedirs` only when a parent dir exists |
| LOW | `test_kernel_disabled_keeps_top_match` was tautological | Now asserts Laya is never consulted AND the top lexical node is resolved |
| — | Walk-reuse integration: kernel ON + escalate keeps top lexical match; OFF is byte-identical | verified by reviewer, asserted in tests |

Independent verification after fixes:
`pytest tests/test_system1.py -q` → **29 passed** · `system1-test.py` →
**18/18** · full suite → **365 passed** · test isolation confirmed (no
`Aster_Vault/system1_log.jsonl` pollution; QA probe artifact removed).
