# Laya Integration — E2E Test Plan

**Purpose:** the **live** end-to-end tests for every phase of
[`laya-integration.md`](laya-integration.md) — the ones that need the real Laya model,
the real llama-server and the real vault. Run them **all together later**, after the
server is up and `main.py` has been restarted with the current code.

**Not this:** `pytest tests/` is the *mocked* unit layer (no model, no ChromaDB, no
network) — 565 passing as of 2026-09-28. This file is the other layer: does the gate
actually behave on the live system.

Status key: **DONE** = already verified live during that phase (evidence in
`laya-integration.md`) · **TODO** = must be run.

---

## 0. Preconditions (check before running anything)

| # | Check | How |
|---|-------|-----|
| P1 | llama-server healthy | `Invoke-RestMethod http://localhost:8080/health` → 200 |
| P2 | `main.py` running the current code | restart it first; the process must post-date the last commit |
| P3 | Laya enabled + resident | `automation.laya_kernel: true`, `laya_keep_resident: true`; expect `[System1] Loading Laya checkpoint …` **once** at first use, not per call |
| P4 | Threshold is 0.25, not 0.01 | `automation.laya_margin_threshold: 0.25` |
| P5 | ~2.2 GB RAM spare | Laya resident costs ~2.2 GB; the browser guard needs 1.5 GB |
| P6 | Evidence sinks open | `Aster_Vault/system1_log.jsonl` (every Laya verdict + margin) and `Aster_Vault/reliability_log.jsonl` (retry/skip events) |

**Two test layers used below:**

- **Layer A — direct gate probes (safe, scriptable).** Call the gate functions
  directly against the real model, exactly like the phase-verification probes. No
  real-world side effects. This is the primary E2E for the decision layers.
- **Layer B — live in-persona (Telegram/CLI).** Drives the real tools. Use a safe
  target (own account / a test contact); the send gate will ask for confirmation.
  **These can send messages — don't point them at a real friend.**

Template for a Layer A probe (used throughout):

```python
import os, sys
sys.path.insert(0, r"E:\LLM testing\Aster-localization")
os.chdir(r"E:\LLM testing\Aster-localization")
import core.brain as brain, core.memory as memory, core.system1 as system1, tools.dom as dom, tools.rag as rag
# ...call the gate, print the verdict + margin
```

---

## 1. Phase 0 — wrappers, batching, threshold, resident

| ID | E2E test | Expected | Evidence |
|----|----------|----------|----------|
| P0-1 | Layer A: `system1.choose("which?", ["a","b","c"], key="t")` with a clear question | a choice, `escalate=False`, a real `margin` | printed verdict |
| P0-2 | Layer A: `system1.score_candidates(levels, q, {…})` on 3 candidates, one clearly best | best = the right one, normalized margin > 0.4 | printed scores |
| P0-3 | Layer A: `system1.ask_batch({…two questions…})` | `ok=True`, both answered, **one** load only | console shows a single `[System1] Loading` |
| P0-4 | Layer A: same `choose` with `min_margin=0.9` | `escalate=True`, `threshold=0.9` | printed verdict |
| P0-5 | Load-once check: run any two gates back to back | the checkpoint is loaded **once** (resident) | `system1.kernel_status()["refcount"]` / console |
| **Status** | **DONE** — verified in Phase 0 | | `laya-integration.md` §Phase 0 |

---

## 2. Phase 1 — consolidation, screenshot, vision, send, research

