---
name: aster-debugging-playbook
description: Load this FIRST when anything in Aster misbehaves at runtime - no response, empty response, hallucinated tool actions, leaked tool syntax, garbage/looping output, VRAM creep, wrong clicks, silent TTS, missing transcription, dead Telegram/Discord/UI connections, failed memory recall, wrong mood tags. Symptom-to-triage table with discriminating experiments for each failure family.
---

# Aster Debugging Playbook

Triage discipline: run the **discriminating experiment** before touching code — a
cheap check that splits the hypothesis space in half. Most Aster failures
pattern-match to a settled battle (details and stories: `aster-failure-archaeology`).

**When NOT to use this skill:** you want to measure/benchmark →
`aster-diagnostics-and-tooling`; the system isn't built yet →
`aster-build-and-env`; you already know the cause and want to ship a fix →
`aster-change-control`.

## Master triage table

| Symptom | First check (discriminating experiment) | Likely cause → section |
|---|---|---|
| No response at all | `Invoke-RestMethod http://localhost:8080/health` | Server down/loading → §1 |
| Empty response after tools ran | Did the apathy nudge fire? (log/`/peek`) | Post-tool apathy → §1 |
| "Playing it now, Sir" but nothing happened | `/peek 5` — is there a tool_calls entry? | Hallucinated execution → §1 |
| `call:func{...}` or `<tool_call>` visible in a reply | Which path? `Select-String -Path config.py -Pattern "USE_NATIVE_TOOL_CALLS"` + real yaml setting | Leaked tool syntax → §2 |
| Same tool called repeatedly with same args | `/peek 20` — count identical calls | Degenerate loop (NO guard exists) → §2 |
| Replies degrade in long sessions | `/status` token estimate vs ctx | Long-context degradation → §1 |
| VRAM climbing / OOM | `nvidia-smi` per-process | Leak class → `aster-vram-discipline` |
| Click lands on wrong thing / nothing | Is the target a keyboard key? DPI scaling ≠ 100%? | GUI automation → §3 |
| Voice note not transcribed | One python process? CUDA DLL lines in boot log? | STT → §4 |
| Call connects but Aster never hears | Is the agent asleep? (face grey, wake state) | Wake word → §4 |
| TTS silent | Kokoro acquire/release logs; CUDA OOM fallback | TTS → §4 |
| Telegram silent | Is the sender the authorized chat ID? Token configured? | §5 |
| Discord friend gets no reply | Whitelist? Discord brain ≠ admin brain | §5 |
| UI panels empty / no logs | `Invoke-RestMethod http://localhost:8000/api/stats` (or any /api probe) | face_server → §5 |
| "I don't remember" but the fact exists | Keyword vs semantic query — try rephrasing | Memory → §6 |
| Facts not being saved | Dedup gate; consolidation cadence | Memory → §6 |
| Mood always `[Mood: neutral]` | `EMOTION_ENABLED`? confidence < 0.5? | Emotion → §7 |
| Wrong mood on energetic speech | Known IEMOCAP arousal confusion | Emotion → §7 |

## §1 Engine and brain loop

**Server unreachable/slow.** `http://localhost:8080/health`, then `/props` (also
tells you WHICH model file is served — the three launchers point at two different
models, see `aster-run-and-operate`). Boot warning `detected an outdated gemma4
chat template, applying compatibility workarounds` is expected and harmless.

**Garbage / role-confused / infinitely-echoing output.** You are in the
template-mismatch class — the costliest bug family in project history (three failed
architectures, 2026-05). Do NOT iterate on prompts. Check: custom Jinja template
flag present in the launcher? Multimodal turns carry the literal `<image>` marker?
Anything new between messages and the server? Then STOP and read
`aster-failure-archaeology` §engine-hell before changing anything.

