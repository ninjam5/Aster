# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> For an exhaustive architectural baseline (VRAM budgets, frontend detail, per-tool tables, emotion-recognition loading workarounds, ambient audio pipeline details), see [`summary.md`](summary.md). This file is the working reference for editing code.

---

## Python Environment

**No virtual environments.** Install all packages globally:
```
python -m pip install <package>
```
This is enforced by `.github/instructions/python-global-packages.instructions.md`. If setup docs suggest a venv, adapt to a global-package workflow.

---

## Running the System

**Prerequisites — must be running before `main.py`:**
- `llama-server` (**BeeLlama** — `E:\Models\beellama-v0.4.7-bin-win-cuda-12.4-x64\llama-server.exe`, a llama.cpp fork with KVarN KV quantization) at `http://localhost:8080`, serving **Qwen 3.6 35B-A3B** (`E:\Models\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`, IQ4_XS MoE, ~3B active params/token) **plus** `E:\Models\Qwen3.6-35B-A3B-mmproj-F16.gguf` for vision (`--no-mmproj-offload`, runs from RAM). **KVarN KV cache** (`--cache-type-k kvarn4 --cache-type-v kvarn2 --kv-tail-tokens 1024`), **60k context** (`--ctx-size 60000`; must match `config.N_CTX`), MTP speculative decoding (`--spec-type draft-mtp`), **thinking OFF** (`--reasoning off`), `--n-cpu-moe 26`, `--threads 8` (P-cores only; E-cores hurt MoE throughput), `--ubatch-size 512`. The layer split is sized so **Faster-Whisper (CUDA, ~1.2 GB) stays resident** alongside the model (~10.9 GB total, ~1.4 GB VRAM headroom). Start with `start.bat`; `Start-all.bat` launches llama-server then **polls `/health` (up to 300 s) before starting Aster**, avoiding the boot-time memory race. (The Gemma-era engine is preserved on branch `gemma-4-e4b-lightweight`.)
- Tesseract OCR at `C:\Program Files\Tesseract-OCR\tesseract.exe` (override with `TESSERACT_CMD` env var).

```powershell
# Start Aster (headless CLI mode — main entry point)
python main.py

# Standalone LiveKit WebRTC voice agent
python livekit_agent.py

# Run the test suites (pytest, mocked LLM — no llama-server needed)
pytest tests/test_migration.py -v   # engine adapter + tool calling + failure guards
pytest tests/test_locator.py -v     # three-track GUI locator (UIA/OCR/YOLO+caption)
pytest tests/test_dom.py -v         # DOM motor (aria parser, shortlist, send gate)
pytest tests/test_system1.py -v     # System-1 kernel (mocked Laya)

# Run a single test
pytest tests/test_migration.py -v -k "test_name_here"

# Rebuild llama-cpp-python with CUDA (unused legacy helper for the old path)
python build_cuda.py
```

The desktop companion UI (`aster-ui/`) is a **Tauri** app — an animated 8-bit
reactor-core "face" with a LiveKit voice call, mute, and a resizable live log strip.
Minimizing the main window pops a small always-on-top circular reactor (the `mini`
window); clicking it restores. It talks to `main.py` via `tools/face_server.py`
(FastAPI on `:8000`). Requires the Rust toolchain + MSVC build tools installed once.
```powershell
cd aster-ui
npm install
npm run tauri build  # production build (~500 MB less RAM than dev mode) — use this for daily use
npm run tauri dev    # dev mode (hot-reload, heavier) — only needed when editing the UI
```

The full-featured dashboard UI (`Aster-UI/`) is a second **Tauri** app with section panels for
Activity Log, Dashboard stats, Memory CRUD, Skills toggles, Voice/Chat streaming, Notes,
and Socials status. It connects to `tools/face_server.py` (FastAPI on `:8000`) for all live data.
Frontend API layer: `Aster-UI/src/api/` (`config.ts`, `client.ts`, `useStats.ts`, `useChatStream.ts`,
`useLiveKitCall.ts`). Chat uses SSE (`POST /api/chat/stream`); logs use WebSocket (`WS /api/logs`).
```powershell
cd Aster-UI
npm install
npm run dev          # browser dev server at http://localhost:5173 (no Rust needed)
npm run tauri build  # production Tauri build — requires Rust toolchain + MSVC build tools
npm test             # vitest run (57 tests, no llama-server needed)
npm run build        # tsc + vite production bundle check
npm run lint         # eslint
```

**LiveKit devmode subprocess check:** `webrtc_bridge.py` runs `server.run(devmode=True)`. Devmode may fork a worker subprocess that duplicates Python+torch (+3-5 GB RAM). To verify: after a voice call connects, run `Get-Process python | Format-Table Id, WorkingSet64` — if two python.exe entries exist with multi-GB footprints, switch the `devmode=True` call in `webrtc_bridge.py:668` to `devmode=False` (or investigate in-process mode).

---

## Architecture

### Request Flow (Admin Brain)

```
User input (CLI / Telegram / WebRTC voice)
  → process_user_input()              [core/brain.py]
    → trim_memory()                   [sliding-window context management]
    → _execute_llm_completion()       [POST localhost:8080/v1/chat/completions, tools=ADMIN_TOOLS]
    → _extract_native_tool_call()     [read structured tool_calls off the response message]
    → execute_tool()                  [dispatch to tools/]
    → loop up to 15 rounds
    → return final text response
```

`_execute_llm_completion()` (`core/brain.py`) is the single LLM adapter. It POSTs OpenAI-format chat messages to llama-server's `/v1/chat/completions`. The server handles multimodal routing internally — there is **no manual prompt building** (no `<image>` text marker; the chat template places vision tokens itself). There is no `llama-cpp-python`, no `Llava15ChatHandler`, no dual-engine setup; one llama-server instance serves all text, vision, tool calls, chat, Discord, and sentry.

### Tool Calling Mechanism

Aster uses **native OpenAI-format function calling** by default: `ADMIN_TOOLS` (already
full JSON-Schema `{"type": "function", "function": {...}}` dicts) is sent as the `tools`
param on every `/v1/chat/completions` POST, and llama-server returns a structured
`tool_calls` array on the response message instead of the model narrating a call in text.
The chat template in use is the fixed **froggeric v22.5** template
(`E:\Models\qwen36_chat_template.jinja`, passed via `--chat-template-file`): it fixes
Qwen's official template bugs (the `|items` filter that broke tool arguments in C++
Jinja runtimes, empty `<think>` blocks filling context) and places vision tokens itself.
Thinking is disabled server-side (`--reasoning off`), so responses come back as plain
`content` with no `reasoning_content`.

