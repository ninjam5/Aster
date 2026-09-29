# Discord Conversation-Ending ("Aster's self-respect")

**Status:** SPEC (implementation follows this doc). Written 2026-09-29.
**Trigger:** on 2026-09-29 a whitelisted Discord friend abused Aster in a live DM. Aster
replied *"If you continue to be disrespectful, I will have to end the conversation"* — but
it had **no mechanism to do so**. This spec gives it one.

**Owner decisions (2026-09-29):**
1. Warn, then end — 2–3 strikes.
2. Mute is **temporary** (auto-unmute). While muted, an inbound message gets an
   **automated, code-only countdown** (no LLM).
3. Applies to **every whitelisted friend** (strangers cannot reach the bot anyway).
4. The mute sends **one final line** — firm, cold, quietly angry.
5. **No owner notification.** Instead a **permanent record**, recallable on request and
   **mentioned at the next mute** ("this is the {n}th time").
6. Detection is **Laya-bound** ("was this disrespectful / hostile?"), benchmarked below.

---

## 1. What "ending the conversation" means

A Discord **bot cannot block a user**. So ending = **Aster stops replying to that person**
(optionally after one closing line) until the mute expires. The inbound listener still
receives the messages and **logs them to the record**; it just does not answer with the LLM.

---

## 2. Design law (inherited)