| ID | E2E test | Expected | Evidence |
|----|----------|----------|----------|
| **7a** | Trigger a consolidation (5 real turns, or `/memorize`) with the llama-server window visible | the consolidation call's prompt is ~1k tokens, **not** ~13k (only `memorize_fact` is sent) | server window prompt size; facts still saved |
| **3a** | Chat-only session (e.g. "hello there", "what time is it?", "tell me a joke") ×5 | consolidation **skipped** | console `Memory consolidation SKIPPED by Laya pre-filter`; `memory_prefilter_skip` in the reliability log |
| **3b** | A session with a real fact ("my sister got married in Cairo last week") ×5 | consolidation **runs** and the fact lands in `memory.md` | no skip event; new line in `Aster_Vault/memory.md` |
| **1a** | In-persona: any GUI action that only presses a key or types (`press_key`, `type_text`) | screenshot **skipped** | console `Post-action screenshot skipped for <action>`; tool result carries `[Screenshot omitted …]` |
| **1b** | In-persona: a click / scroll / form submit | screenshot **attached** (no skip line) | no skip console line |
| **2a** | In-persona: "what app is open?" / "read the error on my screen" | answered from window/UIA text — **no** screenshot | console `look_at_screen answered from window/UIA text (no screenshot taken).` |
| **2b** | In-persona: "is the chart green or red?" / "describe the layout" | **vision** path used (a screenshot is taken) | no text-path line; `system1_log` shows no `text_enough` verdict |
| **17a** | Layer B: browse a normal page, click a benign control ("Next", "Open") | click proceeds | no `confirm_send` refusal |
| **17b** | Layer B: click a destructive-looking control ("Place order" / "Complete purchase") | gated → refuses without `confirm_send: true` | refusal message from `is_send_like` |
| **22a** | In-persona: a current-events question (e.g. a 2026 news question) | `[Source: Firecrawl | live web]` | tool result header |
| **22b** | In-persona: an encyclopedic question ("what is quantum computing") | `[Source: Wikipedia | <real article title>]` | tool result header |
| **22c** | Force a bad Wikipedia candidate (an obscure/long question) | falls through to live web rather than answering from the wrong page | console `judged IRRELEVANT by Laya` or a live-web header |
| **Status** | **DONE** — all six verified live in Phase 1 | | `laya-integration.md` §Phase 1 |

---

## 3. Phase 2 — memory write gate

| ID | E2E test | Expected | Evidence |
|----|----------|----------|----------|
| **11a** | Ask Aster to memorize a fact that is already in the vault (verbatim) | write **skipped** | console `Memory write skipped by Laya gate`; `memory_write_skip` in the reliability log |
| **11b** | Same fact, reworded ("Mohamed owns no pets" vs "mohamed does not have any pets") | write **skipped** | as above |
| **10a** | Trigger an action-log write (send an email / a screen-watcher event) | write **skipped** as junk | skip event with `reason="classified as an action log…"` |
| **10b** | A genuinely new fact ("my favourite language is Python") | **written** to `memory.md` | new line; no skip event |
| **9** | Any of the above, plus a contradiction ("I do have pets") | **no** conflict note is produced (ID 9 is deliberately off) | no `replaces`/`contradicts` verdicts in `system1_log.jsonl`; no SUPERSEDE/CONTRADICTS text in the tool result |
| **REG** | Run 3 facts in the same second (batch memorize) | all 3 appear in `memory.md` **and** ChromaDB (no silent drop) | count lines; `recall_memory` finds all 3 |
| **Status** | **DONE** — ID 11 + ID 10 verified live; ID 9 measured out | | `laya-integration.md` §Phase 2 |

---

## 4. Phase 3 — people & contacts (TODO — not built yet)