`_execute_llm_completion()` returns the **full message dict** (`role`/`content`/
`tool_calls`), not a bare string — callers that only need text do
`(response_msg.get("content") or "")`. `_extract_native_tool_call(message)` /
`_extract_all_native_tool_calls(message)` read the structured `tool_calls` field.
Tool results are fed back as `role: "tool"` messages carrying `tool_call_id` (matched to
the `id` on the assistant's `tool_calls` entry), not as fake `role: "user"` observations.

**The tool-calling path is native-only.** The legacy XML-in-text path and its
`config.USE_NATIVE_TOOL_CALLS` rollback flag (`_legacy_xml_parse*`, `_get_xml_tool_prompt`,
the XML manual in the system prompt, `role:"user"` tool observations) were **removed in
the Qwen 3.6 swap (2026-09-27, branch `qwen3.6-35b`)** — nothing branches on a flag any
more. The Gemma-era engine is preserved on branch `gemma-4-e4b-lightweight`. The test
harness `engine_testing/harness.py` is native-only (sends `tools=ADMIN_TOOLS`, reads
structured `tool_calls`, feeds `role:"tool"` results, runs LoopGuard exactly like
production); historical comparators are the frozen reports in `engine_testing/results/`.
52 scenarios across 11 categories, including 5 `loop_trap` adversarial scenarios (mock
results engineered to tempt re-calling, scored by `check_loop_discipline`). Live
validation of the swapped engine is tracked in `engine_testing/qa/qwen-swap-live-validation.md`.

**Adding a new tool requires three edits:**
1. Add the schema dict to `ADMIN_TOOLS` list (`core/brain.py:324`)
2. Add an `elif` branch in `execute_tool()` (`core/brain.py:2080`)
3. Implement the function in the appropriate `tools/` module and import it at the top of `brain.py`

**Gotcha:** `_execute_llm_completion()` is also called directly (without `tools=`) from
`tools/vision.py`, `tools/sentry.py`, `tools/awareness.py`, and `tools/face_server.py` for
one-shot, non-tool-calling completions (vision descriptions, sentry scene analysis,
proactive-awareness text, persona generation). Every one of these call sites must unwrap
the dict itself — `.get("content") or ""` — since the function's return type is `dict` for
*all* callers, not just tool-calling ones.

### System Prompt & Persona (modular)

The admin system prompt is **no longer hardcoded** in `brain.py`. Personas live as
markdown files in `Aster_Vault/System_Prompts/<name>.md` and are assembled at import by
`_build_system_content(persona_name)` (`core/brain.py`, just above the `messages = [...]` init):

```
messages[0]["content"] = _build_system_content(config.SYSTEM_PROMPT)
# → _load_system_prompt(name) + shared tool laws
```

- **Selector:** `config.SYSTEM_PROMPT` (a filename stem, no `.md`), sourced from
  `self_config.yaml → persona.system_prompt`, default `"jarvis"`. Switching the whole
  personality is a one-line edit there — no code changes.
- **Shipped personas:** `jarvis.md` (the original refined-butler prompt, extracted
  verbatim) and `gogi.md` (a warm best-friend persona with built-in mood-aware
  behavior). `gogi.md` consumes `[Mood: <state>]` message tags — these are now live
  (emitted by `tools/emotion_recognition.py`). See **Emotion Recognition** subsystem.
- The loader **strips `<!-- ... -->` HTML comments**, so each file can carry a doc
  header that never reaches the model. Tool schemas are **not** in the system prompt —
  they travel in the `tools` POST param. **Do not** put a tool list inside a persona
  file. Missing/unreadable file → minimal fallback prompt (never crashes import).
- **Gotcha:** response-length behavior is persona-defined, not global. The "two-sentence
  law" lives only in `jarvis.md`; `gogi.md` deliberately drops it. There is no global
  two-sentence flag anymore.

### Chat Format & Multimodal Images

- Messages use standard OpenAI roles (`system` / `user` / `assistant`).
- Images are sent as `image_url` content blocks: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`.
- The chat template places vision tokens itself; the brain adds **no** `<image>` text marker and strips **no** INST tokens (both were Gemma-template workarounds, removed in the Qwen swap).
- After each turn, `purge_media_cache()` strips Base64 data from history to prevent VRAM bloat, archives images to `Aster_Vault/images/`, and leaves a tombstone with the saved file path. `re_examine_image(filepath, question)` re-loads an archived image for stateless follow-up inference.

### Hallucination Detection

`_claims_tool_execution()` fires when the model narrates executing a tool ("Playing on Spotify", "Taking a screenshot") without an actual `tool_calls` entry. It pattern-matches (user-intent regex × response-claim regex) and short-circuits immediately when `tool_calls` is populated. It injects a retry nudge and re-runs the LLM **once**; the `hallucination_retried` flag prevents infinite loops. **Phase-0 Qwen baseline still shows this family** ("The timer is set, Sir." with no call) — see `engine_testing/qa/qwen-swap-live-validation.md` T3.

**Failure-blind success claims (the inverse case):** the loop tracks `failed_tools` — every genuinely-executed tool whose result reads as a failure (`_tool_result_failed()`: result starts with `FAILED` or `Error`); a later successful retry of the same tool clears its entry. If the model produces a final text response while `failed_tools` is non-empty and the reply doesn't acknowledge a problem (`_acknowledges_failure()`), a one-shot `[System: ... FAILED — that action did NOT happen ...]` retry nudge is injected (`failure_retried` flag). Two related guards: tools in `_TERMINAL_TOOLS` (Spotify playback, volume, etc.) only short-circuit the loop with "Action completed." when the result was **not** a failure — a failed terminal tool loops back so the model can react to the error; and the post-tool ghost prod (below) switches to "tell the user honestly that it failed" when `failed_tools` is non-empty. Tool authors: make failure returns start with `FAILED` (see `_NO_DEVICE_MSG` in `tools/media.py`) so this machinery sees them.

### Agentic Loop — Post-Tool "Apathy" Failsafe

The model can return an empty string after successfully executing tools (Gemma-4 did this routinely; Qwen is watched for it — a mild post-tool refusal survived the swap). The 15-round loop tracks a `tools_executed` boolean; if the model exits empty after running tools, a neutral `[System Internal]` user nudge is injected, one final completion is fired, and the nudge is removed from history so only the real exchange remains.

### Separate Conversation Contexts

- **Admin brain** — `process_user_input()`, uses `ADMIN_TOOLS` (69 tools), full conversation history, 15-round loop.
- **Discord brain** — `process_discord_chat()`, per-friend history in `discord_chat_histories` (keyed by lowercase name), separate `DISCORD_CHAT_SYSTEM_PROMPT`, only 3 tools (`forward_to_owner`, `save_personal_fact`, `get_current_track`), 4-round loop.

These histories are fully independent. Discord replies pass through `_enforce_discord_honorific()`, which rewrites "Sir" → "Ma'am" / "Mr." → "Ms." for friends listed in `config.DISCORD_FEMALE_NAMES` (sourced from `self_config.yaml → contacts.female_names`, empty by default — per-install data, not hardcoded). This regex is fragile (can mangle words like "Sirius").

---

## Subsystems

### Interfaces

- **CLI** — input loop on the main thread (`main.py`), headless mode.
- **Telegram C2 bridge** — hardcoded `AUTHORIZED_CHAT_ID`; handles `/status`, `/compact`, `/sentry on|off`, `/stop`, `/diagnostics on|off`, `/screenshot`, `/peek [N]`, `/gpu`, `/gesture on|off`, `/intervention on|off`, `/note <text>`, `/find <thing>` (Assist Mode highlight), voice (Faster-Whisper STT), photos, text. `_dispatch_telegram_response()` intercepts `[NATIVE_AUDIO_PAYLOAD:path]` tags and sends voice notes.
- **Discord** — two subsystems: `tools/discord_listener.py` (discord.py DM listener, 9-contact whitelist) for inbound, `tools/discord_api.py` (raw Discord REST v10) for outbound.
- **WebRTC voice bridge** (`webrtc_bridge.py`) — LiveKit Agents SDK. Pipeline: AudioStream → Silero VAD → Faster-Whisper STT → brain → Kokoro TTS. Supports barge-in and epoch-based stale-response cancellation. **Chunk-streaming TTS** (`runtime.tts_chunk_streaming`, default on): `local_tts.py` pushes each Kokoro segment's audio into the call as it is synthesized (first-chunk latency logged as `[Aster Perf] First audio chunk in Xms`) instead of concatenating the whole reply first; a stop-event halts synthesis at the next segment on barge-in. STT and TTS are **shared process-wide**: one CUDA Faster-Whisper `medium.en` instance for calls (`local_stt.get_whisper_model()` — Telegram voice notes reuse it when a call is live, otherwise `local_stt.transcribe_file` uses a lazy **CPU** instance so voice notes cost zero VRAM) and one ref-counted Kokoro pipeline — no per-call duplicate model loads. Module-level `_active_bridge`/`_active_loop` expose `call_is_active()` and `speak_intervention(text)` so other threads can push an unprompted turn into a live call (used by Intervention Mode).

### Vision & UI Automation (`tools/vision.py`)

- **`look_at_screen`** — `mss` screen capture → Base64 JPEG → the vision model describes the screen (temp 0.2).
- **`locate_ui_element_ex(goal)`** — core of `smart_click` / `smart_type`. Three-track pipeline; returns `{"x","y","source","text","score",...}` in logical pixels (`locate_ui_element` is the `(x, y)` back-compat wrapper). Goal parsing/scoring is shared across tracks via `tools/locator_common.py` (`parse_goal` strips filler words, extracts spatial hints ("top right" → position prior, ×0.8 on contradiction) and control-type hints; `score_text` = `max(SequenceMatcher, token-overlap × 0.85, substring × 0.9-0.95)`).
  - **Track 0 — UIA accessibility tree (`tools/uia.py`, primary, ~0.1-2s, zero GPU):** walks the foreground window + taskbar via the `uiautomation` package, matching exact element Names/rects from the OS. Threshold 0.70. Chromium/Electron apps enable accessibility lazily — the walk itself wakes it; a sparse first walk (<12 named controls) triggers one retry after 0.8s. Never raises: any failure falls through to Track 1.
  - **Track 1 — full-screen OCR (~2-4s, cached):** `pytesseract.image_to_data` × 2 (normal + inverted), 2× upscaled, grouped into lines, scored ≥ 0.5. Capture+OCR is cached ~4s (`_get_screen_ocr`); **every GUI action calls `invalidate_screen_cache()`** so a click-then-type re-captures.
  - **Track 2 — OmniParser YOLO + icon captioning (last resort):** `icon_detect` boxes get text labels by intersecting the *cached* Track-1 OCR lines (`_texts_in_box` — no per-crop tesseract calls). If no labeled box scores ≥ 0.45 (`_YOLO_SCORE_THRESHOLD`; 0.3 caused false positives), the top-8 most-confident **text-less** boxes (real icons, ≤15% screen area) are captioned by the multimodal engine (`_caption_crop`) and matched on function descriptions. Track 2 is demoted by `config.USE_PIXEL_FALLBACK` (default true; false = the YOLO model is never loaded). Captioner is pluggable via `config.ICON_CAPTIONER` (`vision.icon_captioner`): `"llm"` (default, resident model, zero extra VRAM) / `"off"` (any other value falls through to the captioner). (The Florence-2 experiment was dropped — never wired.)
  - Coordinates are DPI-corrected: `pixel_in_upscaled_image / 2 * (logical_screen / physical_screen)`; UIA rects scale by `logical / physical-desktop-rect`.
- **Deterministic action verification** (`core/brain.py` dispatch + vision helpers): every `smart_click` diffs a small pre/post grayscale frame (`capture_screen_small_gray` + blockwise `screens_differ`) and warns the model when the screen did not change; every GUI result carries the match provenance (source/text/score, low-confidence flagged) and the current foreground window title (`tools.uia.get_foreground_window_title`). `smart_type` checks keyboard focus via UIA before Ctrl+A: form fields get select-all, `DocumentControl` gets append-only (never wipes a document), anything else returns `FAILED` without typing. All locate misses return `FAILED — ...` so the failure-blind-claim guard sees them. The ReAct loop warns from the 3rd identical `smart_click` goal per turn.
- **`verify_action_result(goal)`** — post-click model free-text check (`n_predict=80`); not called by the dispatch path (per-click verification is the deterministic diff above).
- **Eval harness** — `engine_testing/locator_eval.py`: `capture <name>` saves labeled screenshots, `run [--captions]` scores Tracks 1/2 offline on `locator_cases/cases.json`, `live "<goal>"` runs the full locate against the live screen (moves mouse, never clicks). Locator-tuning decisions are made here with data; `run --no-pixel` measures the demoted-Track-2 A/B.
- **DOM-first automation motor (opt-in, Stages 0–3):** `tools/dom.py` builds a role/name/bbox shortlist from Playwright's ARIA snapshot (web, attached over CDP when `config.USE_DOM_MOTOR` is on; only the focused page is actionable) or one shared UIA walk (Windows), ranks it with `locator_common` scoring, and executes via Playwright/UIA. Send/submit/destructive web clicks are refused under `config.DOM_MOTOR_SEND_POLICY` (`confirm` default) until `smart_click(..., confirm_send: true)`. `core/system1.py` (Laya, lazy+ref-counted, CPU) optionally picks among ambiguous candidates and answers neutral-key yes/no gates, escalating to the normal path on low margin (`config.USE_LAYA_KERNEL`). Both flags default OFF; stage plan + QA gates live in `engine_testing/qa/`. **`browse_web(url | site+query)`** opens a page in that browser (launching a real Chrome/Edge when CDP attach isn't available) and returns its visible text + HTTP status — the tool for live prices/pages; read-only (`config.WEB_BROWSE_ENABLED`, default on). **Called with no arguments it reads the current page** without navigating, so the model can interact then review: `browse_web(site='linkedin')` → `smart_type('the search bar', '<name>', submit=true)` → `browse_web()` → `smart_click('<profile>')`. Known sites map to their own search (`linkedin` → people search, `amazon.<tld>` → `/s?k=`, `youtube`, `wikipedia`, `google`); no query → the site home page. **`automation.human_search: true`** (set on this install) makes `site`+`query` land on the site's HOME page so the model drives the site's own search bar like a human (`browse_web(site=…)` → `smart_type('the search bar', q, submit=true)` → `browse_web()`); the mapped URLs are the rollback path (flag off), and unknown bare names still search rather than guess a dead host. The owner's real browser profile can NEVER be driven (settled 2026-09-26: Chrome ≥136 ignores the debug port on the default user-data-dir, and any copy or junction of it loses the app-bound encrypted logins — Chrome purges them, so it must never be attempted); `automation.use_real_profile: true` makes `browse_web` refuse loudly (`RealProfileUnavailable`, no silent logged-out guest) with the supported alternative: **`python login_once.py <site>`** (e.g. `linkedin`) opens the login page in Aster's dedicated persistent profile once — the session persists and every later `browse_web` browses that site logged in (`login_once.py --status` lists saved sessions). Aster's own browser launches with `--disable-background-mode` and detach kills it by `--user-data-dir` match, so closed sessions never leave port-squatting background processes.
- **Webcam / face recognition** — `capture_webcam_base64()` returns `(base64, detected_names)`; encodings loaded from `Aster_Vault/Faces/` (files named per person) at import via `load_known_faces()`.
- **Screen watcher** — `watch_screen(target)` daemon polls every 10s for a target word, alerts via Telegram, times out after 1 hour.

### Voice Recognition (`tools/voice_recognition.py`)

Speaker identification via SpeechBrain ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`, ~200 MB, CUDA, eager-preloaded at module import).