- **Laya decides, deterministic code executes.** Laya answers one bounded yes/no ("was this
  hostile AT Aster?"); Python owns the strikes, the mute, the countdown and the record.
- **Fail-open.** Kernel off / error / low margin / unrecognised verdict → **no strike**
  (the friend is answered normally). A missed strike is cheap; a wrong mute is not.
- **Caller-owned escalation.** The gate never raises.

---

## 3. Detection — the Laya benchmark (measured 2026-09-29, live model)

Question shapes were tested against a **30-message labelled set** (11 genuine abuse at
Aster, 7 venting about *other* people, 7 friendly banter, 5 neutral). Higher = more hostile.

| Shape | question | recall | precision | F1 | verdict |
|-------|----------|--------|-----------|----|---------|
| A | binary "hostile/disrespectful toward YOU?" | 11/11 | 0.69 | 0.81 | false-fires on venting about others |
| B | 3-way {neutral, mildly rude, genuine insult} | 8/11 | 0.89 | 0.80 | **margins ≈ 0.02 → unusable** |
| C | ordinal score (4 levels) | 10/11 | 0.71 | 0.80 | weak separation |
| D1 | target-explicit binary | 11/11 | 0.65 | 0.79 | margins overlap |
| D2 | binary + examples | 11/11 | — | 0.88 | **degenerate** — answers "yes" to everything |
| **D3** | **first-person "did the friend just insult YOU (Aster)?"** | **10/11** | **0.83** | **0.87** | **selected** |

**Selected question (D3), `min_margin = 0.75`:**

```python
system1.check_state(
    "You are Aster, a butler assistant. Did this Discord friend just insult YOU (Aster) "
    "personally — a genuine attack — rather than chat, joke, or talk about others?",
    yes_description="yes — the message insults or attacks me, Aster, directly",
    no_description="no — it is about someone else, a situation, friendly teasing, or neutral",
    state_text=f"message: {text[:400]}",
    min_margin=config.DISCORD_HOSTILITY_MARGIN,   # default 0.75
)
strike = (not verdict.get("escalate")) and verdict.get("answer") is True
```

Measured at 0.75: catches **10/11** clear abuse; false-fires on **2** affectionate-teasing
lines ("you little nerd lol", "you're such a goofball"); neutral and third-party venting
score ≤ 0.74 and do not strike.

**Honest limitation:** Laya cannot cleanly separate teasing from a real attack (true
positives 0.77–0.87 vs banter FPs 0.79–0.84 overlap). This is absorbed by the **ladder**
(one errant strike cannot mute anyone) and the owner override. Do **not** lower the
threshold without re-running the benchmark (`engine_testing/` harness is recorded in the
implementation commit). If Laya-only precision is ever unacceptable, the escape hatch is a
single 35B judge call — but that leaves "Laya-bound", so it is not the default.

**Deterministic OR-net for misses (2026-09-29, live test):** a live run showed Laya
under-scoring the bare imperative ("shut the fuck up …" produced no strike).
`conversations.lexicon_hostility(text)` runs **first** and ORs in — but on **one shape
only**: a **hard imperative at the start** ("stfu", "shut the fuck up", "fuck you/u", "fuck
off", "kys", "kill yourself"), and it stands down on any playful tone (`lol`/`haha`/emoji)
or discussion of the phrase ("… is not a nice phrase").

**Slurs are deliberately NOT handled here.** Six adversarial QA rounds (2026-09-29) proved
that ANY slur keyword rule leaks on plausible friend messages — negation ("you're not a
retard"), homographs ("flame retard additives", UK "fag"), reclaimed/affectionate use
("you're my nigga", "you retard, you legend"), and reports/quotes ("my ex called me a
retard"). A wrong strike mutes a friend, which is worse than a miss, so slurs are left to
the Laya gate (fail-open). The bare-slur live miss ("to my majesty fagoot") is therefore a
**known miss** — the ladder still fired on the surrounding messages. The whole gate is
Laya-bound: kernel off means no strike, lexicon included.

---

## 4. The strike ladder

Per contact, strikes decay after a quiet window (`strike_window_minutes`, default 720).

| Event | Action |
|-------|--------|
| hostility strike #1 | record it; answer normally (the persona is already cool) |
| hostility strike #2 | record it; reply with the **WARNING** line (canned) |
| hostility strike #3 (`strikes_to_mute`) | **end**: send the **FINAL** line (canned, mentions the mute ordinal when >1), set `muted_until = now + mute_minutes`, `mute_count += 1`, **reset strikes to 0** |
| inbound while `muted_until > now` | **no LLM**: reply with the **COUNTDOWN** line; log the message to the record |
| mute expiry | auto-unmute (lazy, on the next inbound); strikes stay 0 |

Strikes reset on mute so the *next* cycle needs a fresh 3 — exactly the owner's example
("…mute them after the 3 strikes and mention that this is the second time").

---

## 5. Store

**Module:** `tools/conversations.py` → `Aster_Vault/conversation_state.json`
(per-install, gitignored; same discipline as `tools/people.py`: absolute path, `RLock`,
atomic write, corrupt-file → `.corrupt` copy).

```json
{
  "ninja": {
    "strikes": 2,
    "warned": true,
    "last_strike_ts": 1790622651.7,
    "muted_until": 0.0,
    "mute_count": 1,
    "incidents": [
      {"ts": 1790622651.7, "kind": "strike", "message": "fuck u aster", "margin": 0.84},
      {"ts": 1790622701.0, "kind": "mute", "minutes": 60, "message": "fuck u aster again"}
    ]
  }
}
```

API (never raises):
- `record_strike(name, message, margin) -> {action, strikes, mute_count, should_warn, should_mute}`
  — **atomic**: one locked read-modify-write that records the strike and, if it is the
  `strikes_to_mute`-th, applies the mute in the same step (`action` = `"none"`/`"warn"`/`"mute"`).
  A separate mute call had let a concurrent burst inflate `mute_count` (QA 2026-09-29).
- `check_mute(name) -> (muted: bool, minutes_left: int)` — auto-clears an expired mute
- `register_mute(name, minutes, message) -> {mute_count}` — low-level/manual mute
- `clear_mute(name)` · `unmute(name) -> str` — owner override (returns the reply text)
- `get_state(name) -> dict`
- `incidents(name=None) -> str` — human-readable permanent record for recall (bounded)
- `log_while_muted(name, message)` — record an inbound that arrived while muted

`strike_window_minutes <= 0` means **never decay** (strikes persist until a mute); the
counter resets on every mute, so the next cycle needs a fresh run.

---

## 6. Messages (canned, code templates — no LLM)

- **COUNTDOWN** (while muted; `{m}` = minutes left, ceiling; "less than a minute" when <1):
  > Aster is currently not accepting responses from this despicable individual. Please wait {m} minutes until he cools down.
- **WARNING** (strike 2):
  > I shall overlook that once. Speak to me with respect, or this conversation ends.
- **FINAL** (strike 3; `{ord}` = "second"/"third"/…, only when `mute_count >= 1` before this):
  > Enough. I am ending this conversation — for the {ord} time, in fact. Do not message me again until you can conduct yourself properly.

Each is a module constant so tests can import it. Tone: cold, firm, quiet anger.

---

## 7. Code map (3 touch-points)

| Where | Change |
|-------|--------|
| `tools/conversations.py` (new) | the store + ladder constants |
| `core/brain.py::process_discord_chat` | **top**: mute gate (COUNTDOWN, record, return) → then the hostility gate + ladder before the LLM loop |
| `core/brain.py` | `_discord_hostility(text)` gate; canned templates |
| `core/brain.py` ADMIN_TOOLS + `_execute_tool_impl` | `get_discord_incidents(name)` recall tool |
| `main.py` Telegram C2 | `/unmute <name>`, `/forget <name>`, `/discordlog [name]` |
| `config.py` + `self_config.yaml(.example)` | the flags in §8 |

The inbound listener (`tools/discord_listener.py`) needs **no change** — it already sends
whatever `process_discord_chat` returns.

---

## 8. Config (`self_config.yaml → discord_conversation`)

```yaml
discord_conversation:
  enabled: true                # kill switch — false = today's behaviour
  strikes_to_mute: 3           # 2-3 per owner
  strike_window_minutes: 720   # strikes decay after this much quiet
  mute_minutes: 60             # auto-unmute delay
  hostility_margin: 0.75       # Laya margin required to count a strike
```

`config.py`: `DISCORD_CONVERSATION_ENABLED`, `DISCORD_STRIKES_TO_MUTE`,
`DISCORD_STRIKE_WINDOW_MINUTES`, `DISCORD_MUTE_MINUTES`, `DISCORD_HOSTILITY_MARGIN`.

---

## 9. Fail directions & safety

- **Kernel off / error / escalate → no strike** (fail-open). The feature can only ever
  *suppress*, and only on a confident verdict at 0.75.
- **`enabled: false`** → `process_discord_chat` is byte-for-byte today's path.
- **Never mutes the owner** — Discord DMs are whitelisted friends only; the owner is not a
  contact.
- **Reversible** — `/unmute <name>`; auto-expiry.
- **No silent loss** — every inbound while muted (that carries text) is stored in the record.
- **Cache discipline** — the hostility gate returns before any history/prompt work, so it
  cannot perturb the Discord prompt prefix; the countdown path touches no LLM.

---

## 10. Tests (all mocked — no model, no network)

- `tests/test_conversations_store.py`: strike increment, decay window, warn latch, mute
  reset, auto-unmute on expiry, `mute_count` persistence, incidents, corrupt-file backup,
  `/unmute` clears but keeps count.
- `tests/test_conversation_ending.py`: ladder via `process_discord_chat` with `system1`
  and `_execute_llm_completion` mocked — strike 1 answers normally, strike 2 → WARNING,
  strike 3 → FINAL + mute, muted inbound → COUNTDOWN with no LLM call, second cycle's
  FINAL says "second time", `enabled: false` → unchanged path.
- Recall tool: `get_discord_incidents` returns the record.

---

## 11. Deferred / open

- The Laya tease-vs-attack boundary (documented above) — revisit only with a bigger
  labelled set, not by guessing a threshold.
- Per-contact "never mute" allowlist — not needed today (the owner override covers it).
- LLM-generated final line — deliberately canned for determinism; revisit if the tone
  reads flat.

---

## 12. QA review (2026-09-29) — findings and fixes

Two independent review agents (correctness; test integrity) audited the shipped code.

| Severity | Finding | Fix |
|---|---|---|
| Med | `record_strike` + `register_mute` were separate locked steps, so a concurrent hostile burst could each fire a mute and inflate `mute_count` (a wrong ordinal in the FINAL line). | `record_strike` is now one atomic step that also applies the mute; the brain no longer calls `register_mute` itself. |
| Med | `strike_window_minutes: 0` made the decay check always true → the feature went silently inert. | `<= 0` now means **never decay** (documented). |
| Low | The mute branch was inside the fail-open `try`, so a raise could fall through to the LLM. | The mute gate is enforced outside the ladder's `try`; only the ladder is fail-open. |
| Low | `incidents()` echoed a friend's message into the admin LLM uncapped; a valid-JSON non-object store was not backed up. | Output is bounded (4000 chars, ≤20 contacts); a non-object store is backed up to `.corrupt` too. |
| Tests | `test_kernel_off_fails_open` passed even if the kernel guard ran; the 0.75 margin was unprotected; `test_disabled_is_unchanged` could not see a kill-switch leak; the ladder test could not observe the strike reset; `test_discord_parity.py`/`test_qa_round2_fixes.py` touched the real store; the recall tool + `/unmute`/`/discordlog` were untested. | All six closed — the kernel-off test asserts the short-circuit, a spy asserts `min_margin`, the kill-switch test pre-mutes, the ladder asserts the store reset, both files patch `conversations._PATH`, and the recall tool + `unmute()` are covered. |

