# Laya Integration Plan

**Status:** PLAN — nothing here is built. Written 2026-09-28.
**Source:** six parallel codebase surveys (Discord/contacts, memory, brain+tool loop,
automation+vision, voice/emotion, research/self-knowledge) + a live-state check.
**Line numbers** are from the tree as of this writing — re-verify before editing.

---

## 1. What Laya is, and the rule for using it

`core/system1.py` — **System-1 decision kernel**. Installed: **laya 0.3.20**. It is a
**non-autoregressive, text-only** model with three primitives:

- `choice` — pick one option from a predefined set (neutral keys A..R)
- `score` — score/rank candidates
- `noul` — deliberately avoided (documented label-bias bug #156)

It **never generates text**. It cannot plan, write a sentence, compose an argument, or
repair its own answer.

**The design law:** the LLM plans and generates free text; deterministic code executes;
**Laya only picks among candidates**. Every Laya verdict must have a **caller-owned
escalation path** — the module never raises; on low margin or any failure it returns
`escalate=True` and the caller falls back to the existing path.

**Therefore a use is a good fit only when both hold:**
1. the decision can be expressed as *"pick one of these N named options"* or *"score
   these candidates"*, and
2. the inputs are **text** (or text derived from images/audio: OCR, ARIA/UIA nodes,
   captions, transcripts, face-match labels).

**Live state today:** `USE_LAYA_KERNEL=True`; **21 decisions logged** in
`Aster_Vault/system1_log.jsonl` (margins 0.79 / 0.41 / 0.13 / 0.002→escalated). Wired into
exactly **one** production site: DOM-motor element ranking (`tools/dom.py:873`,
`_kernel_pick`). `pick_operation` and `check_state` exist but have **zero production
callers**.

---

## 2. Phase 0 — prerequisites (build these before any item below)

| ID | Item | Why it is needed | Complexity |
|----|------|------------------|------------|
| **P1** | **Generic `choice()` / `score()` wrappers** in `core/system1.py` | Only `pick_element` (DOM-specific), `pick_operation` (hardcoded op dict) and `check_state` (binary) exist. **Every item below needs a generic wrapper.** The internals are already generic — `_ask` (`:230`), `_verdict` (`:250`), `_extract_choice` (`:180`), `_gate_escalates` (`:216`) — so this is mechanical. `score` additionally needs a new extractor (`_extract_choice` returns only a discrete choice). | **Low** |
| **P2** | **Restore the margin threshold and calibrate it** | Live `laya_margin_threshold: 0.01` (default 0.25) → the escalation gate is **effectively OFF**. A logged decision with margin 0.13 (A 0.535 vs B 0.401 — a coin flip) was treated as confident. Every "Laya decides, LLM falls back" design is only as safe as this number. | **Low** (re-raise) / **Medium** (calibrate with `engine_testing/calibrate_system1.py`, `engine_testing/qa/stage4.md`) |
| **P3** | **`LAY A_KEEP_RESIDENT=true` for per-turn sites + batch questions** | Currently `keep_resident: false` → every `_predict` **loads and unloads** the checkpoint. Any per-turn classifier pays load latency each turn. `_predict` also accepts **all questions in one forward pass** — independent gates should be batched into a single call. | **Low** |

> Until P1–P3 are done, no item below can be safely or efficiently enabled.

### Phase 0 status — DONE 2026-09-28 (branch `qwen-3.6/laya-integration`)

| Prereq | Status | Detail |
|--------|--------|--------|
| **P1** | **DONE** | `choose(question, criteria, key, state)` and `score_candidates(levels, instruction, candidates, state, min_confidence)` added to `core/system1.py`, plus `ask_batch(questions, state)` — the batching seam (independent gates cost **one** forward pass). `criteria` accepts a dict or a plain list of labels. 21 new tests → `tests/test_system1.py` **51 passed**. |
| **P2** | **DONE (live)** | `laya_margin_threshold` **0.01 → 0.25** in `self_config.yaml`. Proper calibration from a precision curve (`engine_testing/calibrate_system1.py`) is still **pending** — it needs a labeled decision set. Needs a `main.py` restart to take effect. |
| **P3** | **DONE (live)** | `laya_keep_resident` **false → true** in `self_config.yaml`. |

**Measured (one-off probe, CPU):** Laya costs **~2.2 GB RSS** resident, **34.6 s** cold
load, **0.31 s** per `predict`. With `keep_resident: false` every decision paid a reload —
that is why per-turn use requires resident. **RAM caveat:** on a RAM-starved box (llama-server
MoE experts + browser), 2.2 GB resident is real money; `keep_resident: false` remains the
conservative choice for rare/background calls.

**Laya `score` semantics (important):** a score answer is the **expected level index** over
an ordered `criteria` list, **not** a per-candidate probability — so `levels` must run
**worst → best**, and ranking N candidates means N batched score questions (which
`score_candidates` does in one pass).

**Calibration caveat (corrected 2026-09-28):** Laya handles **up to ~20 options**
(`MAX_SHORTLIST = 18`) — the warning's `choice:11+` is a *temperature-bucket* label meaning
"11–20 options", not a capability limit. The checkpoint ships an out-of-range fitted
temperature for that bucket (0.1006 would sharpen the logits ~10x and publish a 0.24
coin-flip as 0.99), so Laya refuses it and falls back to the 0.5 floor. Effect: for
**11–20-option choices the published probabilities/confidence are uncalibrated** — the
distribution is *less* sharp than the checkpoint intended, so gates there over-escalate
(conservative), but they must not be treated as calibrated until the bucket is refitted.

### Phase 1 status — DONE 2026-09-28 (all six items)

| ID | Item | Where |
|----|------|-------|
| 7a | consolidation sends only `memorize_fact` | `_MEMORIZE_ONLY_TOOLS` in `core/brain.py` |
| 3 | Laya pre-filter skips the consolidation pass | `_session_may_hold_new_facts` + `evaluate_and_memorize(..., force=)` |
| 1 | post-action screenshot gate | `_needs_action_screenshot` / `_attach_action_screenshot` (5 GUI action sites) |
| 2 | vision-necessity gate for `look_at_screen` | `_screen_answerable_from_text` / `_foreground_text_state` |
| 17 | send/destructive gate | `tools/dom.py` `is_send_like` = word set + Laya paraphrase net |
| 22 | Wikipedia pick + answerability | `tools/rag.py` `_laya_pick_article` / `_answerability` |

**Live findings that changed the design (recorded so they are not re-litigated):**

- **ID 1 needed reframing.** *"Should the assistant look at a screenshot to verify this
  action?"* escalated on **every** case at threshold 0.25 (margins 0.04–0.22) → zero saving.
  Reframed to *"is the report complete and trustworthy enough to proceed WITHOUT looking?"*
  it is decisive: `press_key` and `type_text` **skip** (margins 0.63 / 0.75) while
  click/scroll/submit attach (escalate or "look"). Polarity: `B` = proceed without looking;
  skip only on a confident, non-escalated `B`.
- **ID 2 needed a hard blocklist.** The model called *"is the chart green or red?"*
  text-answerable. Any appearance word (colour / layout / look / chart / icon / font / …)
  now forces vision **before** the kernel. Verified: visual requests → vision; "what app is
  open" / "read the error" / "what does the window title say" → text.
- **ID 22 needed scoring, not a 3-way choice.** The choice form labelled an unrelated
  Matteo-Renzi paragraph "answers"; the ordinal score gives it **0.30** (no judgement) vs
  **0.84** for the real 2026 UNGA result. `_answerability` now uses `score_candidates` with
  thresholds (<0.4 irrelevant / <0.7 background / else answers) and returns "" (behave as
  before) when the single-candidate confidence gate escalates — the false positive is gone.
- **ID 17 is precise as shipped:** "Place order" / "Complete purchase" / "Confirm booking"
  gated; "Next page" / "Open settings" not.
- **ID 3's pre-filter cannot dedupe** against `memory.md` (the file is far too long for Laya),
  so it only screens out windows that are pure chit-chat/meta/questions; dedup stays the LLM's
  job. Any doubt runs the pass.

**Tests:** `tests/test_memory_consolidation.py` (12), `tests/test_laya_gates.py` (17),
`tests/test_rag.py` (+16), `tests/test_dom.py` (+7) — suite **541 passed**.

### Phase 2 status — DONE 2026-09-28 (one item measured OUT, not shipped)

| ID | Item | Result |
|----|------|--------|
| **11** | Dedup at every write | **SHIPPED** — the gate moved into `core.memory.memorize_fact`, the choke point every writer uses (the tool, the Discord sync, Gmail, the face server, vision, consolidation). Verbatim duplicate margin **0.72**, natural paraphrase **0.65** → skip. |
| **10** | Fact categorization / store routing | **SHIPPED as a junk filter** — `check_state` neutral-key binary. The email action log answers "not a personal fact" with margin **0.55** → skipped; real facts escalate → written. Only *confident junk* is dropped. |
| **9** | Contradiction / supersede | **MEASURED OUT — not shipped** (see below). |

**Shapes that work (recorded so they are reused):**

- Laya's strong shape is **candidates as `criteria`, the target in the state** (same as
  `pick_element` / `_laya_pick_article`).