> **ID 15 is owner-required** ("if I ask Aster to text someone, that should be forwarded
> to Laya so Laya can make the decision").

| ID | E2E test | Expected |
|----|----------|----------|
| **15a** | In-persona: "text George and say I'll be late" (exact name) | `send_discord_message` resolves the contact and sends |
| **15b** | Same with a nickname / typo / wrong case ("text adham", "tell geroge…") | the right contact is resolved (**no** "Unknown Discord contact"); case is preserved; a typo/guess asks for confirmation first |
| **15c** | A name not in `CONTACTS` at all ("tell my brother", "message the plumber") | asks for clarification, does **not** guess a contact, nothing is sent |
| **15d** | Confirm flow: reply "yes" to the CONFIRM REQUIRED question | the send then happens (with `confirm=true`) |
| **15e** | "let George know I'll be late" (no verb+name pattern) | recognised as a relay via the Part C gate |
| **13a** | Discord: a friend whose gender Laya cannot tell | Aster **asks** them; the answer is saved |
| **13b** | Second time that friend messages | no repeat question (the saved record is used) |
| **13c** | Friend answers "she/her" | `remember_pronoun` stores it; the next reply says "Ma'am" and never re-asks |
| **13d** | Layer A: `laya_guess_gender` over the real contact names | female/male names classified; ambiguous ones → `unknown` (then ask) — **TODO (probe was interrupted)** |
| **14a** | Layer A: `laya_guess_relationship` over real names | sensible class or `unknown` → falls back to "friend" — **TODO** |
| **14b** | Discord prompt | renders "Mohamed's {relationship}, {name}" with no `KeyError` |
| **16** | A friend with many stored facts | only the relevant ones are injected (prompt shrinks); an empty selection injects all |
| **12** | `recall_memory` on a query with near-duplicate stored lines | the correct fact is ranked first (rerank); kernel off → original order |

> **Phase 3 status: SHIPPED, Layer A partly verified.** ID 15 verified live (see its note
> above). IDs 13/14/16/12 are covered by mocked tests (647 passing) but the Laya **gender**
> and **relationship** guesses have **not** been run against the real model yet — the live
> probe was interrupted. Run 13d/14a first when the E2E pass happens.

> **ID 15 status: Layer A DONE, Layer B TODO.** Verified live (2026-09-28): `Adham` /
> `adham` / `ADHAM` all resolve to the `'Adham'` key; `geroge`→george and `farrah`→farah via
> difflib; "ninja guy"→ninja via Laya (margin 0.62); "my brother" / "the plumber" correctly
> guess nothing. The `[CONFIRM REQUIRED]` path and the relay directive were verified with
> mocked sends — **the live send + confirmation round-trip (15a/15d) still needs a real
> Discord run with a safe target.**

---

## 5. Phase 4 — live call + daemons

| ID | E2E test | Expected | Layer |
|----|----------|----------|-------|
| **19a** | LiveKit: awake, then talk to another person in the room (not to Aster) | **no** brain turn; console shows `Ignored — not addressed to Aster` + the verdict/margin line | B (live) |
| **19b** | LiveKit: a real request to Aster, **without** saying his name | answered normally (fail-open) | B (live) |
| **19c** | LiveKit: any request containing "aster" | answered with **no** Laya call (the word-boundary shortcut) | B (live) |
| **19d** | LiveKit: responsiveness while the gate runs | audio/STT/barge-in do **not** stall — the gate runs off the event loop (`asyncio.to_thread`) | B (live) |
| **20a** | Start a call, then let a proactive nudge become due | nothing is spoken **into** the call | B (live) |
| **20b** | Same for a distraction nudge (intervention daemon) | also suppressed — it now shares the moment gate | B (live) |
| **20c** | Owner away / brain mid-turn | nudge dropped, and it does **not** burn the unsolicited budget | B (live) |
| **5a** | Awareness poll with a window open | `screen` comes from window/UIA text; **one** image (webcam) in the call, not two | B (live) |
| **5b** | Awareness poll with the text path unavailable | falls back to the old two-image call | A |
| **4a** | Sentry on: sweep with a known face | classified from the face-match text, no vision call | B (live) |
| **4b** | Sentry on: an unrecognized face | `UNKNOWN` → the intruder flow (`WAITING_FOR_ID` + photo) still fires | B (live) |
| **4c** | Sentry with the kernel **off** | no extra face pass; the vision call runs exactly as before | A |

> **Phase 4 status: SHIPPED + QA-reviewed; Layer A DONE, Layer B TODO.**
> `tests/test_phase4_daemons.py` — **33 tests**, suite **680 passed**.

### Independent QA review (2026-09-28) — findings and fixes

A separate review agent audited the four gates for correctness and efficiency. What it
found, and what was done:

| Severity | Finding | Fix |
|---|---|---|
| **Critical** | **ID 19 blocked the asyncio event loop.** `_flush_pending_transcript` is a coroutine on the RTC loop and called Laya synchronously — ~0.31 s per gated turn, and up to ~35 s on a cold load (no startup prewarm). That stalls audio, STT, wake detection and barge-in. | `await asyncio.to_thread(_addressed_to_aster, transcript)`. Pinned by a test that the loop is not blocked. |
| **High** | **ID 19 could swallow a real request** on a confident "B". | Margin raised to **0.6** (vs the 0.25 default) and the verdict + distribution is now **logged** on every suppression, so a wrong one is visible and tunable. |
| **High** | **Sentry key collision.** Names used keys B, C, D… while `EMPTY` was also `E`, so with **≥4 enrolled faces** the 4th person's option was overwritten by "EMPTY" and became unselectable. | Reserved key space: `A` = owner, `U`/`E`/`Z` = specials, names get a disjoint range. Pinned by a test with 5 names. |
| **High** | **Sentry Route 2 was dead code** — `import base64` inside `execute_sentry_sweep` made `base64` function-local, so the fallback raised `UnboundLocalError`. Pre-existing, but it sits on the new escalation path. | `import base64` moved to module top. |
| **High** | **ID 20 had a second, ungated delivery point**: `intervention._trigger_intervention` duplicated the delivery logic and could speak over a live call — the exact bug ID 20 exists to fix. | It now calls the shared `awareness._good_moment_to_speak()` before mutating trigger state (so a deferral can retry). Pinned by a test. |
| **Medium** | **`_predict` was not serialized.** Four threads now share one resident model; Laya is not documented as reentrant. | `agent.predict` runs under a `_PREDICT_LOCK`. Pinned by a concurrency test. |
| **Medium** | **ID 4 paid the face pass even with the kernel off**, and double-decoded on escalation. | `_classify_frame_from_faces` returns `None` **before** any decode when the kernel is off — behaviour on default installs is unchanged. |
| **Medium** | **`"aster"` substring false-positives** (master, disaster, faster, plaster). | Word-boundary regex `\baster\b`. Pinned by a test. |
| **Low** | `text_state[:300]` could cut mid-element. | `_truncate_on_boundary` cuts on a `; ` node boundary. |
| **Low** | Doc drift (ID 5 says "label it with Laya"; ID 19 says 3-way). | Corrected in `laya-integration.md`. |

**Still open from the review:** the known-face list is capped by the key space (20 names) —
if more than 20 people are enrolled, the extras have no candidate and may be forced to
`UNKNOWN` (a false intruder alert). Worth revisiting if the enrolled set ever grows.

---

## 6. Phase 5 — tool loop + remaining gates (TODO)

| ID | E2E test | Expected |
|----|----------|----------|
| **8** | "read this link" / "search the web for X" | the right tool of the overlapping pair is chosen |
| **6a** | A turn needing a tool | the shortlisted tool set is **stable across turns** (prefix cache preserved) |
| **6b** | A turn needing an unusual tool | it is still in the set, or escalation sends the full list (no missing tool) |
| **6c** | Measure cold vs cached prefill across two turns | no regression vs the current ~13k prompt baseline |
| **18** | Enroll a voice sample that is a poor match | rejected (centroid not poisoned) |

---

## 7. Phase 6 — micro-decisions (TODO, lowest value)

Mood fusion arbitration · `_infer_mood` · TTS voice per contact · intervention
distraction classification · Whisper "Esther"→"Aster" · Sentry intruder naming.
One E2E each: trigger the path and confirm the new label/behaviour matches the old
heuristic's intent.

---

## 8. How to run the whole thing later

1. **Restart** llama-server + `main.py` (current code) — the gates only exist in a
   fresh process.
2. Confirm **P1–P6** above.
3. Run **Layer A** probes for every DONE/TODO item that has one — fast, safe, no
   side effects. These can be scripted into one file (see the template in §0) and
   run in a single pass; the resident model makes each call ~0.3 s.
4. Run the **Layer B** in-persona items from Telegram/CLI, using a safe target.
5. After each run, diff the evidence: `system1_log.jsonl` (verdicts + margins) and
   `reliability_log.jsonl` (skip/retry events), plus `Aster_Vault/memory.md`.
6. Record results back into `laya-integration.md` under the matching phase status.

**Known-good reference values** (from the 2026-09-28 live probes, for comparison):

| Gate | Input | Observed |
|------|-------|----------|
| ID 17 | "Place order" / "Next page" | gated `True` / `False` |
| ID 2 | "is the chart green or red?" / "what app is open?" | `False` / `True` |
| ID 22 | Renzi paragraph vs the UNGA question | score 0.30 → no judgement; correct result 0.84 → answers |
| ID 1 | `press_key` / `click submit` | skip / attach |
| ID 11 | verbatim duplicate / natural paraphrase | margin 0.72 / 0.65 → skip |
| ID 10 | "Aster sent an email reply…" | skip (margin 0.55) |
| ID 9 | contradiction | margins 0.00–0.28 → **inert (fenced)** |