**Directory:** `Aster_Vault/Voices/<Name>/<Name>.wav` (manual seed, never rotated) + `<Name>__<ts>.wav` (auto-captured). Identity = folder name. Legacy flat `Voices/*.wav` files auto-migrated to subfolders on first start.

**Key functions:**
- `load_known_voices()` — scans subdirs, averages all `.wav` embeddings per speaker into one L2-normalised centroid. Called at import and on hot-reload. The eager import-time call is **fail-closed**: a transient import failure (e.g. `MemoryError` during a simultaneous llama-server boot) prints a notice and continues; empty globals ⇒ `identify_*` returns `None` instead of crashing `main.py`.
- `identify_speaker(audio, sample_rate=16000)` — accepts `np.float32` mono array or raw WAV bytes. Returns enrolled name if cosine similarity ≥ `SIMILARITY_THRESHOLD` (0.35), else `None`. Returns `None` for audio < 1.0 s.
- `identify_and_maybe_learn(audio, sample_rate=16000)` — preferred entry point. Identifies; if score ≥ `LEARN_THRESHOLD` (0.50) and duration ≥ `MIN_LEARN_SECONDS` (2.5 s), auto-accumulates via `accumulate_sample`.
- `accumulate_sample(audio, name)` — saves `Voices/{name}/{name}__{ts}.wav`, rotates oldest auto-captured file when > `MAX_SAMPLES_PER_SPEAKER` (15), rebuilds that speaker's centroid in-place. Never raises.