- **Batching that shape degrades it**: the same verbatim duplicate scored **0.14** when
  three candidate-pick questions shared one `ask_batch`, vs **0.72** alone. Each pick
  question now gets its own call.
- The junk question works as a **neutral-key binary** (`check_state`), *not* as a
  categories-as-criteria `choose` — the latter answered "durable personal fact" for an
  email log at margin 0.70 (confidently wrong).

**ID 9 measured out — fences:**

- candidate-pick ("which saved fact does this REPLACE / CONTRADICT?") → margins **0.00–0.07**
- binary pair check ("do these two statements contradict?") → **0.16–0.28**, and it answered
  "contradicts" for an *unrelated* fact.

Both are inert at the 0.5 write threshold, so shipping it would cost two Laya calls per
write for nothing. The `conflict`/`supersedes` fields and the SUPERSEDE/CONTRADICTS note
plumbing are kept as the seam for a future calibrated detector. **Do not re-add without a
fresh measurement.**

**Also found and fixed while measuring:**

- **ID 3's pre-filter was unsafe.** At the global 0.25 threshold it answered "no new facts"
  for a genuine fact window ("my sister just got married in Cairo", margin 0.41) — i.e. it
  would have **skipped consolidating a real fact**. It now passes `min_margin=0.5`, so only
  a very confident "no" skips.
