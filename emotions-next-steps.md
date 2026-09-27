# Emotion Detection — Next Steps

`[Mood: <state>]` rides next to every user message before it reaches the brain; the
persona (`gogi.md`) reacts in tone. **All three mood-*triggered* features below are
now BUILT** (Idea 3 first as the shared foundation, then Ideas 1 & 2):
- **Idea 1** — proactive check-ins (`tools/awareness.py`).
- **Idea 2** — inline ambient-action offers (`tools/mood_actions.py`).
- **Idea 3** — mood-trend memory (`tools/mood_memory.py`).

All three are **debounced over several turns** (act on a *sustained* mood via
`get_sustained_mood()` / `get_mood_streak()`, never a single blip) and **opt-in**
(Ideas 1 & 2 default OFF), so Aster never feels like it's surveilling. Ideas 1 & 2
coordinate through a shared streak-guard + touch cooldown so they never double-nudge.
Per-feature detail, tests, and failure modes are in each section below. The
"original roadmap notes" subsections are kept for historical context.

---

## Idea 1 — Mood-gated proactive check-ins (reuse the Initiative/Awareness dial) — ✅ BUILT (2026-06-21)

> **Status: implemented (opt-in, default OFF).**
>
> **Aim.** Give Aster the instinct a close friend has: when you've been *heavy*
> (sad/anxious) or *buzzing* (happy) for a while and then go quiet, it gently breaks
> the silence — "you've gone quiet and a bit heavy, want to talk or just want
> company?" — instead of waiting to be addressed. It never fixes, never nags.
>
> **How it works.** `_maybe_trigger_mood_checkin()` (`tools/awareness.py`) runs once
> per Awareness-daemon tick (the daemon is already live), mirroring
> `_maybe_trigger_compliment()`. Pipeline: `emotion_recognition.get_mood_streak()`
> gives the debounced sustained mood + a streak id → require mood ∈
> {sad, anxious, happy} → **quiet gate** (newest `_recent_user_msgs` timestamp ≥
> `MOOD_CHECKIN_QUIET_SECONDS`, default 90s — this is the *split-by-context* rule that
> hands active chat to Idea 2) → per-streak guard (`streak_id != _last_checkin_streak`)
> → not `mood_touch_recent(MOOD_TOUCH_COOLDOWN)` (don't stack on an Idea-2 offer) →
> `_budget_remaining()` (the per-hour `INITIATIVE_LEVELS` cap) and the new
> `mood_checkin` capability flag (True at initiative ≥ medium). Delivery reuses the
> existing `_push_nudge()` (LiveKit if a call is live, else Telegram text + Kokoro VN).
>
> **What shipped.** `tools/awareness.py` (`_maybe_trigger_mood_checkin`,
> `_seconds_since_last_user_msg`, daemon call); `config.py` + `self_config.yaml`
> (`emotion.proactive_checkin` → `MOOD_CHECKIN_ENABLED`, `MOOD_CHECKIN_QUIET_SECONDS`,
> `MOOD_CHECKIN_COOLDOWN`; `mood_checkin` flag added to `INITIATIVE_LEVELS`); toggle
> `toggle_mood_checkins` + `/checkins on|off`. Reuses — not rebuilds —
> `get_mood_streak()`, `_push_nudge()`, `_budget_remaining()`, `INITIATIVE_LEVELS`.
> (Note: the original plan to add a buffer to `record_user_message` was superseded by
> the `get_sustained_mood()`/`get_mood_streak()` foundation built in Idea 3.)
>
> **Tested by `emotion-test.py`:** sad/happy streak + quiet → one check-in; active
> (not quiet) → none; budget exhausted → none; same streak → at most once; neutral →
> none; gentle-company / celebrate wording.
>
> **Possible derailments.**
> - **Needs the Awareness daemon alive.** If Awareness is disabled, Idea 1 never runs
>   (it's a daemon trigger). Idea 2 is unaffected (inline).
> - **In-memory streak resets on restart** — a sustained mood is "forgotten" across a
>   process restart; the next streak starts fresh. Acceptable.
> - **High-arousal false positive** (fast/energetic neutral → happy) could trigger an
>   unwanted celebrate. Mitigated by the sustain debounce + opt-in + quiet gate.
> - **Quiet gate depends on `_recent_user_msgs`** — only real user turns feed it
>   (system nudges excluded), so the "gone quiet" read is honest.

## Idea 1 — Mood-gated proactive check-ins (original roadmap notes)

Feed the running mood into `tools/awareness.py`'s existing initiative system. When
`sad` or `anxious` persists across N consecutive turns and `INITIATIVE_LEVEL` allows
an unsolicited turn, Aster opens a gentle check-in unprompted:

> "you've gone quiet and a bit heavy — want to talk or just want company?"

When `happy` / `hyped` is sustained, it leans into hype and celebrates. No new daemon
needed — this is just a mood input to the proactive logic already there, gated by the
per-level `unsolicited_max_per_hour` matrix in `config.INITIATIVE_LEVELS` so it can
never nag.

**Files to touch:** `tools/awareness.py` (add mood window buffer to
`record_user_message`; gate `_maybe_fire_initiative` on sustained mood), `config.py`
(a `MOOD_SUSTAIN_TURNS` knob, e.g. default 3).

---

## Idea 2 — Mood → ambient-action policy (music / volume / distraction-dimming) — ✅ BUILT (2026-06-21)

> **Status: implemented (opt-in, default OFF). Offer-only — never auto-executes.**
>
> **Aim.** While you're *actively chatting*, let a sustained mood shape what Aster
> *offers* (not does): calming music + quieter volume when you're down, closing a
> distraction when you're frustrated, hype music when you're up. The room is never
> hijacked — Aster asks first; the action only runs if you say yes (normal tool call).
>
> **How it works.** `tools/mood_actions.py` is a thin offer-only policy.
> `maybe_mood_action_nudge()`: gate on `MOOD_ACTIONS_ENABLED` →
> `get_mood_streak()` (debounced by `MOOD_SUSTAIN_TURNS`) → mood ∈ policy
> {sad, anxious, frustrated, happy} → one-offer-per-streak (`streak_id`) + off
> `MOOD_ACTIONS_COOLDOWN` → `note_mood_touch()` and return a short
> `[System Internal: …offer…]` string. `process_user_input` (`core/brain.py`, right
> after `_log_turn_mood`) appends that nudge to the current plain-text turn (skips
> system nudges, `[NATIVE_*]` payloads, Discord-routing requests) so Aster's
> *same-turn* reply naturally voices the offer. The actual tools
> (`play_spotify_playlist`/`play_liked_songs`, `set_volume`, `close_distraction_window`)
> are only reached if Mohamed agrees.
>
> **What shipped.** `tools/mood_actions.py` (new); brain wiring; `config.py` +
> `self_config.yaml` (`emotion.mood_actions` → `MOOD_ACTIONS_ENABLED`,
> `MOOD_ACTIONS_COOLDOWN`); toggle `toggle_mood_actions` + `/moodactions on|off`.
>
> **Tested by `emotion-test.py`:** each mood → correct offer; neutral / non-sustained
> → none; same streak → offered once; new distinct streak → offers again; cooldown
> blocks a new streak within the window; disabled → none.
>
> **Possible derailments.**
> - **High-arousal false positive** (energetic-neutral → happy/frustrated) → an
>   unwanted offer. Mitigated by sustain debounce + opt-in + offer-only; lever is
>   `EMOTION_MIN_CONFIDENCE`.
> - **History pollution** — the offer text is appended to the user message, so it
>   persists in conversation history. Kept short; acceptable for v1.
> - **Offers can fail downstream** — if the user accepts but no Spotify device is
>   active, the underlying tool returns an error string (existing behavior, not new).
> - **Voice/image turns don't get an inline offer** (we only append to plain text) —
>   the streak still counts; the offer simply surfaces on a later text turn.

## Idea 2 — Mood → ambient-action policy (original roadmap notes)

A thin policy layer maps a *sustained* detected mood to suggestions using tools that
already exist:

| Mood | Offer |
|---|---|
| `sad` / `anxious` | Calming Spotify playlist (`tools/media.py`) + lower volume by ~20% |
| `frustrated` | Close the active distraction window (surfaces the Intervention "close" nudge) |
| `happy` | Hype playlist (`tools/media.py`) |

**Always offer, never auto-execute at first** — Aster asks "want me to put something
on?" so a misread never hijacks the room. Promote to auto-execute only once trust and
accuracy are established.

**Files to touch:** new `tools/mood_actions.py` (thin policy + debounce state);
wired into `process_user_input` in `core/brain.py` after the mood tag is embedded
(inject a `[System Internal: mood-action suggestion]` nudge into the turn when the
threshold is crossed).

---

## Idea 3 — Mood-trend memory (emotional continuity) — ✅ BUILT (2026-06-21)

> **Status: implemented.** This is the first mood-*triggered* feature shipped, and
> it lays the shared foundation (`get_sustained_mood()`) that Ideas 1 & 2 will reuse.
>
> **What shipped**
> - `tools/emotion_recognition.py` — `log_mood_turn(mood, context)` (appends
>   `Aster_Vault/emotion_log.jsonl` + feeds an in-memory rolling window),
>   `get_sustained_mood(min_turns)` (the debounce gate: returns a mood only when the
>   last N turns are the *same non-neutral* label — the shared foundation for Ideas
>   1 & 2), `read_mood_log()`, `prune_mood_log()`.
> - `tools/mood_memory.py` (new) — `maybe_flush_mood_summary()` reads the log on a
>   time-gated cadence and folds a rolled-up summary into long-term memory.
> - `core/brain.py` — `_log_turn_mood()` taps the `[Mood: X]` tag right after it's
>   embedded (uniform across text / image-caption / voice-note paths) and logs each
>   real turn; the existing auto-consolidation cycle (`MEMORIZE_EVERY_N_TURNS`) also
>   calls `maybe_flush_mood_summary()`.
> - **Storage decision:** summaries go to a dedicated `Aster_Vault/mood_trends.md` +
>   ChromaDB (id prefix `moodtrend_`), **not** `memory.md` — keeps the personal-facts
>   vault clean and `evaluate_and_memorize`'s dedup-blacklist prompt small, while
>   still being semantically recall-able.
> - **Config** (`config.py` + `self_config.yaml → emotion.mood_trend`):
>   `MOOD_TREND_ENABLED`, `MOOD_SUSTAIN_TURNS` (3), `MOOD_LOG_RETENTION_DAYS` (30),
>   `MOOD_FLUSH_INTERVAL_HOURS` (24), `MOOD_FLUSH_MIN_TURNS` (6),
>   `MOOD_FLUSH_NONNEUTRAL_FRAC` (0.4).
>
> **Gating (debounce, per the roadmap's "sustained, never a blip" rule)**
> - Per-turn: every real user turn is logged (slash commands / `[System Internal]`
>   nudges excluded — they reach the brain untagged).
> - Flush: fires only when `≥ MOOD_FLUSH_INTERVAL_HOURS` since the last flush **and**
>   `≥ MOOD_FLUSH_MIN_TURNS` logged turns exist in the window; mostly-neutral periods
>   (`< MOOD_FLUSH_NONNEUTRAL_FRAC` non-neutral) advance the clock but write nothing.
>
> **Test checklist** (`tests/test_mood_memory.py`, 15 cases, no llama-server needed):
> - [x] `log_mood_turn` appends JSONL, flattens whitespace, coerces invalid labels → `neutral`, no-ops when disabled.
> - [x] `read_mood_log` honours `since_hours` and skips malformed lines; `prune_mood_log` drops aged-out entries.
> - [x] `get_sustained_mood` — all-same-non-neutral returns the mood; one blip breaks the streak; neutral never counts; too-few turns → `None`.
> - [x] `build_mood_summary` — neutral-dominant period → `None`; non-neutral → distribution string + representative example snippet.
> - [x] `maybe_flush_mood_summary` — not-enough-turns doesn't advance the clock; a due flush writes `mood_trends.md` + state then time-gates the next call; neutral period advances the clock without writing.
> - [x] Brain `_log_turn_mood` tag-parsing smoke-tested across plain-text / image-caption / voice-note shapes; `[System Internal]` nudges skipped; no base64 leaked into `context`.

Log each turn's `(timestamp, detected_mood, brief_context_snippet)` to a lightweight
store and periodically fold summaries into the existing memory pipeline
(ChromaDB / `Aster_Vault/memory.md` via `tools/memory_manager.py`).

This lets Aster notice *patterns* a real friend would:

> "you've been frustrated and wired every evening this week — what's actually going on?"

Instead of reacting message-by-message, Aster builds a longitudinal read on how
Mohamed is doing. This is the piece that makes the best-friend persona feel like it
*knows* him over time rather than just in the moment.

**Files to touch:** `tools/emotion_recognition.py` (add `log_mood_turn(mood, context)`
appending to a rolling JSON log in `Aster_Vault/emotion_log.jsonl`);
`tools/memory_manager.py` or a new `tools/mood_memory.py` that periodically reads the
log, computes a summary ("last 24h mood distribution"), and writes it to ChromaDB /
`memory.md` via the existing `memorize_fact` path. Tie the periodic flush to the
existing `evaluate_and_memorize` cycle (`MEMORIZE_EVERY_N_TURNS`).

---

## Tier 2 — Facial emotion (multimodal fusion, the third channel) — ✅ BUILT (2026-06-23)

> **Status: implemented (opt-in, default OFF).** Adds the *vision* modality so Aster
> reads facial affect, completing the text + voice + face fusion.
>
> **Aim.** Aster can already *see* Mohamed via the Awareness webcam poll but didn't
> read his expression. Now it does: sitting there looking down or frustrated registers
> even when the words ("ok", "fine") don't — a real friend reads your face, not just
> your sentences.
>
> **Model.** HSEmotion (`hsemotion-onnx`, AffectNet 8-class EfficientNet-B2) served via
> `onnxruntime` on **CPU, eager** — mirrors the Tier-0 text model (~0 VRAM, sub-100 ms/
> face). The **ONNX** variant is used deliberately: the torch `hsemotion` package's
> pickled models break on the installed timm 1.x (`conv_s2d` attribute error), and
> `onnxruntime` is already present for the wake-word stack. 8-class → 5-vocab map:
> `Happiness→happy`, `Sadness→sad`, `Anger/Disgust/Contempt→frustrated`, `Fear→anxious`,
> `Surprise/Neutral→neutral`.
>
> **How it works.** `load_face_emotion_model()` / `detect_face_emotion(frame)` /
> `get_face_mood()` / `reset_face_mood()` / `fuse_face()` in
> `tools/emotion_recognition.py`. The **Awareness daemon** drives it: it reuses the
> webcam frame it already grabs each poll (**no second `cv2.VideoCapture`**, so zero
> contention with the gesture daemon), calls `detect_face_emotion(webcam_b64)`, and
> stores the result in `current_context["face_mood"]`. The crop comes from
> `tools/vision.get_primary_face_crop_rgb()` (reuses `face_recognition`). The read is
> **EMA-smoothed** (`FACE_EMOTION_EMA_ALPHA`) across polls and confidence-gated
> (`FACE_EMOTION_MIN_CONFIDENCE`); a missed detection *holds* the last read, and leaving
> frame (`presence == "away"`) resets it. Two consumers:
> 1. **Ambient** — `_ambient_block()` renders a `Face read: X` line beside `Mood read:`.
> 2. **Fused** — `_maybe_tag_text_mood()` (`core/brain.py`) folds the latest face read
>    into the per-turn `[Mood:]` tag via `fuse_face()` (confident text wins; neutral
>    text falls back to face), so face feeds the debounce window → Ideas 1/2/3 +
>    mood-trend. (Voice-path face fusion is a deliberate v1 omission — Mohamed is often
>    not facing the camera mid-call.)
>
> **What shipped.** `tools/emotion_recognition.py` (Tier 2 fns + eager boot),
> `tools/vision.py` (`get_primary_face_crop_rgb`), `tools/awareness.py` (capture +
> `Face read:` line + `face_mood` field), `core/brain.py` (per-turn fusion + the
> `toggle_face_emotion` tool), `main.py` (`/faceemotion on|off`), `config.py` +
> `self_config.yaml` (`emotion.face` → `FACE_EMOTION_ENABLED`, `FACE_EMOTION_MODEL`,
> `FACE_EMOTION_MIN_CONFIDENCE`, `FACE_EMOTION_EMA_ALPHA`). Dep:
> `pip install hsemotion-onnx` (the model auto-caches to `~/.hsemotion/`; the loader
> pre-fetches the weights to work around hsemotion-onnx's own broken downloader).
>
> **Tested by `face-emotion-test.py`** (standalone mock, no onnxruntime/webcam/llama-server):
> - [x] 8-class → 5-label mapping for all eight raw labels.
> - [x] EMA smoothing damps a single off-frame (vs alpha=1.0 which flips immediately).
> - [x] confidence gate holds the previous read on a low-confidence frame.
> - [x] a missed detection holds the last read (doesn't wipe to neutral).
> - [x] `fuse_face` policy (base content wins; neutral base → face; both neutral → neutral).
> - [x] ambient block renders `Face read:` only when enabled.
> - [x] disabled path is a hard no-op (always `neutral`).
>
> **Possible derailments.**
> - **FER tops out ~60–67% on AffectNet**, lower in dim light / off-angle / glasses —
>   it's a confidence-gated, EMA-smoothed, debounced *ambient hint that fuses with
>   text/voice*, never a standalone trigger. Lever: `FACE_EMOTION_MIN_CONFIDENCE`.
> - **Stale read** — the fused face mood can be up to `AWARENESS_INTERVAL` (default 300s)
>   old; acceptable for an ambient signal. Lower `awareness.interval` for fresher reads.
> - **Disgust/Contempt → frustrated** can over-fire `frustrated`; same lever.
> - **Requires the Awareness daemon alive** (it drives the capture) — mirrors Idea 1.
> - **No dedicated camera open** by design; a future faster loop would reintroduce
>   gesture-daemon contention.

## Tier 1b — Ambient room audio / silent observer — ✅ BUILT (2026-06-25)

> **Aim.** Read Mohamed's vocal tone even when he isn't talking *to* Aster — upset on
> the phone about bills, venting to a friend — so Aster keeps emotional context during
> the long silent stretches the other tiers miss (they only fire on direct interaction).
>
> **How it works.** A new opt-in daemon (`tools/ambient_audio.py`) opens a local mic
> (`sounddevice`, 16k mono) and gates it through `webrtcvad`: it accumulates a speech
> utterance and flushes on trailing silence. Each utterance is **paused if a LiveKit
> call is active** (that path already reads him; speakers would echo Aster's TTS), then
> **owner-gated** — only processed if ECAPA `identify_speaker()` returns `OWNER_NAME`.
> The gate is the key design move: it rejects a visiting friend's voice, Aster's own TTS
> playback, and background music/TV in one check. Surviving utterances run
> `detect_ambient_voice_emotion()` — the **same IEMOCAP wav2vec2 model on a separate CPU
> copy** (`_get_cpu_voice_clf`, held resident, ~0 VRAM; the CUDA path the LiveKit/Telegram
> tiers ref-count is untouched), EMA-smoothed + confidence-gated like the face tier.
>
> **Why CPU + owner-gate (decisions with Mohamed).** The 3080 is saturated by llama-server,
> so the ambient model runs CPU-only (the user picked this over a resident-VRAM hold).
> Owner-gating wires in the existing voice-recognition model and was chosen specifically to
> keep a friend's anger / media audio from being attributed to Mohamed. VAD-gated capture
> keeps it from analyzing silence/typing.
>
> **What shipped.** `tools/ambient_audio.py` (daemon), `tools/emotion_recognition.py`
> (`_classify_voice_waveform` extracted + reused, `_get_cpu_voice_clf`,
> `detect_ambient_voice_emotion`/`get_ambient_voice_mood`/`reset_ambient_voice_mood`),
> awareness `Voice tone:` ambient line + face **owner-guard** fix, brain `_with_face`
> fusion chaining (**text > face > ambient-voice**), `toggle_ambient_audio` tool +
> `/ambientaudio` Telegram command, `emotion.ambient_audio` config block + `OWNER_NAME`.
> Feeds the sustained-mood window (`log_mood_turn(_, "(ambient room)")`, cooldowned) so a
> sustained ambient mood can drive proactive check-ins. Deps: `sounddevice` + `webrtcvad`.
>
> **Tests** (`ambient-audio-test.py`, 23 checks, no mic/deps/model/llama-server):
> - [x] 4-class IEMOCAP → 5-label mapping via the EMA argmax path.
> - [x] EMA damps a single off-utterance (and `α=1.0` flips, as a contrast).
> - [x] confidence gate + short-clip guard hold the previous read.
> - [x] fusion order text > face > ambient-voice.
> - [x] ambient block renders `Voice tone:` only when enabled.
> - [x] owner gate drops non-Mohamed, call-pause skips, neutral reads aren't logged.
> - [x] disabled path is a hard no-op (always `neutral`).
>
> **Possible derailments.**
> - **Speaker-ID misses** (ECAPA `< 0.70` → None) drop valid Mohamed clips — accepted
>   tradeoff for rejecting friends/echo; lever is the threshold / `AMBIENT_VOICE_GATE_OWNER`.
> - **IEMOCAP anger→happy** confusion (see *Known limitations*) — ambient voice is the
>   *lowest-precedence* signal, so it only fills a still-neutral text+face read.
> - **Privacy** — an always-listening room mic is significant; hence default OFF,
>   owner-gated, paused in calls, and **content is never transcribed/logged** (tone label
>   + `"(ambient room)"` only). Mic opens only while enabled.
> - **Stale tone** — a held read is dropped after ~10 min of no owner speech.

## Known limitations & tuning levers

These are live characteristics of the current detector worth knowing before building
on the mood signal.

- **High-arousal confusion (fast/energetic neutral speech → `frustrated`).** The
  IEMOCAP voice model reads *arousal* (energy) far more confidently than *valence*
  (positive/negative). Speaking fast or loud on neutral-content words ("hey how are
  you") can tag `frustrated` (or `happy`) even when you feel calm — the model can't
  tell "fast because excited" from "fast because angry." On voice paths the fusion
  defers to prosody when the words are neutral, so this surfaces most on quick,
  content-light utterances.
  - **Cheap fix / lever:** raise `config.EMOTION_MIN_CONFIDENCE` (`emotion.min_confidence`
    in `self_config.yaml`), e.g. `0.5 → 0.6`. Borderline high-arousal reads then fall
    back to `neutral` instead of committing to `frustrated`. Trades a little sensitivity
    for fewer false positives. Left at `0.5` by default — only bump it if false
    `frustrated`/`happy` on fast speech becomes annoying in daily use.
  - **Better (deferred) fix:** the **Per-turn EMA smoothing** below, or a swap to a
    valence/arousal *dimensional* model so the label isn't forced onto one of 4 acted
    classes.
- **Text model leans `sad` for angry content.** j-hartmann sometimes scores clearly
  angry text as `sad` rather than `frustrated` (e.g. "this person screwed me over").
  Still far better than the voice model's `happy`, and both pull supportive responses —
  but don't treat `sad` vs `frustrated` as a precise distinction when gating actions.

## Further horizons

Beyond the three ideas above:

- **Multimodal fusion** — ✅ **BUILT** (see "Tier 2 — Facial emotion" + "Tier 1b —
  Ambient room audio" above): text (Tier 0), direct voice (Tier 1), ambient room voice
  (Tier 1b), and face (Tier 2) now fuse into one `[Mood:]` tag with **text > face >
  ambient-voice** precedence. The remaining open work is a *confidence-weighted* blend
  (current `fuse_face` is a priority rule, not a weighted average) and folding face into
  the direct-voice (LiveKit) path.
- **Mood-conditioned TTS prosody** — vary Kokoro's `speed` knob with detected mood
  (slightly slower + softer for `sad`/`anxious`, punchier for `happy`). Requires
  exposing a per-call speed override alongside `config.VOICE_SPEED`.
- **Per-turn EMA smoothing** — replace raw per-message detection with an exponential
  moving average (`α ≈ 0.3`) so a single off-label response doesn't spike the read.
  Purely a post-processing wrapper around `detect_text_emotion` / `detect_voice_emotion`.
- **Aster self-affect (deferred)** — a CPU-only text emotion model run on Aster's
  *own* responses to give Aster a reported emotional state. Lightweight (same Tier 0
  model, different input), but only meaningful once the persona is stable enough that
  the model's output reliably reflects intent rather than noise.