**Integration:** called inside `LocalWhisperSTT._transcribe` (`local_stt.py`) and the Telegram voice handler (`main.py`). Prepends `[Speaker: <name>]` to transcript. Unknown voice → stash to `TEMP_VOICE_PATH`, prompt `/enroll`.

**Enrollment:** `/enroll <name>` via Telegram → `Voices/{Name}/{Name}.wav`; re-enrolling an existing speaker adds `{Name}__{ts}.wav` (no overwrite).

**Thresholds:** `SIMILARITY_THRESHOLD = 0.35`, `LEARN_THRESHOLD = 0.50`, `MIN_LEARN_SECONDS = 2.5`, `MAX_SAMPLES_PER_SPEAKER = 15`. VRAM: ~200 MB.

### Emotion Recognition (`tools/emotion_recognition.py`)

User mood detection. Embeds `[Mood: <state>]` into every user message before the brain sees it. All three mood-triggered roadmap features are live: Mood-Trend Memory (Idea 3), Proactive Check-ins (Idea 1), Ambient-Action Offers (Idea 2). Ideas 1 & 2 are opt-in (default OFF).

**Label vocabulary:** `happy` | `sad` | `frustrated` | `anxious` | `neutral`. Always tagged — `neutral` when below `EMOTION_MIN_CONFIDENCE` (default 0.5).

**Tiers:**
- **Tier 0 — text (CPU, eager):** `j-hartmann/emotion-english-distilroberta-base` (7-class → 5-label). Zero VRAM. `detect_text_emotion(text)`. Injected centrally in `_maybe_tag_text_mood()` (`core/brain.py`) at the top of `process_user_input()`.
- **Tier 1 — voice (CUDA, lazy/ref-counted):** `speechbrain/emotion-recognition-wav2vec2-IEMOCAP` (4-class → 4-label; no `anxious` from voice). ~200–300 MB VRAM. `acquire_voice_emotion_model()` / `release_voice_emotion_model()`. `detect_voice_emotion(audio)` self-manages acquire/release. LiveKit call: `LocalWhisperSTT.__init__` acquires an outer hold; `aclose()` releases. Telegram: load → classify → offload per voice note. Loading requires `_prepare_speechbrain_imports()` workaround (SpeechBrain lazy-module guard + CVE-2025-32434 patch) — details in `summary.md`.
- **Tier 1b — ambient room audio (`tools/ambient_audio.py`, CPU, opt-in/default OFF):** local-mic daemon (sounddevice → webrtcvad VAD) reads Mohamed's vocal tone when not addressing Aster. Owner-gated (ECAPA `identify_speaker` == `OWNER_NAME`), paused during calls. Separate CPU copy of IEMOCAP (`_get_cpu_voice_clf()`). Content never transcribed/logged — tone label only. Toggle: `toggle_ambient_audio` / `/ambientaudio on|off`. Config: `emotion.ambient_audio` in `self_config.yaml`. Tests: `ambient-audio-test.py`.
- **Tier 2 — face (CPU, eager, opt-in/default OFF):** `hsemotion-onnx` (AffectNet 8-class EfficientNet-B2, via `onnxruntime`). `detect_face_emotion(frame)` — EMA-smoothed, confidence-gated. `get_face_mood()` / `reset_face_mood()`. Owner-gated: Awareness daemon only updates `face_mood` when `config.OWNER_NAME` is the recognized face. Toggle: `toggle_face_emotion` / `/faceemotion on|off`. Config: `emotion.face`. Tests: `face-emotion-test.py`.

**Fusion (`fuse_moods`):** confident text content wins over voice prosody; prosody used only when text reads `neutral` (handles "I'm fine" said sadly). IEMOCAP reads angry speech as `happy` (high-arousal confusion) — text content gates prosody to fix this.

**Per-path:**
- Typed text / CLI / Telegram text / image captions: Tier 0 only.
- LiveKit call: `detect_combined_emotion(audio, transcript)` — Tier 1 + Tier 0 on Whisper transcript, fused.
- Telegram voice note: `detect_text_emotion(gist)` + `detect_voice_emotion(audio)` fused in `core/brain.py`.
- Ambient room: Tier 1b only, lowest-precedence.

**Fusion precedence in `_maybe_tag_text_mood()`:** text > face (`fuse_face()`) > ambient-voice (`get_ambient_voice_mood()`), each ambient signal filling only a still-neutral read.

**Injection sites:**
- LiveKit: `[Mood: X]` inserted after `[Speaker: Y]` in `local_stt.py`.
- Telegram voice note: `[Mood: X]` injected into voice-note caption in `core/brain.py`.
- Text: `_maybe_tag_text_mood()` in `core/brain.py`; skips `[System Internal]` nudges, already-tagged messages, `[NATIVE_AUDIO_PAYLOAD:]` blobs.

**Config:** `emotion:` in `self_config.yaml`; `EMOTION_ENABLED`, `EMOTION_TEXT_MODEL`, `EMOTION_VOICE_MODEL`, `EMOTION_MIN_CONFIDENCE`. Set `emotion.enabled: false` to fully disable.

### Mood-Trend Memory (`tools/mood_memory.py`)

Longitudinal mood logging (emotion roadmap Idea 3). `_log_turn_mood()` (`core/brain.py`) reads `[Mood: X]` from the already-tagged `user_text` and calls `log_mood_turn(mood, context)` in `tools/emotion_recognition.py` — appends `{ts, mood, context}` to `Aster_Vault/emotion_log.jsonl` per real user turn. `[System Internal]` nudges and slash commands are skipped. Also feeds an in-memory deque for `get_sustained_mood(min_turns=MOOD_SUSTAIN_TURNS)` — returns a mood only when last N turns are the same non-neutral label (debounce gate for Ideas 1 & 2).