- **`memorize_fact` doc ids collided.** They were second-granular
  (`mem_{int(time.time())}`), so facts written in the same second were silently dropped by
  ChromaDB — now millisecond + content hash.

**Tests:** `tests/test_memory_write_gate.py` (19), `test_system1.py` `min_margin` (+5) —
suite **565 passed**.

### Phase 3 status — DONE 2026-09-28 (people & contacts + recall quality)

| ID | Item | Result |
|----|------|--------|
| **15** | Contact routing | **SHIPPED** (see its section above) — exact → send, typo → difflib, semantic → Laya, guess → confirm. |
| **13** | Pronouns + people-record | **SHIPPED** — new `tools/people.py` owns `Aster_Vault/people.json` (`{pronoun, gender, honorific, relationship, asked, source}`). Flow: stored → one Laya guess `{male, female, unknown}` → if unknown, **ask the person once** (recorded so it never nags) → `remember_pronoun` tool persists the answer. `_discord_honorific` now consults the record (config `DISCORD_FEMALE_NAMES` stays as the fallback). |
| **14** | Relationship | **SHIPPED** — `resolve_relationship` (stored → Laya guess → `"friend"` default) and the Discord prompt's hardcoded *"his friend"* is now a `{relationship}` placeholder. **All three** `.format()` call sites were updated — a missing key would have `KeyError`-ed every Discord message. |
| **16** | Friend-fact selection | **SHIPPED** — `get_user_facts(name, query=…)` scores each staged fact against the incoming message (one batched pass) and injects only the relevant ones. Without a query it is byte-identical to before. |
| **12** | Recall rerank | **SHIPPED** — `recall_memory` now retrieves 10 candidates and reranks to the best 3 via `_laya_rerank` (dense-only and hybrid paths). The vault is full of near-identical lines, which is exactly where RRF order ≠ relevance. |

**Why the people-record was the blocker:** contacts were only a name→Discord-ID map plus a
flat `config.DISCORD_FEMALE_NAMES` set, so there was nowhere machine-readable to *store* a
pronoun — the owner's example could not be persisted. `people.json` is that store.

**Conservative by construction (every item):** kernel off, escalate, failure, or "nothing
clears the bar" returns the previous behaviour exactly. For ID 16/12 that means the full
fact list / the original RRF order — never a silent drop.

**Not yet live-verified:** the Laya *gender* and *relationship* guesses over the real contact
names (the live probe was interrupted; it needs a ~35 s model load). Everything else is
covered by mocked tests. Listed as TODO in `laya-integration-testing.md`.

**Tests:** `tests/test_people.py` (39), `tests/test_phase3_selection.py` (14) — suite
**647 passed**.

---

## 3. Build order at a glance (first → last)

| # | ID | Item | Phase | Complexity |
|---|----|------|-------|------------|
| 1 | 7a | Consolidation call sends only `memorize_fact` (static fix, no Laya) | 1 | **Low** |
| 2 | 3 | Skip the 5-turn consolidation pass when nothing new | 1 | Medium |
| 3 | 1 | Post-action screenshot gate | 1 | Medium |
| 4 | 2 | Vision-necessity gate before `look_at_screen` | 1 | Medium |
| 5 | 17 | Send/destructive-action gate | 1 | Medium |
| 6 | 22 | Wikipedia candidate selection + answerability gate | 1 | Medium |
| 7 | 11 | Dedup at every write path | 2 | Medium |
| 8 | 9 | Contradiction / supersede detection | 2 | High |
| 9 | 10 | Fact categorization / store routing | 2 | High |
| 10 | 13 | Pronouns + structured people-record | 3 | Medium-High |
| 11 | 15 | Fuzzy contact resolution | 3 | Low |
| 12 | 14 | Contact relationship classification | 3 | Low-Medium |
| 13 | 16 | Which of a friend's stored facts to inject | 3 | Medium |
| 14 | 12 | Rerank recall candidates | 3 | Medium |
| 15 | 19 | "Is this utterance addressed to Aster?" | 4 | Medium-High |
| 16 | 20 | "Is now a bad moment to speak?" | 4 | Medium |
| 17 | 5 | Awareness scene from window/UIA text | 4 | Medium |
| 18 | 4 | Sentry person classification from face-match text | 4 | Medium |
| 19 | 8 | Tie-break among overlapping tools | 5 | Medium |
| 20 | 6 | Tool shortlisting over the 69 tools | 5 | **High** |
| 21 | 18 | Speaker-enrollment accept/reject | 5 | Low-Medium |
| 22 | 21 | Voice/emotion/awareness micro-decisions (bundle) | 6 | Low each |

