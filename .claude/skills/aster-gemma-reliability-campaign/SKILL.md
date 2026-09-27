---
name: aster-gemma-reliability-campaign
description: Load this to work on Aster's hardest live problem (owner-confirmed 2026-07-05) - Gemma agent reliability. Covers tool-call hallucination, post-tool apathy, degenerate tool loops, and long-context degradation on the quantized E4B. An executable, decision-gated campaign - numbered phases, exact commands, expected numbers at every gate, a ranked solution menu, and fenced-off wrong paths. Triggers - "Aster keeps repeating the tool call", "model claims it did something", "improve tool reliability", "port LoopGuard", "empty response after tools".
---

# Gemma Reliability Campaign

> **STATUS: SETTLED 2026-09-27 — resolved by swapping the model.** The owner
> replaced the quantized Gemma-4-E4B with **Qwen 3.6 35B-A3B** (BeeLlama v0.4.7,
> 60k ctx, KVarN KV, MTP) rather than continuing to harden the small model. See the
> `aster-failure-archaeology` entry "Qwen 3.6 35B-A3B engine swap". Everything below
> is preserved as the record of how the failures were measured and mitigated; **the
> mitigations themselves all survive** (`_claims_tool_execution`, the apathy nudge,
> `core/loop_guard.py`, output compression) and are still the first thing to check
> when the new engine misbehaves. The one residual family is tool-avoidance on
> trivial instant actions — tracked in
> `engine_testing/qa/qwen-swap-live-validation.md` T3, lever = tool-law wording.

**The problem (owner-stated 2026-07-05):** the quantized Gemma-4-E4B agent
misbehaves in four ways — (a) *hallucinated execution* (narrates a tool action
without calling it), (b) *post-tool apathy* (empty final answer after successful
tools), (c) *degenerate loops* (same call repeated / A-B-A-B ping-pong; **no guard
exists today**), (d) *long-context degradation*. Mitigations (a) and (b) exist as
single-retry patches in the loop; this campaign is about **measuring** the failure
rates and **reducing them at the source**, promotable only with numbers.

**When NOT to use this skill:** one-off triage of a symptom →
`aster-debugging-playbook`; general method discipline →
`aster-research-methodology`; framing beyond the near-term →
`aster-research-frontier`.

**Ground rules inherited from change control:** baseline before change; every
change behind a flag default-off; A/B via the harness; VRAM gate for model swaps;
the legacy XML path stays.

---

## Phase 0 — Baseline (GATE: no baseline, no changes. None.)

**Assets today:** `engine_testing/` — 47 scenarios in `scenarios.py` across 10
categories, run by `run_engine_test.py` through `harness.py` (which imports the
LIVE system prompt + ADMIN_TOOLS from core.brain, replays the real 15-round loop
with mocked tool results, auto-scores 6 categories). Frozen June-2026 reports live
in `results/`.

**The honest caveat (historical):** `harness.py` used to exercise **only the legacy
XML path**. **Step 0.1 was completed in 2026-07** — the harness is now native-only
(`tools=ADMIN_TOOLS`, structured `tool_calls`, `role:"tool"` results), and the
legacy path itself was deleted in the 2026-09-27 swap. The original obligation
read as follows, for the record:

### 0.1 Extend the harness to the native path
Add a `--native` mode to `harness.py`/`run_engine_test.py` that mirrors
`core/brain.py` exactly:
- POST body carries `tools=ADMIN_TOOLS` (and NO XML manual in the system prompt);
- read `message["tool_calls"]` via `_extract_native_tool_call` (import it — reuse
  live machinery, the harness's founding principle);
- feed tool results back as `{"role":"tool","tool_call_id":…}`;
- keep the same scenarios, scoring, and report format so numbers are comparable.
Ship it as a new mode, not a rewrite — the legacy mode remains the rollback
comparator.

### 0.2 Run both paths, same day, same model
```powershell
.\start.bat    # or the QAT launcher — RECORD which via Invoke-RestMethod http://localhost:8080/props
python engine_testing/run_engine_test.py            # legacy mode
python engine_testing/run_engine_test.py --native   # new mode (after 0.1)
```

**Baseline of record:** the frozen 2026-06-24 Gemma report (legacy path, 128k ctx)
scored 32/32 auto at 90.2 tok/s. **Current Qwen 3.6 35B-A3B baseline (native path,
60k ctx, 52 scenarios):** 31/37 auto at temp 1.0 (84%), 27/37 at temp 0.7 (73%),
0.0% text-tool-syntax leaks, 0.0% JSON decode failures, loop discipline 5/5, ~40
tok/s decode. The older expectation text below is kept for the record:**
auto-score **32/32** (single-tool 8/8, args 6/6, multi-step 5/5, restraint 5/5,
hallucination 5/5, cutoff-routing 3/3); 90.2 tok/s; 4.98 s avg/turn; 0% XML
mangle on 84 calls.

**Decision gates:**
- Legacy run ≈ 32/32 and native run within 2 points → healthy; proceed to Phase 1.
- Native run shows *catastrophic* failures (role confusion, echo loops, empty
  grammar) → **STOP.** You are in template territory — read
  `aster-failure-archaeology` §engine-hell; suspect llama-server version or Jinja
  template drift, not your harness code.
- Legacy itself regressed vs the frozen report → the model/launcher changed;
  check `/props`, re-run on the documented q4km model before concluding anything.

### 0.3 Baseline the live failure rates (the lab ≠ the daily driver)
The harness scenarios are clean one-shot tasks; the real failures show up in long
daily sessions. Before Phase 2, capture at least a week of Phase-1 counters
(below) as the live baseline. Record: hallucination-retry firings/day,
apathy-nudge firings/day, loop incidents/day (manual count from `/peek` when
noticed), average rounds per tool task.

---

## Phase 1 — Instrumentation (small, safe, do it once)

Add cheap counters where the mitigations already fire in `core/brain.py`:
- increment-and-log when `_claims_tool_execution` triggers the retry
  (`hallucination_retried` site, ~:3217);
- when the apathy nudge fires (the `tools_executed`-and-empty exit);
- rounds used per turn; and a repeated-identical-call detector in *log-only* mode
  (hash `(tool_name, json.dumps(arguments, sort_keys=True))`, warn at 3 — this is
  LoopGuard's sensor half without the blocking half).
Write to stdout (the diagnostics relay forwards it) or a JSONL in `Aster_Vault/`.
Gate behind a config flag per `aster-config-and-flags` checklist. This phase
changes NO behavior — it only makes the problem measurable.

---

## Phase 2 — Solution menu (ranked; each with theory, expected effect, gate)

Work top-down; one change per A/B cycle.

### 2.1 LoopGuard port (graded highest-payoff in `open-jarvis.md` §1.1)
- **Mechanism:** call-hash budget (block after N identical calls, default 3),
  sliding-window ping-pong detection (A-B-A-B), warn-before-block (first cycle
  injects a warning into context, second blocks), per-tool `polling=True` budget
  relaxation.
- **Theory obligation:** blocking must not break legitimate repetition — mark
  `watch_screen` (and any poller) as polling; verify `smart_click` retries on a
  *changed* screen hash differently (arguments differ → not identical).
- **Implementation:** drop in as `core/loop_guard.py`, wire between tool-call
  extraction and `execute_tool()` in the admin loop; flag:
  `runtime.loop_guard_enabled` default false.
- **Gate:** on a replay set of known loop incidents (collect from Phase 1) →
  loop incidents ≈ 0; on the 47-scenario battery → auto-score unchanged (32/32
  legacy / Phase-0 native number); no legitimate task needs >1 warning.

### 2.2 Observation compression (`open-jarvis.md` §1.2)
- **Mechanism:** `_compress_tool_output(text, mode)` in `execute_tool()` —
  truncate long tool results (hard cap + tombstone) or summarize the heavyweights
  (`research`: one temp-0.2 call, ~200 tokens).
- **Theory obligation:** never truncate what the model still needs — per-tool
  policy table (e.g. `read_local_file` truncate-with-path-tombstone,
  `list_directory_tree` truncate, `research` summarize, short tools none).
- **Gate:** long-session context growth measurably slower (context_estimate.py on
  `/peek` dumps before/after); multi-step scenario scores unchanged.

### 2.3 Think-tag stripping (`open-jarvis.md` §1.5 — cheap hygiene)
Regex-strip `<think>…</think>`/`<thinking>…` blocks in
`_execute_llm_completion` before returning. Gate: no legitimate content ever
matches (audit a week of transcripts in `Aster_Vault/Conversations/` for false
positives first).

### 2.4 Sampling tuning (method, not a guess)
Current production defaults: temp 1.0, top_p 0.95, top_k 64 (config.py).
Hypothesis worth testing (write predictions FIRST — see
`aster-research-methodology`): lower temp (0.7) and/or a `repeat_penalty` reduces
loop/hallucination rates on tool turns at some cost to persona liveliness.
Sweep with the native harness (reliability) + `conversation_testing.py` (style
side-by-side). Gate: reliability metric improves with style judged acceptable —
then change the default in `self_config.example.yaml`/config docs, not hardcoded.

### 2.5 RRF hybrid memory (`open-jarvis.md` §1.3 — recall quality, secondary here)
BM25 over `memory.md` + ChromaDB dense, fused `RRF(d)=Σ w_i/(60+rank_i)`.
Improves "agent acts on wrong/missing memory" failures, not loops. Gate: a
labeled recall mini-set (semantic + keyword-exact queries) where hybrid beats
dense-only.

### 2.6 Model swap (last — biggest hammer, most expensive to validate)
The June-2026 sweeps already compared E2B-QAT / E4B-QAT / E4B-q4km / 12B builds
(reports frozen in `results/`; note the newer launchers already serve a QAT E4B).
Any swap must: run the full battery on BOTH paths, pass the VRAM budgets
(`aster-vram-discipline` — the live engine holds ~11.5-11.9 GB of the 12.3 GB card at
60k ctx; say so with `nvidia-smi` numbers, which were never recorded for 12B), and
soak a week on the daily driver.

---

## Fenced-off wrong paths (each cost real time once — do not re-enter)

1. **No llama-cpp-python, no Llava15ChatHandler, no ChatML function-calling.**
   The three 2026-05 dead ends (`aster-failure-archaeology` §engine-hell).
2. **No casual edits to the chat template** (now `E:\Models\qwen36_chat_template.jinja`). Template changes
   require the full Phase-0 A/B on both paths before AND after.
3. **Never inject the XML tool manual into the system prompt in native mode** —
   the schemas travel in the `tools=` param; doubling them confuses the grammar.
4. ~~Do not delete the legacy XML path~~ — **superseded 2026-09-27:** the XML path and
   its `USE_NATIVE_TOOL_CALLS` flag were deleted with owner sign-off; the rollback is now
   branch `gemma-4-e4b-lightweight`, not a flag. Never re-add a parallel parse path.
5. **Do not raise the hallucination retry count above 1** — the single-retry flag
   exists to prevent retry loops; fix upstream instead.
6. **Do not "fix" apathy by auto-repeating the nudge** — same loop risk; the
   nudge is deliberately once-then-clean-history.

---

## Validation and promotion protocol

1. A/B on the 47-scenario battery, both paths, same model/launcher (recorded via
   `/props`).
2. Live soak ≥ 1 week behind the flag, Phase-1 counters compared to the live
   baseline (0.3).
3. Style check if sampling changed (`conversation_testing.py`).
4. VRAM check if anything loads differently (budgets in `aster-vram-discipline`).
5. Ship via `aster-change-control` (flag default flip = the promotion), update
   CLAUDE.md + this skill's numbers.
**Success is a moved number against Phase 0/0.3 — never "it feels better".**

## Provenance and maintenance

Authored 2026-07-05. Baseline numbers from
`engine_testing/results/engine_report_unknown_20260624_160054.txt`.

- Scenario count: `Select-String -Path engine_testing\scenarios.py -Pattern '"category":' | Measure-Object`
- Harness is native-only: `Select-String -Path engine_testing\harness.py -Pattern "tools=|_extract_native_tool_call"` (expect the former; the latter is imported from brain)
- Latest frozen report: `Get-ChildItem engine_testing\results | Sort-Object LastWriteTime | Select-Object -Last 1`
- Loop-guard existence: `Get-ChildItem core\loop_guard.py` (absent as of 2026-07-05)