`maybe_flush_mood_summary()` runs in the auto-consolidation cycle (every 5 turns): prunes log (`MOOD_LOG_RETENTION_DAYS` = 30d), and if ≥ 24h since last flush + ≥ 6 logged turns, writes a distribution summary to `Aster_Vault/mood_trends.md` + ChromaDB (`moodtrend_` prefix). Mostly-neutral windows advance the clock but write nothing. State persists in `Aster_Vault/mood_flush_state.json`.

**Config:** `emotion.mood_trend:` in `self_config.yaml`; `MOOD_TREND_ENABLED`, `MOOD_SUSTAIN_TURNS`, `MOOD_LOG_RETENTION_DAYS`, `MOOD_FLUSH_INTERVAL_HOURS`, `MOOD_FLUSH_MIN_TURNS`, `MOOD_FLUSH_NONNEUTRAL_FRAC`. Gated by both `EMOTION_ENABLED` and `MOOD_TREND_ENABLED`. Tests: `tests/test_mood_memory.py` (15 cases).

### Mood-Triggered Behaviors — Check-ins (Idea 1) & Ambient-Action Offers (Idea 2)

Both consume `get_sustained_mood()` debounce and share a streak-guard (`get_mood_streak()`, `note_mood_touch()`, `mood_touch_recent()`) to prevent double-nudging. **Idea 2 owns active chat; Idea 1 owns the quiet after.** Both opt-in (default OFF).

- **Idea 2 — Ambient-Action Offers (`tools/mood_actions.py`):** `maybe_mood_action_nudge()` returns a `[System Internal: …offer…]` appended to the current turn in `process_user_input` (skips system nudges / `[NATIVE_*]` payloads). Never auto-executes. Policy: sad/anxious → calming playlist + lower volume; frustrated → close distraction window; happy → hype playlist. Toggle: `toggle_mood_actions` / `/moodactions on|off`. Config: `MOOD_ACTIONS_ENABLED`, `MOOD_ACTIONS_COOLDOWN`.
- **Idea 1 — Proactive Check-ins (`tools/awareness.py`):** `_maybe_trigger_mood_checkin()` runs per Awareness-daemon tick. Fires on sustained sad/anxious/happy streak + quiet period ≥ `MOOD_CHECKIN_QUIET_SECONDS`, gated by per-streak guard + `_budget_remaining()`. Requires the Awareness daemon running. Delivers via `_push_nudge()`. Toggle: `toggle_mood_checkins` / `/checkins on|off`. Config: `MOOD_CHECKIN_ENABLED`, `MOOD_CHECKIN_QUIET_SECONDS`, `MOOD_CHECKIN_COOLDOWN`.

Shared config: `emotion.mood_shared` (`MOOD_TOUCH_COOLDOWN`), `MOOD_SUSTAIN_TURNS`. Tests: `emotion-test.py` (19 checks, no llama-server).

### GUI Automation (active)

`smart_click`, `smart_type`, `smart_scroll`, `press_key` are registered in `ADMIN_TOOLS` and dispatched in `execute_tool()` — `pyautogui` calls live directly in `brain.py`'s dispatch, on top of the three-track locator + deterministic verification described under **Vision & UI Automation** above (UIA tree → cached OCR → YOLO+caption; pre/post screen diff, foreground-title ground truth, UIA focus check before Ctrl+A, `FAILED —` results on every miss, repeat-click warning from the 3rd identical goal per turn). **Important:** keyboard keys (Enter/Tab/Esc) must use `press_key`, NOT `smart_click` (clicking the word "Enter" found by OCR is a known past bug). `tools/gui.py` still has `ui_type`/`ui_press_key` wrappers but the file is **not imported** anywhere.

### Sentry Mode (`tools/sentry.py`)

Webcam intruder detection. `sentry_daemon()` polls every 5s when active: vision-model scene analysis with `face_recognition` fallback. Known-as-"Mohamed" ignored; others alert with 5-min cooldown; unknown faces save `temp_intruder.jpg` and set `WAITING_FOR_ID`. The daemon **auto-start is commented out** in `main.py` (~line 664), but `toggle_sentry_mode` still works.

### Intervention Mode (`tools/intervention.py`)

Proactive focus daemon. `intervention_daemon()` polls the foreground window every `INTERVENTION_CHECK_INTERVAL`s when active, accumulating continuous time on apps whose title matches `config.DISTRACTION_KEYWORDS`. One glance-away poll is tolerated before the accumulator resets. Once `INTERVENTION_THRESHOLD` is crossed (and not in quiet hours / snoozed / within `INTERVENTION_COOLDOWN`), `_trigger_intervention()` flags the window and routes a `[System Internal: …]` nudge through the brain — spoken over the call via `webrtc_bridge.speak_intervention()` if one is live, else a Telegram text + Kokoro voice note. Toggled via `/intervention on|off` or `toggle_intervention_mode`; `close_distraction_window` / `snooze_intervention` handle the accept / push-back replies. **Not** mutually exclusive with Sentry/Gesture (no webcam/GPU use).

### Assist Mode (`tools/assist.py`)

Screen-highlight helper. When the user asks *where* something is on screen,
`locate_ui_elements_boxed(goal)` (`tools/vision.py`) returns **all** matches as
logical-pixel boxes — a multi-match, box-returning sibling of `locate_ui_element`
that reuses the same two-track OCR/YOLO pipeline (Track 1 collects every line
scoring ≥ threshold; Track 2 YOLO is the fallback only when Track 1 is empty;
near-identical boxes are deduped). `highlight_regions(boxes)` then spawns a
stdlib-`tkinter` overlay on its own daemon thread: a fullscreen topmost window at
80% opacity black (desktop dimmed to ~20%) with each match's interior painted in
a `-transparentcolor` key so it shows through at full brightness, framed by a
gold outline. The overlay auto-dismisses after ~6 s or on any key/click; a second
trigger replaces the prior overlay. tkinter failures are caught — never crash the
brain. Exposed as the `highlight_on_screen` admin tool and the `/find` Telegram
command. Primary monitor only.

### Memory Pipeline (dual-write)