**Ordering rationale:** cheap static wins first (they need nothing new), then the three
biggest *recurring* costs (a skipped LLM pass, two removed image prefills), then safety
and source-quality, then memory integrity (highest correctness value, needs new seams),
then people/contacts (your example), then the live-call and background daemons, and last
the high-risk cache-sensitive change (tool shortlisting) plus the low-value micro-cleanups.

---

## Phase 1 — Biggest wins, lowest risk

### [ID 7a] Consolidation call sends only `memorize_fact`
- **Where:** `core/brain.py:2360-2374` (`evaluate_and_memorize`), fired every 5 turns
  (`:3446`), on shutdown (`:3431`), and on `/memorize` (`:2895`).
- **What:** pass a one-tool schema (or a two-tool set with an explicit `NO_UPDATE`
  sentinel) instead of all 69.
- **Why it is needed:** the call asks the model to emit `memorize_fact` calls, but hands
  it the **full 69-tool schema** so it can choose tools it will never be allowed to use —
  every non-`memorize_fact` call is silently skipped at `:2367`. That is ~12.8k tokens of
  prompt per consolidation call, ~98% of it pure waste.
- **Complexity:** **Low** — deterministic, no Laya, no accuracy risk.
- **Escalation:** none needed; keep the existing `NO_UPDATE` fallback.

### [ID 3] Skip the 5-turn consolidation pass when the window holds no new fact
- **Where:** `core/brain.py:2315-2377`; triggers `:3446-3448`, `:3431`, `:2891-2897`.
- **What:** a binary `check_state` per recent turn — "does this contain at least one new
  durable fact?" — over a compact digest (each user turn + the top few similar stored
  facts). Any `A` or any `escalate` → run the existing full pass.
- **Why it is needed:** this is the **dominant recurring cost in the memory domain** — a
  full-history 35B call, with all 69 tool schemas **and the entire ever-growing
  `memory.md` as a blacklist string** (`:2329-2335`), every 5 turns whether or not
  anything was said. The only guard today is the turn counter.
- **Complexity:** **Medium** — needs P1 + digest building + a conservative escalate rule.
- **Escalation:** any doubt → run the pass unchanged.
- **Honest caveat:** Laya cannot ingest a 60k transcript. If the digest truncates, escalate
  rather than skip.

### [ID 1] Post-action screenshot gate
- **Where:** `core/brain.py:1969` (smart_click), `:2024` (smart_type), `:2039`
  (type_text), `:2053` (press_key), `:2081` (smart_scroll); injection at `:3211-3220`.
- **What:** after a GUI action, `check_state("did this succeed / is a follow-up needed?")`
  over the tool's own already-computed text (`result_text` / `change_note`) + the
  foreground window title. Only attach the screenshot when the answer is ambiguous/failed.
- **Why it is needed:** a screenshot is currently captured and injected into a multimodal
  `role:user` message **unconditionally after every GUI action**, forcing a full **image
  prefill on the next round — and it stays in history, so it is re-prefilled on every
  later round**. This is the single most expensive path in the system.