**Hallucinated tool execution.** `_claims_tool_execution()` (`core/brain.py:185`)
cross-references user-intent patterns against response claims, catches residual
leaked syntax, injects a retry nudge, and re-runs ONCE (`hallucination_retried`
flag, `:2990`). If you see repeated hallucinations in one turn, the detector's
single-retry design is working as intended — the model is the problem, not the
detector. Never raise the retry count (loop risk by design).

**Post-tool apathy (empty final answer).** Gemma-4 sometimes returns "" after
successful tool rounds. The loop tracks `tools_executed`; on empty exit it injects
a neutral `[System Internal]` nudge, fires ONE more completion, then removes the
nudge from history. Verify via transient nudge in logs. If the final answer is
still empty, that's a real failure — capture the `/peek` and scenario.

**Long-context degradation.** `/status` for the estimate; remember it's `len//4` —
at 128k real tokens the estimate can be off. `/compact` is the mitigation. Also
check the `--ctx-size` (128000) vs `config.N_CTX` (default 131072) mismatch: the
trim budget (90% of N_CTX ≈ 118k est. tokens) can exceed what the server actually
accepts.

## §2 Tool calling

**Which path am I on?** Native (default): `tools=ADMIN_TOOLS` on the POST,
structured `tool_calls` on the response, results as `role:"tool"` +
`tool_call_id`. Legacy (rollback): `runtime.use_native_tool_calls: false` → XML
manual in the system prompt, `_legacy_xml_parse` regex extraction, results as
`role:"user"` text. `engine_testing/harness.py` ALWAYS exercises the legacy path
(never sends `tools=`) — don't let its behavior confuse a native-path
investigation.

**Leaked raw syntax in replies** (`<|tool_call>`, `call:name{...}`): the detector
catches some; display cleanup strips fragments. Persistent leakage on the native
path suggests template/server-version drift — treat as §1 template class.

**Degenerate loops** (same call repeated, A-B-A-B ping-pong): there is NO LoopGuard
yet — this is the owner-confirmed hardest live problem. Evidence to capture:
`/peek 20`, scenario, rounds used. The fix campaign (with a ranked solution menu)
is `aster-gemma-reliability-campaign` — don't improvise one inline.

**Tool ran but the model never saw the result:** on the native path the result
message's `tool_call_id` must match the id on the assistant's `tool_calls` entry.

## §3 GUI automation

- **Keyboard keys are `press_key`, NEVER `smart_click`.** The OCR once found the
  literal word "Enter" on screen and clicked it (settled battle).
- 2–4 s per `smart_click`/`smart_type` is EXPECTED (two full-screen pytesseract
  passes: normal + inverted for dark UIs, 2× upscaled).
- **Wrong coordinates:** check display scaling. Coordinates are corrected
  `pixel_in_upscaled/2 * (logical/physical)`; a new monitor/DPI change is the
  usual suspect. Vision is primary-monitor-only.
- **Element not found:** Track 1 needs OCR-able text scoring ≥0.5
  (SequenceMatcher/token-overlap/substring). Icon-only targets rely on Track 2
  (OmniParser YOLO fallback, downloads on first use, released after each use).
- `verify_action_result(goal)` runs a post-click Gemma check — read its text in the
  tool result before assuming the click worked.

## §4 Voice pipeline

**STT silent/missing (Telegram or call):** one shared Faster-Whisper `medium.en`
int8 instance (`local_stt.get_whisper_model()`). Boot must show the CUDA DLL path
patch working (`main.py:14-19`) — `cublas64` errors mean the nvidia pip packages
are missing (`aster-build-and-env` trap 1). ffmpeg on PATH is required for
Telegram OGG transcode.

**Agent never hears on a call:** it starts ASLEEP. Wake with the wake word (default
model `hey_jarvis` — the phrase is "hey jarvis" unless a custom model is
configured). Re-mutes after `WAKE_INACTIVITY_TIMEOUT` (300 s). Face shows grey
asleep state. Threshold 0.5 — raise/lower `runtime.wake_word_threshold` for false
accepts/misses.