- **ChromaDB** (`Aster_Vault/chroma_db/`, collection `aster_long_term_memory`) — semantic vector search via `recall_memory()`.
- **Markdown** (`Aster_Vault/memory.md`) — append-only timestamped human-readable log.
- **Discord staging** (`discord_memories.json` at repo root) — per-friend facts `{fact, synced}`, promoted to ChromaDB via `sync_discord_memories`.
- **Dedup gate:** `is_fact_already_known()` blocks writes via bidirectional substring + 85% `SequenceMatcher` against `memory.md`.
- **`trim_memory()`** (`core/memory.py`) — sliding window using a ~4-chars-per-token heuristic (calibrated against llama-server `/tokenize` when `EXACT_TOKEN_COUNT` is on), budget 90% of `N_CTX`, preserves index 0 (system prompt). **Tool-pair-aware:** an assistant message carrying `tool_calls` and its trailing `role:"tool"` messages pop as one unit (`_pop_oldest_turn_unit`) so trimming never orphans a tool message with a dangling `tool_call_id` (covers both admin and Discord histories).
- **`/compact` + auto-compact** — `_compact_context_locked()` (`core/brain.py`) LLM-summarizes history and replaces it with a `[System Memory Restored: ...]` block; refuses if < 2000 estimated tokens (calibrated estimator, images charged flat — base64 can't fake the count). Manual `/compact` and the automatic trigger share this one function: when `runtime.auto_compact` is on (default) and the estimate crosses `runtime.auto_compact_threshold` (0.75) × `N_CTX` at the start of a turn, compaction runs before the turn is processed (one-time latency hit) and logs an `auto_compact` instrumentation event.
- **Auto-consolidation:** `evaluate_and_memorize()` fires automatically every `MEMORIZE_EVERY_N_TURNS` (= 5) real user turns inside `process_user_input()` — not just at shutdown / `/memorize`. Crash safety: at most 4 turns of facts are lost. Multi-fact extraction: `_extract_all_native_tool_calls()` reads every entry off the response's `tool_calls` array so a single pass can save N facts — the old single-match path silently dropped 2nd+.
- **Raw conversation archive** (`log_raw_turn()`, `core/memory.py`) — independent of the fact pipeline above: every real turn's verbatim user/assistant exchange is appended to `Aster_Vault/Conversations/YYYY-MM-DD.md` (one file per calendar day), called from the end of `process_user_input()` with a pristine pre-mutation copy of the input. Not deduped, not summarized, no retention limit yet — it's a grep-able fallback of last resort for "what exactly did we say on day X" when `recall_memory()` comes up empty. Skips `[System Internal]` nudges; native image payloads collapse to an `(image)` placeholder instead of dumping base64 (voice notes are plain text now — Whisper transcript).

### Google Integration — Gmail + Calendar (`tools/google_auth.py`, `tools/gmail_tool.py`, `tools/google_calendar.py`)

Read/modify/send access to the owner's Gmail and Calendar, gated by one shared
`config.GOOGLE_ACCESS_TIER`: **Limited** (read-only), **Partial** (modify + drafts, always
asks before sending), **Autonomous** (sends immediately unless a safety guardrail trips).
Partial and Autonomous request identical Google OAuth scopes (`gmail.modify` +
`calendar.events`) — the only difference between them is Aster's own send-policy gate
(`google_auth.send_policy()`), not what Google grants.

- **Auth** (`tools/google_auth.py`) — mirrors the Spotify OAuth pattern in `config.py`: credentials from `secrets.yaml`, gated on non-empty values into `GOOGLE_AVAILABLE`. Uses `google-auth-oauthlib`'s `InstalledAppFlow`; token cached to `Aster_Vault/google_token.json`. Non-interactive by default — `get_credentials()` raises cleanly if not connected rather than opening a browser from a daemon thread; only `connect_google_account()`, called from a deliberate UI action, runs the interactive consent flow. Google issues ~7-day refresh tokens for personal-use ("Testing" publish status) apps — full verification would require an annual third-party security audit, disproportionate here — so `reauth_needed()` surfaces refresh failures for the Awareness daemon's reminder instead.
- **Pending-approval flow** — mirrors `tools/sentry.py`'s `WAITING_FOR_ID` idiom rather than new Telegram button/callback infrastructure: `google_auth.set_pending_action()` stores one shared pending action (a Gmail reply or a Calendar invite — one slot, not one per service, so "send it"/"discard" is never ambiguous), resolved by `resolve_pending_action()` at the very top of `process_user_input()` (`core/brain.py`) — works uniformly from any interface (CLI/Telegram/WebRTC/dashboard) since they all funnel through that one function.
- **Gmail** (`tools/gmail_tool.py`) — `summarize_unread_emails`, `search_emails`, `read_email` (read tier); `archive_email`, `mark_email_read`, `label_email` (modify tier); `reply_to_email` always drafts via the Gmail API first (the pending state *is* the Gmail draft, not a parallel structure). Autonomous-tier guardrail: never auto-sends to a sender with no prior sent-mail history (checked against Gmail itself, not Aster's memory) and enforces `AUTONOMOUS_SEND_DAILY_CAP`/day — tripping either falls back to the approval flow instead of silently dropping the reply. Every send is logged via `memorize_fact()`.
- **Calendar** (`tools/google_calendar.py`) — `list_upcoming_events` (read tier); `create_calendar_event`/`update_calendar_event`/`respond_to_invite` — solo actions with no guests are modify-tier only; anything that notifies other people (guests on a create/update, responding to someone else's invite) routes through the same `send_policy()` gate as Gmail, sharing its daily cap.
- **Ambient nudges** (`tools/awareness.py`) — `_maybe_trigger_email_checkin()` mirrors `_maybe_trigger_mood_checkin()`'s exact gate order (enabled → initiative → condition → dedup → quiet/budget), nudging when unread mail in Primary crosses `EMAIL_CHECKIN_THRESHOLD` (only re-fires as the count grows, not every tick). `_maybe_trigger_reauth_reminder()` nudges once per lapse when `reauth_needed()` is true. Both run from the daemon's fixed trigger sequence alongside the mood check-in.
- **Dashboard** (`Aster-UI`) — `GoogleCard` in the Socials section (`src/sections/GoogleCard.tsx`) shows connection status and a 3-tier picker, backed by `GET/POST /api/google/status|connect|tier` in `tools/face_server.py`. Tier changes persist via `tools/config_writer.set_config_values()`, matching persona/voice — not the ephemeral Skills-toggle pattern, since access level is a deliberate standing choice, not daemon runtime state.
- **Config**: `integrations.google` in `self_config.yaml` (`access_tier`, `autonomous_daily_cap`, `reauth_reminder`, `email_checkin.*`); `google.client_id`/`client_secret` in `secrets.yaml`.

### Other Tools

- **Spotify** (`tools/media.py`) — Spotipy OAuth; `ensure_active_spotify_device()` auto-transfers playback, and when **no device exists at all** (Spotify closed) it self-heals: launches the desktop app via `tools.system.open_application` and polls `sp.devices()` for up to ~25 s until it registers (`_launch_spotify_and_wait`). All Spotify/timer tools return `ToolResult(ok, text)` envelopes (`core/tool_result.py`) — the failure-blind-claim guard reads `.ok` exactly; failure texts keep their LLM-instructive `FAILED` prefix. Unmigrated tools still return plain strings, classified by the legacy FAILED/Error prefix regex in `core.brain._normalize_tool_result` (`runtime.structured_tool_results: false` reverts to regex-everywhere). `execute_tool()` strips envelopes for legacy callers; the ReAct loops use `execute_tool_ex()` → `(ok, payload)`.
- **System** (`tools/system.py`) — Win32 control: `set_system_state` (rundll32 lock/sleep/restart), `set_volume` (pycaw), `open_application` (Start Menu fuzzy match), process list (psutil).
- **Timers/Alarms** (`tools/media.py`) — daemon threads firing `winotify` toasts.
- **RAG** (`tools/rag.py`) — single `research(topic)` tool. Internal pipeline: vault cache (90-day staleness) → Wikipedia API (fast, free, encyclopedic) → Firecrawl fallback (live web, paid). Auto-saves every result to `Aster_Vault/database/*.md` tagged `[Source: Wikipedia|Firecrawl | topic]`. The model sees one tool; vault/source routing is internal Python.
- **Audio** (`tools/audio.py`) — Kokoro TTS 82M. **One shared, ref-counted pipeline** (`acquire_kokoro_pipeline` / `release_kokoro_pipeline`) used by both the `generate_kokoro_voice` tool and the LiveKit call (`local_tts.py`); lazy-loaded on first use, **offloaded from VRAM when no consumer holds it**. Emits `[NATIVE_AUDIO_PAYLOAD:path]`.

---

## Disabled / Archived Code — Do Not Assume It Runs

- **FastAPI dashboard block in `main.py`** — still commented out. The old 3-panel `aster-ui` dashboard was replaced by the Tauri face app. A *separate* minimal FastAPI lives in `tools/face_server.py` (token + logs only) — that one IS active.
- **Sentry daemon auto-start** — commented out (`main.py` ~359).
- **Old YOLO coordinate pipeline** — the archived `'''...'''` blocks were **deleted in the Qwen swap** (`parse_screen`, `UI_ELEMENT_COORDS`, `get_ui_element_coords`, `_extract_detector_elements` no longer exist anywhere). The current locator is `locate_ui_element`; the live Track-2 path is `_yolo_track` + `_caption_crop`.
- **`tools/gui.py`** — present but not imported.
- **`tools/web.py`** — `search_web()` is dead code; the brain uses `deep_web_search()` from `tools/rag.py`.
- **`whisper-models/models--Systran--faster-distil-whisper-large-v3/`** — present but never loaded; only `medium.en` is used.
- **Native audio input (`input_audio`) — removed in the Qwen swap.** Gemma's audio head is gone; voice notes now go through Faster-Whisper (`local_stt.transcribe_file`) and enter the brain as text.

---

## Tool Registry (69 admin tools registered at boot)

Categories: System (7), File I/O (3), Spotify (9), Timers (2), Memory (3), Vision (4), GUI Automation (6), RAG (1), Notes (2), Awareness/Initiative (2), Intervention (3), Mood opt-in (4), Sentry/Gesture (2), Self-knowledge (6), Diagnostics (1), Discord/Voice (2), Gmail (7), Calendar (4).

To regenerate full list: `python -c "import core.brain as b; [print(t['function']['name']) for t in b.ADMIN_TOOLS]"`. Full per-tool table in `summary.md` Appendix A.

Discord brain has only 3 tools: `forward_to_owner`, `save_personal_fact`, `get_current_track`.

---

## Thread Architecture

Main thread runs the CLI loop. Daemon threads: Telegram `infinity_polling`, Discord DM listener, WebRTC bridge, Gesture daemon, Intervention daemon (idle until `/intervention on`), Awareness daemon, Ambient Audio daemon (`tools/ambient_audio.py`, idle/no-mic until `/ambientaudio on`), Face server (`tools/face_server.py`, uvicorn on `:8000`), Health watchdog (`tools/health_watchdog.py` — pings llama-server `/health` every 30s; 3 consecutive failures → Telegram alert + capped auto-relaunch of `start.bat`; 503 = model loading, never restarts into it; flags `daemons.health_watchdog` / `health_watchdog_autorestart`) — all active; Sentry daemon (disabled); Screen watcher (on-demand, 1-hour timeout).

---

## Known Gotchas

- **Credentials live in `secrets.yaml`, not `config.py`.** Spotify, Telegram, Discord, LiveKit, and Firecrawl credentials are loaded via `config._secret()` from `secrets.yaml` (gitignored, per-install — see `secrets.example.yaml` for the template). `config.py` no longer contains any real credential; do not add new ones there or to any tracked file. `self_config.yaml` (identity/contacts — also gitignored, see `self_config.example.yaml`) and `secrets.yaml` can both be populated by running `python first_run_setup.py`. Every integration is optional — a blank/missing credential disables that integration gracefully (`SPOTIFY_AVAILABLE` / `TELEGRAM_AVAILABLE` / `LIVEKIT_CONFIGURED` / Discord's own empty-token check) rather than crashing.
- **`uiautomation` package required for locator Track 0** — `python -m pip install uiautomation`. Without it `tools/uia.py` degrades gracefully (every locate falls through to the OCR track), but GUI automation gets slower and less accurate. UIA cannot read elevated (admin) windows from Aster's non-elevated process, and the secure desktop (UAC prompts / lock screen) is off-limits entirely.
- **Vault path casing is fixed** — all `Aster_vault` (lowercase `v`) literals were replaced with `config.VAULT_DIR` / `config.FACES_DIR` (2026-07-12); the faces dir is `paths.faces_dir` (default `Aster_Vault/Faces`). Use these constants for any new vault path — never a hardcoded string.
- **`build_cuda.py`** — legacy; the engine migrated from `llama-cpp-python` to a standalone `llama-server` binary.
- **Firecrawl key required for web fallback** — `research()` falls back to Firecrawl only when Wikipedia fails. If `FIRECRAWL_API_KEY` in `config.py` is unset, the fallback returns a graceful error string and never crashes. Free tier: 500 credits/month; ~9 credits per invocation.
- **`wikipedia` package required** — `python -m pip install wikipedia`. The `research` tool fails gracefully without it but Wikipedia lookup will always fall through to Firecrawl.
- **LiveKit Agents devmode subprocess** — `webrtc_bridge.py` calls `server.run(devmode=True)`. Devmode may fork a worker subprocess that duplicates Python+torch, adding ~3-5 GB RAM. To check: after a call connects, run `Get-Process python | Format-Table Id, WorkingSet64` — two python.exe entries with multi-GB footprints confirms the subprocess. Switching `devmode=True` → `False` in `webrtc_bridge.py:668` should eliminate it; test carefully as it changes agent worker lifecycle.
- **`daemons.awareness_mode` in `self_config.yaml` IS honored** (fixed — the old hardcode in `tools/awareness.py` is gone; boot state comes from `config.AWARENESS_ACTIVE`, default `true`). If awareness seems mysteriously off, check whether the per-install `self_config.yaml` still carries `awareness_mode: false` copied from the old example template.
- **Persona is the single source of truth for personality** — `config.SYSTEM_PROMPT` (`self_config.yaml → persona.system_prompt`) selects the active prompt file in `Aster_Vault/System_Prompts/`. `get_my_status()`'s `personality_mode` is *derived* from it (`tools/self_knowledge.py`), so they can't drift. The legacy `identity.personality_mode`, `settings.two_sentence_law`, and `settings.technical_mode_trigger` yaml knobs were removed — don't re-add a separate personality flag. `runtime.wake_word_model: hey_jarvis` is unrelated (the wake-word model name, not the persona).

---

## Key Files

| File | Purpose |
|---|---|
| `main.py` | Entry point, Telegram handlers, engine warmup, headless CLI loop |
| `config.py` | Single-engine config, ChromaDB init; loads `self_config.yaml` (`SELF_CONFIG`/`_cfg()`) and `secrets.yaml` (`SECRETS`/`_secret()`) as two separate gitignored per-install files; persona selector (`SYSTEM_PROMPT`) + `VOICE_SPEED` / `UTTERANCE_DEBOUNCE` / `INITIATIVE_LEVEL` knobs |
| `first_run_setup.py` | Standalone interactive wizard — `python first_run_setup.py` — writes `secrets.yaml` (credentials) and the identity/contacts fields of `self_config.yaml`. Every integration optional; safe to re-run (existing values become prompt defaults). Not auto-invoked by `main.py` (would hang non-interactive contexts like pytest) — `config.py` just prints a one-time note when `secrets.yaml` is missing. |
| `secrets.example.yaml` / `self_config.example.yaml` | Public templates (committed) for the two gitignored per-install config files above. Copy to `secrets.yaml` / `self_config.yaml` and edit, or use `first_run_setup.py`. |
| `core/brain.py` | LLM adapter (`_execute_llm_completion`), native OpenAI tool calling (no legacy XML path since the Qwen swap), ReAct loop, hallucination detection, `ADMIN_TOOLS`, `_load_system_prompt()` persona loader, Discord brain (~3100 lines) |
| `Aster_Vault/System_Prompts/*.md` | Swappable persona prompts (`jarvis.md` butler, `gogi.md` best-friend); chosen by `config.SYSTEM_PROMPT`, assembled by `_build_system_content()` (calls `_load_system_prompt()`). `<!-- -->` comments stripped; tool schemas travel in the `tools` POST param |
| `core/memory.py` | Hybrid memory read/write, tool-pair-aware `trim_memory()`, raw daily conversation archive (`log_raw_turn()`) |
| `core/tool_result.py` | `ToolResult(ok, text)` envelope + `tool_ok`/`tool_fail` helpers — exact failure signal for migrated tools (media.py, GUI branches); normalized in `core.brain._normalize_tool_result` |
| `tools/health_watchdog.py` | llama-server `/health` watchdog daemon — 3-strike detection, Telegram alert (`diagnostics.send_alert`), capped auto-relaunch of `start.bat` |
| `tools/vision.py` | Screen/webcam capture, face recognition, `get_primary_face_crop_rgb` (face crop for Tier-2 emotion), `locate_ui_element_ex` (Tracks 1-2: cached OCR + YOLO/captioning; Track 0 delegated to `tools/uia.py`), `screens_differ` action diffing, cold storage |
| `tools/uia.py` | Locator Track 0 — Windows UI Automation accessibility tree (`uia_locate`, Chromium warm-up retry); `get_foreground_window_title`, `focused_control_type` (smart_type Ctrl+A safety) |
| `tools/locator_common.py` | Shared goal parsing/scoring for all locator tracks: `parse_goal` (filler stripping, spatial + control-type hints), `score_text`, `spatial_weight` |
| `engine_testing/locator_eval.py` | Locator eval harness: `capture` / `run [--captions]` / `live "<goal>"` — decides captioner/threshold questions with data on real screenshots |
| `tools/voice_recognition.py` | SpeechBrain ECAPA speaker recognition (CUDA, eager-preloaded); per-speaker subfolders under `Voices/`; centroid-averaged embeddings; `load_known_voices()`, `identify_speaker()`, `identify_and_maybe_learn()`, `accumulate_sample()` — auto-accumulates high-confidence utterances (cap 15/speaker) |
| `tools/emotion_recognition.py` | Multi-tier user mood detection. Tier 0: text (CPU, eager, `j-hartmann/emotion-english-distilroberta-base`). Tier 1: voice (CUDA, lazy/ref-counted, `speechbrain/emotion-recognition-wav2vec2-IEMOCAP`). Tier 1b: ambient room voice (a **CPU** copy of the same model, `_get_cpu_voice_clf`/`detect_ambient_voice_emotion`/`get_ambient_voice_mood`/`reset_ambient_voice_mood`; shared inference via `_classify_voice_waveform`). Tier 2: face (CPU, eager, opt-in, `hsemotion-onnx` AffectNet-8; `detect_face_emotion`/`get_face_mood`/`reset_face_mood`/`fuse_face`). Emits `[Mood: <state>]` tags. Also hosts mood-trend logging primitives (`log_mood_turn`, `get_sustained_mood`, `read_mood_log`, `prune_mood_log`). |
| `tools/ambient_audio.py` | Tier-1b ambient room-audio daemon (opt-in/default OFF) — `sounddevice` local mic → `webrtcvad` VAD → LiveKit-call pause → ECAPA owner gate → `detect_ambient_voice_emotion` (CPU). Mock tests: `ambient-audio-test.py`. |
| `tools/mood_memory.py` | Mood-Trend Memory (Idea 3) — reads `Aster_Vault/emotion_log.jsonl`, time-gated flush to `Aster_Vault/mood_trends.md` + ChromaDB (`maybe_flush_mood_summary`). |
| `tools/mood_actions.py` | Ambient-Action Offers (Idea 2) — `maybe_mood_action_nudge`, injected inline in `process_user_input`. Proactive check-ins (Idea 1) live in `tools/awareness.py` (`_maybe_trigger_mood_checkin`). Mock tests: `emotion-test.py`. |
| `tools/system.py` `tools/media.py` | OS control / Spotify + timers |
| `tools/sentry.py` | Webcam intruder daemon |
| `tools/intervention.py` | Intervention Mode — foreground-window focus daemon; `classify_window()`, toggle/snooze/close, brain-routed in-persona delivery |
| `tools/assist.py` | Assist Mode — stdlib-tkinter dim overlay (`highlight_regions()`) that highlights located screen elements; backed by `locate_ui_elements_boxed()` in `tools/vision.py` |
| `tools/discord_listener.py` `tools/discord_api.py` | Discord inbound / outbound |
| `tools/google_auth.py` | Shared Gmail + Calendar OAuth/token management, tier gating (`tier_allows`, `send_policy`), shared pending-approval slot |
| `tools/gmail_tool.py` | Gmail read/modify/reply tools; draft-then-send with autonomous-tier guardrail |
| `tools/google_calendar.py` | Calendar read/modify/invite tools; guest-affecting actions share Gmail's send policy |
| `tools/rag.py` `tools/audio.py` `tools/memory_manager.py` | Web research / Kokoro TTS / Discord fact staging |
| `tools/diagnostics.py` | `sys.stdout` shim → Telegram forwarder; `/diagnostics on\|off` toggle; `send_error()` / `send_alert()` relays; also feeds the desktop UI log strip |
| `tools/face_server.py` | FastAPI on `:8000` — `GET /api/token` (LiveKit JWT w/ agent dispatch) + `WS /api/logs`; powers the Tauri desktop UI |
| `tools/realtime_stream.py` | WS broadcast queue (`publish_terminal` / `publish_wake` / `publish_sentiment` / `stream_dispatch_loop`) feeding the desktop UI |
| `tools/sentiment.py` | Keyword-only `classify_sentiment()` — maps Aster's final response to a reactor-face mood (`calm`/`working`/`alert`/`music`/`success`); no LLM call |
| `aster-face/` | (formerly `aster-ui/`) Tauri desktop companion — 8-bit reactor-core face, LiveKit voice call, mute, resizable log strip, minimize→mini-circle window. Face colour reacts to wake state (`useWakeState`) and conversational mood (`useSentiment`, fed by `{type:"sentiment"}` events) |
| `Aster-UI/` | Full dashboard Tauri app — eight section panels (Activity Log, Dashboard, Memory CRUD, Skills toggles, VoiceChat SSE stream, Notes, Socials status). API layer in `Aster-UI/src/api/`. Aster character companion with two renderers: 8-bit pixel canvas (default, `src/character/pixel/`, choreographed mood transitions) and the original cartoon puppet — switch in Settings → Appearance. Connects to `tools/face_server.py` on `:8000`. 57 vitest tests, build+lint clean. |
| `tools/gesture.py` | MediaPipe Hands gesture daemon; 7 Tier-1 gestures (volume/pause/skip/boss/lock); mutually exclusive with Sentry |
| `webrtc_bridge.py` `local_stt.py` `local_tts.py` | LiveKit voice bridge + STT/TTS adapters |
| `tests/test_migration.py` | PyTest suite (mocked LLM) |
| `tests/test_locator.py` | PyTest suite for the three-track GUI locator (per-feature test classes; no llama-server, no live-screen assertions) |
