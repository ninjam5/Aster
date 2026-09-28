---
name: aster-research-methodology
description: Load this before running any experiment, tuning any threshold, comparing models or prompts, or promoting a hunch to a change in Aster. The discipline that turns an idea into an accepted result here - the evidence bar, hypothesis-predicts-numbers-before-running, the idea lifecycle from backlog to adopted-or-retired, and the project's analysis recipes (VRAM accounting, model sweeps, style sweeps, threshold tuning, token math) each with a worked example from this repo's history.
---

# Aster Research Methodology

**When NOT to use this skill:** the specific reliability campaign →
`aster-gemma-reliability-campaign`; what to work on →
`aster-research-frontier`; the instruments → `aster-diagnostics-and-tooling`.

## The evidence bar

1. **One mechanism must explain ALL observations — including the negatives.**
   If your theory explains why the bug happens but not why it *doesn't* happen in
   case X, you don't have the mechanism yet. Worked example: "IEMOCAP mislabels
   moods" wasn't accepted until the arousal-vs-valence mechanism explained BOTH
   failure directions (fast-neutral→frustrated AND angry→happy) and the
   non-failure (calm speech reads fine) — which then dictated the correct fix
   (text gates prosody) rather than a threshold band-aid.
2. **Hypothesis predicts numbers BEFORE running.** Write the prediction table
   first ("temp 0.7 will cut loop incidents ≥50% on the replay set, style sweep
   will drop ≤1 grade") — then run. A post-hoc "the numbers look better" is
   selection bias with extra steps.
3. **Assigned adversarial refutation.** When you believe a fix works, spend a
   dedicated pass trying to BREAK it before promoting: feed the edge inputs, run
   the negative checklist items, try the inputs that made the old code fail. The
   mock harnesses encode this habit — every one tests the disabled path is a hard
   no-op and that gates BLOCK (e.g. `emotion-test.py`: "budget exhausted → none",
   "same streak → at most once").
4. **"Works on my one prompt" is not evidence.** Minimum: the relevant harness
   battery or a labeled mini-set. Anecdotes only generate hypotheses.
5. **Pre-register failure modes.** Every emotion feature shipped with a
   "Possible derailments" list written BEFORE going live
   (`emotions-next-steps.md`) — several later manifested exactly as predicted and
   were already fenced. Copy this: list how your change could misbehave and the
   lever for each, in the spec, before shipping.

## The idea lifecycle (every stage has a real example)

```
capture → grade → spec (+ per-feature checklist + derailments) → build behind
opt-in flag (default OFF) → mock harness + live checklist → daily-driver soak →
PROMOTE (flag default flips)  |  RETIRE (documented)  |  DEAD END (documented)
```

- **Capture:** `ideas.md` (features), `ideas-for-backend.md` (infra),
  `open-jarvis.md` (external mining).
- **Grade:** open-jarvis.md's method — each idea scored by *payoff vs rewiring
  cost*, tiered, with effort estimates. Surgical lifts, never framework adoption
  ("we don't want their framework — we want surgical lifts").
- **Spec:** per-feature testing checklist + derailments (template in
  `aster-docs-and-writing`).
- **Build:** opt-in flag default OFF (all four emotion tiers, both mood
  behaviors); toggle tool + Telegram command.
- **Test:** standalone mock harness (no llama-server) + live checklist.
- **Soak:** the owner runs it daily; feedback is ground truth.
- **Promote:** default flips / feature declared live in docs.
- **Retire:** Karaoke (2026-07-03) — removed cleanly, sweep documented, frozen
  logs left intact, "never restore" recorded.
- **Dead end:** the three 2026-05 engine architectures — documented in
  `aster-failure-archaeology` §engine-hell with explicit fences.

**Where good ideas historically came from:** (1) mining external projects with
the grading method (OpenJarvis → LoopGuard/compression/RRF), (2) systematic
sweeps (the June model sweep), (3) daily-use pain (barge-in, apathy nudge, cold
storage). Notably NOT from speculative architecture.

## Analysis recipes

### (a) VRAM accounting — predict, then verify
Predict: weights ≈ bits/8 × params (+ ~10% overhead) + KV cache (scales with ctx
× layers; q4_0 ≈ ¼ of fp16) + projector + per-model runtimes. Verify with
`vram_report.ps1` / `nvidia-smi` at idle → loaded → released.
**Worked example:** the Qwen 3.6 35B-A3B swap's llama-server footprint was measured
at ~11.5–11.9 GB / 12.3 GB (2026-09-27, 60k ctx, `--n-cpu-moe 20`, KVarN KV) — the
measured number is what the launcher flags are tuned against. Estimates inform;
measurements decide.

### (b) Model-comparison sweep — one variable, fixed battery, frozen reports
Method (June 2026, `engine_testing/`): same 47 scenarios, same scoring, swap ONE
thing (the GGUF in the launcher), auto-score, write a timestamped report, diff.
Run categories you can't auto-score (persona/vision) as manual-grade sections in
the same report. Never compare runs from different scenario versions.
**Worked example:** reports for E2B-QAT/E4B-QAT/E4B-q4km/12B builds, 2026-06-18
→ 06-24 (latest Gemma: 32/32 auto, 90.2 tok/s, 4.98 s/turn); the Qwen 3.6 35B-A3B
swap (2026-09-27) scored **31/37 auto @ temp 1.0, 27/37 @ temp 0.7**, ~40 tok/s
decode hot.

### (c) Style/sampling sweep — matrix, side-by-side, honest about subjectivity
`conversation_testing.py`: fixed prompt battery (persona-relevant situations,
with the live `[Mood:]`/`[Speaker:]` context tags reproduced) × sampling presets
→ markdown matrix for side-by-side human judgment. Style has no auto-metric yet
(known gap — frontier 1.1); the discipline is *fixed inputs + all outputs
visible together*, so judgment is at least comparative, not anecdotal.

### (d) Threshold tuning — documented lever + data, never a feel
The pattern: the threshold lives in config; its tradeoff is pre-written where
it's defined. Worked examples: `EMOTION_MIN_CONFIDENCE` 0.5 with a written rule
("raise to 0.6 iff false frustrated/happy on fast speech becomes annoying —
trades sensitivity for fewer false positives"); ECAPA `SIMILARITY_THRESHOLD`
0.35 vs `LEARN_THRESHOLD` 0.50 (deliberately split: identify loosely, learn
strictly). Changing one requires a labeled mini-set OR a logged week of outcomes.

### (e) Token-budget math — know your heuristic's error direction
Everything token-shaped uses `len//4` (`core/memory.py`). It UNDER-counts dense
text (code/JSON), so real usage ≥ estimate; the trim budget (90% of N_CTX = 54,000
est. tokens at 60000) now matches the 60000-token server.
Use `context_estimate.py` (diagnostics scripts) and treat >80% as the danger
zone. Any accuracy-critical work should state estimate error explicitly.

### (f) Replay-set construction — turn live pain into a benchmark
When a live failure class matters (loops, recall misses): collect real instances
(`/peek` dumps, `Aster_Vault/Conversations/` transcripts), anonymize to
scenario JSON in the harness format, freeze the set, THEN develop against it.
The campaign's LoopGuard gate is defined this way. Never tune against the
incident you're currently annoyed by alone — that's n=1.

## When a result is accepted

Measured against a pre-registered prediction on a frozen battery/replay set +
survived the adversarial pass + soaked live if behavioral + documented in the
right doc of record (`aster-docs-and-writing`) + shipped through
`aster-change-control`. Anything less is labeled candidate.

## Measurement traps that cost real time (2026-09-27/28)

1. **`usage.prompt_tokens` is the full prompt size, not the prefill work.**
   llama-server returns top-level `timings` with `prompt_n` (tokens actually
   processed), `prompt_ms`, `prompt_per_second`. With a shared system+tools prefix
   (~13k), a warm call prefills only ~50-200 tokens while `prompt_tokens` still
   reads 13k. Reading the wrong field made a fast harness look like it re-prefilled
   12.5k per call - it did not.
2. **Prefill tok/s is length-dependent.** Small prompts are overhead-dominated
   (a 1.1k cold prompt reads ~190-400 tok/s; a 4k prompt ~860; the same config).
   Never compare rates across different prompt sizes - compare absolute ms for the
   same prompt, or quote `prompt_n` + `prompt_ms`.
3. **Single-sample A/B is noise.** The 37-auto battery swung 29-35 across identical
   configs. Use `--repeat N` + the FLAKY list before claiming a win.
4. **Wall-clock-minus-assumed-decode estimates overstate prefill.** The probe's
   "~1,600 pp tok/s" estimate disagreed with the server's timer (~860). Prefer the
   server's own timings.

## Provenance and maintenance

Authored 2026-07-05.

- Lifecycle exemplars still present: `Select-String -Path emotions-next-steps.md -Pattern "Possible derailments"`
- Grading method: `Select-String -Path open-jarvis.md -Pattern "TIER"`
- Threshold docs: `Select-String -Path config.py -Pattern "min_confidence" -Context 2`
- Sweep tooling unchanged: `python conversation_testing.py --help` (or read its docstring)