**TTS silent:** Kokoro is lazy + ref-counted; look for
`[Aster Core] Booting Kokoro TTS 82M...` then `...offloaded from VRAM (no active
users)`. On CUDA failure it falls back to CPU rather than dying — a *slow* voice
often means it's on CPU because VRAM was full at acquire time.

**Speaker tag wrong/missing:** `[Speaker: X]` needs ECAPA similarity ≥ 0.35 and
clip ≥ 1.0 s; auto-learn needs ≥ 0.50 and ≥ 2.5 s. Unknown voices stash a temp
sample and prompt `/enroll <name>`.

**Stale/overlapping responses on a call:** epoch-based cancellation
(`_response_epoch`) plus barge-in on START_OF_SPEECH — if replies play over each
other, look there in `webrtc_bridge.py`.

## §5 Interfaces

**Telegram:** only the configured `authorized_chat_id` is served. No token ⇒
`TELEGRAM_AVAILABLE=False` ⇒ the whole bridge silently absent (fail-closed by
design). `/diagnostics on` forwards stdout to Telegram — the fastest remote
debugging tool.

**Discord:** inbound only from the whitelist; replies come from the SEPARATE
Discord brain (3 tools, own history) — "Discord can't control my PC" is by design,
not a bug. Honorific rewrites can mangle words (known regex fragility).

**face_server/UIs:** everything on `:8000`; probe any endpoint with
`Invoke-RestMethod`. UI mock fixtures render as offline fallback when the fetch
fails — data that looks "frozen/fake" usually means face_server is down, not a UI
bug.

## §6 Memory

**Recall misses:** `recall_memory` is dense-vector only (top-3 from ChromaDB) —
keyword-exact things (IDs, exact names) can miss; rephrase semantically. Last
resort: grep the verbatim daily transcripts `Aster_Vault/Conversations/`.

**Facts not saved:** dedup gate `is_fact_already_known()` (`core/brain.py:1462`)
blocks bidirectional-substring or ≥85% SequenceMatcher matches against memory.md —
NOTE it reads the path with a lowercase-v `Aster_vault/memory.md` (harmless on
Windows, real on case-sensitive FS). Auto-consolidation fires every 5 real turns
(`MEMORIZE_EVERY_N_TURNS`), so up to 4 turns of facts are in-flight at any time.

**History shrinking:** `trim_memory` silently pops oldest non-system messages over
budget; `/compact` replaces history with a summary block. Both are features.

## §7 Emotion / mood

- Always-neutral: master switch `emotion.enabled`; below-threshold scores
  (`EMOTION_MIN_CONFIDENCE` 0.5) tag neutral BY DESIGN.
- Energetic speech tagged frustrated/happy: IEMOCAP reads arousal, not valence
  (settled) — text content gates prosody in fusion; lever: raise min_confidence
  to 0.6.
- Angry text tagged sad: known j-hartmann lean; don't treat sad-vs-frustrated as
  precise.
- Face/ambient reads only fill a still-neutral text read (precedence
  text > face > ambient-voice) and are owner-gated — a friend's face/voice is
  deliberately ignored.
- Mood-feature "not firing": Ideas 1 & 2 are opt-in default OFF, debounced over
  `MOOD_SUSTAIN_TURNS`=3 same-label non-neutral turns, cooldowned, and check-ins
  additionally need Awareness running + initiative ≥ medium + quiet ≥ 90 s. Most
  "bugs" here are the gates working.

## Provenance and maintenance

Authored 2026-07-05 against live code.

- Detector/nudge anchors: `Select-String -Path core\brain.py -Pattern "_claims_tool_execution|hallucination_retried|tools_executed"`
- Path fork: `Select-String -Path config.py -Pattern "USE_NATIVE_TOOL_CALLS"`
- Dedup path casing still lowercase: `Select-String -Path core\brain.py -Pattern "Aster_vault"`
- Thresholds: `Select-String -Path tools\voice_recognition.py -Pattern "THRESHOLD"`