- **Complexity:** **Medium** — hot loop, needs P3, must fail-open.
- **Escalation:** `escalate`/failure → attach the screenshot (today's behavior). Never skip
  on a below-threshold or failure verdict.

### [ID 2] Vision-necessity gate before `look_at_screen`
- **Where:** `core/brain.py:1709-1732` (handler, `capture_screen_base64()` at `:1710`,
  LLM vision calls `:1724`, retry `:1727`); schema `:715`.
- **What:** a text-only gate over the request: "answerable from the app/window title and
  UIA element text" vs "requires actual pixel understanding". The first path is answered
  from `get_foreground_window_title()` (`tools/uia.py:39`) + a UIA snapshot.
- **Why it is needed:** the very common "what app is open / read the error" class currently
  pays a **full image prefill + an 800-token generation** (twice on the apathy retry) to
  describe the screen.
- **Complexity:** **Medium** — the text path must genuinely answer, or it becomes a wrong
  answer rather than a saving.
- **Escalation:** `escalate` → today's vision path unchanged.

### [ID 17] Send / destructive-action gate
- **Where:** `tools/dom.py:285-288` (`is_send_like`), `SEND_LIKE_WORDS` `:92-96`,
  enforcement `:955-959`; policy `:291-298`, `config.py:286-288`.
- **What:** `check_state("is acting on this element an outward/destructive action?")` over
  the node role + accessible name/text, with the existing word-set kept as a second,
  independent net.
- **Why it is needed:** the gate is a substring set that **misses paraphrases** ("Place
  order", "Post", "Complete purchase") and **false-fires** on benign ones ("share
  location"). This is the one safety-critical item.
- **Complexity:** **Medium**.
- **Escalation:** **must fail CLOSED** — low margin / kernel failure → treat as
  destructive and require `confirm_send`. Do **not** fall back to "proceed".

### [ID 22] Wikipedia candidate selection + post-retrieval answerability gate
- **Where:** `tools/rag.py:190-223` (search fallback loop), `_wiki_title_relevant` `:55-59`,
  `_tokens_match` `:41-52`; return points `:432-447`; caller `core/brain.py:1868`.
- **What:** (a) given the topic and up to 5 `wikipedia.search()` titles, `choice` the right
  article **or `NONE`**; (b) given the question and a truncated content lead, `choice`
  `answers / background-only / irrelevant`.
- **Why it is needed:** this is the **documented failure class** — the fuzzy matcher let the
  *Matteo Renzi* article be cached under a Netanyahu/UNGA-2026 question and answered from.
  There is **no post-retrieval quality gate at all** today.
- **Complexity:** **Medium** — needs `score` for (b); pure accuracy play (no LLM call saved).
- **Escalation:** `NONE` / irrelevant / low margin → fall through to the live web path, or
  return the existing "could not find — do not guess" message.

---

## Phase 2 — Memory integrity (highest correctness value; needs new write seams)

### [ID 11] Dedup at every write path
- **Where:** heuristic `core/brain.py:1499-1529`, reachable only from `:1694`; bypassing
  writers: `tools/gmail_tool.py:201`, `tools/face_server.py:219`, `tools/vision.py:368-375`,
  `tools/memory_manager.py:113-165` (`sync_unsynced_facts`), and `core/memory.py:15-30`
  itself.
- **What:** a 4-way `choice` — exact duplicate / paraphrase / new detail / genuinely new —
  against the top-K most similar existing facts.
- **Why it is needed:** the `difflib` heuristic is **not called on the paths that generated
  the live duplicates**. `memory.md:9/11` and `:10/12` are duplicate pairs; the vault also
  holds ~78 identical "Aster sent an email reply" lines.
- **Complexity:** **Medium** — must be installed at `memorize_fact`/`save_fact` themselves,
  not just `execute_tool`, or the hole stays open.
- **Escalation:** ambiguous → treat as **new** (write it); never silently drop a fact.

### [ID 9] Contradiction / supersede detection
- **Where:** write seam `core/memory.py:15-30`, `core/brain.py:1690-1699`.
- **What:** 3-way `choice` — consistent / newer-and-supersedes / contradicts.
- **Why it is needed:** **nothing resolves contradictions.** Live proof: `memory.md:9`
  "does not have any pets" coexists with `:19` "favorite pet is a parrot"; `:48`/`:49`
  ("planning to buy a car tomorrow" / "intends to buy Mercedes"). Aster can answer from a
  superseded fact.
- **Complexity:** **High** — needs a policy for what to do with the verdict (tombstone,
  flag, or ask) and a safe auto-supersede threshold. Highest correctness value, highest
  risk of corrupting the vault on a confident-wrong verdict.
- **Escalation:** contradicts / low margin → keep both and flag; never auto-delete.

### [ID 10] Fact categorization / store routing
- **Where:** `core/memory.py:15-30` (`memorize_fact` writes every string to `memory.md` +
  Chroma), `tools/notes.py:7-25`, `tools/mood_memory.py:121-138`, `tools/memory_manager.py:72-93`.
- **What:** single-label `choice` — personal fact / friend fact / project-preference /
  health-sensitive / task-or-note / system-event (do NOT put in memory.md) / mood trend.
- **Why it is needed:** writes are **unconditional and store-blind**, which is why
  `memory.md` contains action logs and screen-watcher events — and that file is fed verbatim
  into every consolidation prompt, so **junk permanently inflates every future 35B call**.
  It is also the prerequisite for retrieval filtering.
- **Complexity:** **High** — changes where data lands (needs a decision about existing junk,
  and a "do not store" path).
- **Escalation:** unknown → current behavior (`memory.md`).

---

## Phase 3 — People & contacts (your example) + recall quality

### [ID 13] Pronouns: name → {male, female, unknown} → ask → save
- **Where:** new pre-step at `core/brain.py:2622` (before the `brain_lock` at `:2624`);
  replaces/augments `_discord_honorific` `:1541-1546`; the ask arrives next turn in
  `process_discord_chat`; persistence via `save_personal_fact` `:2577-2584` →
  `tools/memory_manager.py:72`.
- **What:** `choice` over `{MALE, FEMALE, UNKNOWN}` from the name + known facts; on
  `UNKNOWN` (or low margin) Aster asks the person; the answer is saved for next time.
- **Why it is needed:** **no pronoun logic exists anywhere.** The honorific is **binary with
  no `unknown` state** — anyone not in `DISCORD_FEMALE_NAMES` is assumed "Sir" — and the
  output fixer regex `_enforce_discord_honorific` (`:1571-1593`) mangles words like "Sirius".
- **Complexity:** **Medium-High** — the model decision is trivial; the real work is the
  missing storage (below).
- **Blocker:** there is **no structured people-record**. Contacts are name→id
  (`Aster_Vault/discord_contacts.json`), gender is a flat config set, facts are free text.
  A small per-contact record (`{pronoun, honorific, relationship, …}`) is required, and
  note `config_writer._apply_live` (`:68-80`) cannot currently hot-update
  `contacts.female_names`.
- **Escalation:** low margin → the `UNKNOWN` branch (ask), never a guess.

### [ID 15] Fuzzy contact resolution for outbound sends
> **OWNER-CONFIRMED (2026-09-28):** *"if i ask aster to text someone, that should be
> forwarded to laya so laya can make the decision."* So this is **required, not optional**,
> and it covers the whole outbound-message path — `send_discord_message` *and* the relay
> parser — not just typos: **Laya picks the contact.** (Bumped: do it early in Phase 3.)

**STATUS — DONE 2026-09-28 (Parts A + B + C).** Owner decision: a non-exact match must be
**confirmed before sending**.

| Part | What shipped |
|------|--------------|
| **A** | `tools/discord_api.resolve_contact` — case/whitespace-insensitive lookup. Fixes the **live bug**: `discord_contacts.json` has `'Adham'` (capital) while every path lowercased the input first, so that contact was unreachable ("Unknown Discord contact 'Adham'") and the brain *forced* the lowercased name into the tool call. |
| **B** | `laya_pick_contact` — Laya picks among the real contact keys (candidates as `criteria`); returns the **canonical key**, so case is preserved by construction. |
| **C** | `_laya_relay_intent` — conservative neutral-key gate so phrasings the verb regex misses ("let George know…") are still recognised. Runs only after a cheap pre-filter (a send verb or a contact name in the text), and returns False on any doubt — this path can *force a send*, so a false positive is worse than a miss. |

**Measured live (the design follows the evidence):**

- **Typos are difflib's job, not Laya's.** `get_close_matches` resolved `geroge→george`,
  `farrah→farah`, `maski→masky`, and correctly returned *nothing* for "brother"/"plumber".
  Laya's margins on the same typos were **0.15–0.37** — and it guessed **tiger** for
  "my brother" (margin 0.19). So: exact → difflib (typos) → Laya (semantics).
- Laya *is* good at the semantic/verbatim case: "tell the ninja guy hello" → `ninja`,
  margin **0.62**.
- Verified end-to-end: `Adham`/`adham`/`ADHAM` all resolve to the `'Adham'` key;
  "tell geroge I will be late" → `george` (not exact → confirm); "tell my brother" and
  "message the plumber" → **no guess** (escalate).

**Confirmation gate:** `send_discord_message(target_name, message, confirm=False)` sends
immediately only for an **exact** name; a fuzzy/typo/Laya pick returns
`[CONFIRM REQUIRED: … resolves to 'X' … re-call with confirm=true]` and sends **nothing**.
The relay directive tells the model to ask the owner first when the target is a guess.
(The tool-level check also covers model-initiated sends, not just the relay path.)

- **Where:** `tools/discord_api.py:59-74` (`send_discord_message`), regex parser
  `core/brain.py:2151-2188` (`:2181` membership test).
- **What:** `choice` over the known contact names (nickname/typo/case tolerance).
- **Why it is needed:** the lookup **lowercases** the target but `CONTACTS` preserves case —
  `discord_contacts.json` has `"Adham"`, so `CONTACTS.get("adham")` is `None` and **outbound
  to Adham is unreachable** ("Unknown Discord contact 'Adham'"). A deterministic case-fold
  fix is the first line; Laya handles the rest.
- **Complexity:** **Low** (case-fold first, Laya second).
- **Escalation:** low margin → exact match / current error string / ask the owner.

### [ID 14] Contact relationship classification
- **Where:** none exists; the prompt hardcodes "`{owner_name}`'s friend"
  (`core/brain.py:2444-2450`).
- **What:** `choice` over {friend, family, partner, colleague, acquaintance, unknown},
  cached per contact in the structured people-record.
- **Why it is needed:** the system has no memory of **who people are** to the owner; register
  and relay policy are hardcoded.
- **Complexity:** **Low-Medium** (depends on the ID 13 store).
- **Escalation:** low margin → `UNKNOWN`, defer to the LLM once.

### [ID 16] Which of a friend's stored facts to inject
- **Where:** `tools/memory_manager.py:96-110` (`get_user_facts` joins **all** facts);
  injected every turn at `core/brain.py:2566`, `:2629`, `:1561`.
- **What:** rank/select over the friend's stored facts (cap ~18, neutral keys).
- **Why it is needed:** **all** facts are dumped every turn — `farah` has 9 including a
  ~1,500-char self-description. Direct KV/token cost that grows with history.
- **Complexity:** **Medium** — needs `score`/rank (a single `choice` cannot select a subset).
- **Escalation:** low margin → inject all (today's behavior).

### [ID 12] Rerank recall candidates
- **Where:** `core/memory.py:184-216` (`recall_memory`), fusion `:167-181`, final `[:3]` `:210`.
- **What:** `score`/`choice` over the fused candidates (dense top-10 ∪ BM25 top-10).
- **Why it is needed:** **no reranker exists** — RRF order is taken as final. The vault is
  dominated by lexically near-identical lines, exactly where similarity ≠ relevance.
- **Complexity:** **Medium** — needs the `score` wrapper.
- **Escalation:** low margin → keep the raw RRF order (byte-identical to today).

---

## Phase 4 — Live call + background daemons

### [ID 19] "Is this utterance actually addressed to Aster?"
- **Where:** `webrtc_bridge.py:322-341` (`_consume_stt_events`) → `_flush_pending_transcript`
  `:519-526` → `_start_response_pipeline` `:351`; wake state `:571-577`.
- **What:** 3-way `choice` — addressed to Aster / conversation with another person / media
  or background speech.
- **Why it is needed:** once awake, **every** final transcript becomes a full 15-round admin
  turn. A side conversation or TV audio caught within the 300 s timeout is treated as a
  command to Aster.
- **Complexity:** **Medium-High** — latency-sensitive live path; must fail-open.
- **Escalation:** low margin → send to the brain (today's behavior).
- **Non-fit note:** the pre-transcript **barge-in** decision is NOT a Laya fit — Whisper
  emits no text at that instant (`local_stt.py:183-185`).

### [ID 20] "Is now a bad moment to speak?" (proactive gate)
- **Where:** `tools/awareness.py:370` / `:396` / `:433` / `:489` / `:531`; delivery
  `_push_nudge` `:301`; also `tools/intervention.py:150-161`.
- **What:** 3-way `choice` — speak now / defer / drop, over `call_is_active()`, presence,
  quiet hours, budget, seconds since last user message.
- **Why it is needed:** the gates are boolean cascades that never check whether a call is
  active for **suppression** — `_push_nudge` only uses `call_is_active()` to pick a channel,
  so an ambient nudge can be **spoken into an active call**.
- **Complexity:** **Medium**.
- **Escalation:** low margin → current heuristic result.

### [ID 5] Awareness scene description from window/UIA text
- **Where:** `tools/awareness.py:121-172` (`_brief_scene_describe`), called `:646-650`;
  lighting keyword block `:165-171`.
- **What:** derive the **screen** field from `get_foreground_window_title()` + UIA node names
  and label it with Laya; keep the webcam frame path (Laya cannot read pixels).
- **Why it is needed:** a periodic background call carries **both a screen and a webcam
  image** every `AWARENESS_INTERVAL` (default 300 s), competing with real turns on the single
  llama-server instance.
- **Complexity:** **Medium** — must not degrade the genuinely-visual cases.
- **Escalation:** low margin → current `_brief_scene_describe` call.

### [ID 4] Sentry person classification from face-match text
- **Where:** `tools/sentry.py:20` (`_analyze_frame_with_llm`), called `:61`; interval
  `SENTRY_INTERVAL = 5` `:10`; face-recognition path `:99-135`; daemon disabled at
  `main.py:664`.
- **What:** `choice` over the known-face candidates ∪ `UNKNOWN` ∪ `EMPTY`, fed the
  `face_recognition` match text instead of the image.
- **Why it is needed:** every sweep sends the webcam frame to the **vision LLM** — an image
  prefill **every 5 s** while Sentry runs.
- **Complexity:** **Medium** — and **latent**: the daemon is currently off by default.
- **Escalation:** low margin → `_analyze_frame_with_llm` (today's behavior).

---

## Phase 5 — Tool-loop economics (risky) + remaining gates

### [ID 8] Tie-break among overlapping tools
- **Where:** `research` `core/brain.py:991-998` vs `browse_web` `:1001-1045`; `save_note`
  `:1306-1319` vs `memorize_fact` `:652-665`; `smart_type` `:1186-1207` vs `type_text`
  `:1284-1302`.
- **What:** `choice` over the overlapping cluster, criteria = the distinguishing clauses.
- **Why it is needed:** near-duplicate schemas are a known source of harness `single_tool`
  flakiness (`C1-06`). Accuracy play.
- **Complexity:** **Medium**.
- **Escalation:** low margin → leave the choice to the model (full set).

### [ID 6] Tool shortlisting over the 69 tools
- **Where:** `tools=ADMIN_TOOLS` at `core/brain.py:3051-3057`; schemas `:375-1493`
  (~51.3k chars ≈ **12.8k tokens**); Discord already hand-shortlists to 3 at `:2489-2540`.
- **What:** pre-select the relevant subset of tool names from the user text + context, then
  send only those.
- **Why it is needed:** ~13k of a ~13.2k-token prompt is tool schemas — 22% of the 60k
  window, on every completion.
- **Complexity:** **HIGH — and the saving is not free.** The tool block is **prefix-cached**;
  a per-turn *varying* set **invalidates the cache** and each change costs a cold ~13k
  re-prefill (~25 s at measured prefill rates). Design for a **stable per-session set**, or
  latency gets *worse*. Real wins: cold calls, cache evictions, context headroom, accuracy.
- **Escalation:** low margin / failure → send the full `ADMIN_TOOLS` (needs P2 first).

### [ID 18] Speaker-enrollment accept/reject
- **Where:** `tools/voice_recognition.py:297-324` (`identify_and_maybe_learn`), thresholds
  `:18-21` (`LEARN_THRESHOLD=0.50`), `accumulate_sample` `:252`.
- **What:** `choice`/`score` — enroll this sample under the best match, or reject, using
  top-1 cosine **plus the top1−top2 margin**.
- **Why it is needed:** a single absolute threshold with **no runner-up check**
  auto-accumulates samples and **silently poisons the speaker centroid** (cap 15 samples).
- **Complexity:** **Low-Medium**.
- **Escalation:** low margin → keep the current threshold decision; optional owner confirm.

---

## Phase 6 — Micro-decisions (low value, do last)

### [ID 21] Voice / emotion / awareness micro-decisions (bundle)
All `choice`-only, all replacing small heuristics, all ~0 latency value (accuracy/UX only):

| Sub-item | Where | Why |
|---|---|---|
| Mood-fusion arbitration | `tools/emotion_recognition.py:376-395` | Fixed priority (text>voice>face) when tiers disagree. Note: the policy is **deliberate** (anger-vs-happy), so low value / riskier. |
| `_infer_mood` label | `tools/awareness.py:190-214` | Keyword cascade injected into **every** turn via the context block. |
| TTS voice / register per contact | `local_tts.py:148-159`, `tools/audio.py:13-29` | One global `VOICE_NAME`; capability does not exist. |
| Intervention distraction classification | `tools/intervention.py:30-40`, `config.py:429-433` | Hardcoded substring dict; new sites/renamed apps are invisible. |
| Whisper "Esther" → "Aster" | `local_stt.py:176-177`, `:241-242` | Unconditional rewrite corrupts a real person named Esther. |
| Sentry intruder-name extraction | `main.py:560-577` | An LLM call to map a reply to a name (Sentry only). |

- **Complexity:** **Low** each; **Escalation:** current heuristic result.

---

## 4. Honest non-fits (do NOT attempt)

- **Barge-in / interrupt** (`webrtc_bridge.py:315-319`) — Whisper declares
  `streaming=False, interim_results=False` (`local_stt.py:183-185`), so at START_OF_SPEECH
  there is **no text** to feed a text-only kernel. Must stay audio-side (VAD/energy).
- **Wake-word detection / threshold** — pure audio, no text.
- **Image / audio understanding or generation** — Laya cannot consume tensors or generate.
  Only **text derived** from media is usable.
- **Per-line log forwarding** (`tools/diagnostics.py:45-60`) — one Laya pass per stdout line
  is the wrong shape and would be crushed; batch/window-level `score` only.
- **Research output summarization** (`core/output_compression.py:68`) — the summarizer must
  **generate text**; Laya can only choose whether to summarize.
- **`_discord_honorific` as a set-membership test** (`core/brain.py:1541`) — deterministic;
  the fix is a better structured record (ID 13), not a model.

---

## 5. Found along the way (non-Laya bugs worth fixing)

| Bug | Where | Impact |
|---|---|---|
| Admin tool loop has **no final `else`** — an unknown/hallucinated tool name is rewritten to *"Action completed successfully."* | `core/brain.py:1641-2118`; rewrite at `:3149-3150` | **Silent false success.** The Discord loop validates (`:2676`); admin does not. |
| Only `tool_calls[0]` is executed; extra calls in one message are dropped | `core/brain.py:306-324` (vs `_extract_all_native_tool_calls` `:327-346`) | Leaves N `tool_calls` with one `role:"tool"` reply — malformed history. |
| `is_fact_already_known` reads `Aster_vault/memory.md` (lowercase `v`) | `core/brain.py:1501` | Case-sensitive filesystem bug (already noted in `AGENTS.md`). |
| `memorize_fact` doc IDs are second-granular | `core/memory.py:25` | Multiple facts written in the same second collapse in ChromaDB, silently. |
| `send_discord_message` case mismatch | `tools/discord_api.py:59-74` | Outbound to `"Adham"` is unreachable. |

---

## 6. Suggested first sprint

1. **P1 + P2 + P3** (the enablers — a day).
2. **ID 7a** (static consolidation schema trim — minutes).
3. **ID 3** (consolidation pre-filter — the biggest recurring LLM saving).
4. **ID 1** (post-action screenshot gate — the biggest per-action saving).

Then re-measure with `engine_testing/harness.py` (`--repeat`) and the live
`Aster_Vault/system1_log.jsonl`, and calibrate the margin threshold on real decisions
before widening to the memory-write items (ID 9/10/11), where a confident-wrong verdict
corrupts the vault.
