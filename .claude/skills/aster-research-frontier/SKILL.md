---
name: aster-research-frontier
description: Load this when choosing what to build next in Aster beyond bug fixes, evaluating whether an idea is novel or already planned, or asked "what should we research", "what's on the roadmap", "could Aster advance the state of the art at X". The open-problem catalog in the owner's priority order, each with why current SOTA fails, Aster's specific asset, the first three concrete steps in this repo, and a falsifiable you-have-a-result-when milestone. Everything here is OPEN or CANDIDATE - nothing is shipped.
---

# Aster Research Frontier

Owner's priority order (2026-07-05): **(1) frontier-agent behavior on a 4–6 GB
local model → (2) the definitive local companion → (3) shippable local-agent
platform.** Every item below is open/candidate — do not describe any as done.

**When NOT to use this skill:** executing the near-term reliability work →
`aster-gemma-reliability-campaign` (that skill IS track 1's execution arm);
the discipline for testing a hunch → `aster-research-methodology`.

---

## Track 1 — Frontier-agent behavior on 4–6 GB

### 1.1 Eval telemetry for local agents (from `ideas-for-backend.md` #3)
- **Why SOTA fails:** agent benchmarks assume frontier models and cloud traces;
  there is no established, cheap, continuous eval for a *daily-driver* sub-8 GB
  agent. Local-agent projects ship vibes.
- **Aster's asset:** a real 47-scenario auto-scored harness that replays the live
  loop, months of frozen model-sweep reports, and a genuine daily workload.
- **First three steps:** (1) native-path harness mode (campaign Phase 0.1);
  (2) implement the two proposed metrics — *response match score* and *tool
  trajectory score* (did the model take the expected tool sequence, not just the
  right final answer) — on top of `run_scenario`'s existing per-round records;
  (3) wire the campaign's Phase-1 live counters into a weekly scorecard.
- **Result when:** two harness runs a month apart produce comparable scorecards
  (lab + live) and a regression is caught by the numbers before the owner feels it.

### 1.2 Runtime guards for small-model agent loops
- **Why SOTA fails:** big models rarely loop; small quantized ones do, and the
  guard literature (reflection, critic models) assumes budget Aster doesn't have.
- **Asset:** LoopGuard design already graded (open-jarvis.md §1.1) + a live system
  that actually exhibits the failure.
- **First steps / result-when:** see campaign §2.1 — the research framing beyond
  it: characterize *which* loop classes a hash/ping-pong guard cannot catch
  (semantic loops with varying args) and whether a cheap screen-state hash fixes
  the smart_click case.
- **Result when:** a taxonomy of observed loop classes with per-class catch rates,
  measured on ≥1 month of live counters.

### 1.3 Long-context degradation on quantized KV cache — a measurable science question
- **Why SOTA fails:** published long-context evals use fp16 KV; almost nothing
  quantifies task accuracy vs context fill under **q4_0 KV at 128k on consumer
  VRAM**.
- **Asset:** the exact rig, permanently running.
- **First steps:** (1) add a `--context-fill N` harness option that pre-stuffs
  history to N tokens before running the battery; (2) sweep fill ∈ {0, 32k, 64k,
  96k, 120k} on the same scenarios; (3) plot auto-score + tok/s vs fill.
- **Result when:** a curve exists; the knee (if any) dictates the real `/compact`
  policy instead of the current guess, and resolves whether the
  N_CTX/--ctx-size mismatch matters in practice.

### 1.4 Hybrid recall (RRF) — campaign §2.5; research angle: what mix of
keyword-exact vs semantic queries does a *personal assistant* workload actually
produce? `Aster_Vault/Conversations/` is a labeled corpus waiting to be mined.

## Track 2 — The definitive local companion

### 2.1 Confidence-weighted mood fusion
- Today `fuse_face`/fusion is a **priority rule** (text > face > ambient-voice),
  explicitly flagged in `emotions-next-steps.md` "Further horizons" as unfinished.
- **First steps:** (1) log per-tier confidences alongside labels in
  `emotion_log.jsonl`; (2) replay a month of logs offline comparing priority-rule
  vs weighted-blend agreement with self-reported check-ins; (3) prototype the
  blend behind `emotion.fusion_mode`.
- **Result when:** on the replay set, the blend disagrees with the priority rule
  in >N cases AND human-judged labels favor the blend in a clear majority.

### 2.2 Mood-conditioned TTS prosody — Kokoro speed knob per detected mood
(slower/softer for sad, punchier for happy); needs a per-call speed override
beside `config.VOICE_SPEED`. Result when: blind A/B (same reply, two prosodies)
prefers conditioned in most sad/happy cases.

### 2.3 Longitudinal mood science — `emotion_log.jsonl` + `mood_trends.md`
accumulate daily. Open question: do sustained-mood streaks predict anything
actionable (e.g. late-night frustration clusters)? Result when: one validated
pattern drives one new (opt-in, debounced) behavior that the owner keeps enabled.

### 2.4 Proactivity-that-never-annoys as a measurable property — nudge
acceptance is implicitly observable (did the owner accept the mood-action offer /
respond to the check-in / snooze the intervention?). First step: log
accept/dismiss per nudge type. Result when: per-nudge acceptance rates exist and
the initiative matrix is re-tuned from data.

### 2.5 Custom "Hey Aster" wake word — openWakeWord supports synthetic-data
training (maintainer's Colab, no recordings needed; `ideas.md` #6). Drop the
.onnx path into `runtime.wake_word_model`. Result when: false-accept/miss rates
measured ≥ parity with `hey_jarvis` over a week.

### 2.6 3D VRM character — **BLOCKED ON OWNER** (needs a rigged .vrm; plan of
record: VRoid Studio → three.js + @pixiv/three-vrm; game engines rejected for
VRAM contention). Not actionable by engineers; don't start it speculatively.
(2D puppet character is live in Aster-UI since 2026-07-04.)

### 2.7 Aster self-affect (deferred in emotions-next-steps.md) — Tier-0 model on
Aster's own responses for a reported internal state. Deferred until persona
output is stable; respect that gate.

## Track 3 — Shippable local-agent platform

### 3.1 Hardware-adaptive launcher (`packaging.md`, design 2026-07-03)
One Python launcher: detect VRAM/RAM/disk → pick model tier + ctx/KV flags →
`hf_hub_download` if missing → launch llama-server → poll `/health` → launch
main.py → launch dashboard. **Its own doc lists the open research questions
verbatim** — real VRAM thresholds for E2B/E4B/12B (never measured), whether
E2B/12B GGUFs ship their own mmproj, whether the vendored binary degrades to
CPU-only cleanly. First step is *measurement*, not code: record `nvidia-smi`
numbers for each tier. Result when: a fresh machine reaches a healthy boot from
one command, tier chosen automatically.

### 3.2 One-click install — Tauri sidecar bundling (LM-Studio-shaped); explicitly
out of near-term scope per packaging.md (dlib/MediaPipe freezing pain). Candidate
only.

### 3.3 Asset ecosystem specs — voice (recording spec, ZIP+meta.json), avatar
(Live2D/sprite manifest), persona contribution rules (no tool lists in persona
files) — skeletons in `aster-opensource-plan.md` Step 4; several checklists
unfilled. Result when: one external contributor ships one asset without help.

### 3.4 Agent-to-agent protocol (plan Step 9) — keypair identity, signed message
schema, owner-approval on every cross-agent request, and a **prompt-injection
sanitization layer** (strip tool syntax/`[System Internal]`/turn tokens from
external messages). The security-research angle is the interesting part: local
agents talking to each other is an unsolved trust problem. Result when: two
Asters exchange one useful request end-to-end with every action owner-approved
and injection attempts from a red-team corpus all neutralized.

### 3.5 Beta discipline — plan Step 6's "do not skip" gate: 5–10 users, install
success rate, first-failing tool, cold-boot time, VRAM on varied GPUs.

## Provenance and maintenance

Authored 2026-07-05 from `ideas-for-backend.md`, `open-jarvis.md`, `ideas.md`,
`emotions-next-steps.md`, `packaging.md`, `aster-opensource-plan.md`, owner Q&A.

- Roadmap drift: re-read `ideas.md` status markers and `emotions-next-steps.md` headers
- Blocked-item status: ask the owner about the .vrm model before touching 2.6
- Launcher open questions: `Select-String -Path packaging.md -Pattern "Open questions" -Context 0,12`
