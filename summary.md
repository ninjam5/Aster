# ASTER — State of the System Architectural Summary

**Report Date:** 2026-09-27 (engine section revised for the Qwen 3.6 35B-A3B swap; original baseline 2026-05-20)
**Prepared For:** External AI Systems Architect
**Classification:** Technical Baseline Assessment
**Workspace Root:** `E:\LLM testing\Aster-localization`

---

## 1. Core Architecture & Stack

### 1.1 Language & Runtime

| Layer | Technology |
|---|---|
| Backend Language | Python 3.x (synchronous brain loop, async LiveKit agents) |
| Frontend Language | TypeScript 6.x / React 19.x |
| Build Tooling | Vite 8.x, TailwindCSS 3.x, PostCSS, ESLint |
| OS Target | Windows 11 (native Win32 APIs: `rundll32`, `pyautogui`, `pycaw`, `winotify`) |

### 1.2 Backend Frameworks & Libraries

| Component | Library / Version |
|---|---|
| LLM Inference | **llama-server**/**BeeLlama v0.4.7** fork (native REST `/v1/chat/completions`, CUDA offload, 60k context, KVarN KV cache, MTP speculative decoding) |
| STT Engine | **Faster-Whisper** (CTranslate2, `faster_whisper` — primary for Telegram) |
| TTS Engine (WebRTC) | **Kokoro 82M** (`kokoro` Python package, `KPipeline`) |
| TTS Engine (Telegram) | **Kokoro 82M** (same engine, single voice pipeline) |
| WebRTC / Voice | **LiveKit** (`livekit` SDK, `livekit-agents`, `livekit-plugins-silero`) |
| Vector Database | **ChromaDB** (PersistentClient, collection `aster_long_term_memory`) |
| Telegram Bot | **pyTelegramBotAPI** (`telebot`) |
| Discord Bot | **discord.py** (listener) + raw **requests** REST API (sender) |
| Spotify Control | **Spotipy** (OAuth2, `SpotifyOAuth`) |
| Vision | **Qwen 3.6 mmproj** (native vision via `/v1/chat/completions`, **served on the GPU** — primary), **face_recognition** (fallback), **OpenCV** |
| OCR | **pytesseract** (ACTIVE — full-screen `image_to_data` two-track locator, see Section 2.6.1) |
| UI Detection | **DOM motor** (`tools/dom.py`; ARIA/CDP + UIA shortlist, opt-in) → UIA → pytesseract → **OmniParser v2** (icon_detect YOLOv8 via `ultralytics` + `huggingface_hub`; flag-gated pixel fallback) |
| Screen Capture | **mss** |
| GUI Automation | **pyautogui** (ACTIVE — `smart_click`, `smart_type`, `type_text`, `smart_scroll`, `press_key`, see Section 2.7) |
| System Monitoring | **psutil** |
| Volume Control | **pycaw** |
| Notifications | **winotify** (native Windows 11 toast) |
| Web Search | **ddgs** (DuckDuckGo Search) |
| Audio Processing | **soundfile**, **numpy** |
| VAD (Voice Activity Detection) | **Silero VAD** (via `livekit-plugins-silero`) |
| Wake Word | **openWakeWord** (CPU-only model, `hey_jarvis` default; fully offline) |
| YAML Config | **PyYAML** (`self_config.yaml` — single source of truth for identity, paths, integrations, tunable knobs) |

### 1.3 Frontend Stack

| Component | Library / Version |
|---|---|
| UI Framework | React 19.2.5 |
| State Management | React `useState`/`useEffect` (no Redux/Zustand) |
| LiveKit Integration | `@livekit/components-react` 2.x, `livekit-client` 2.x |
| Animation | `framer-motion` 12.x |
| Icons | `lucide-react` |
| Styling | TailwindCSS 3.x (class-based dark mode) |
| Fonts | Inter (sans), IBM Plex Mono (mono) |

### 1.4 Local LLM Configuration

| Parameter | Value |
|---|---|
| Engine | **llama-server** (llama.cpp native server binary, CUDA 12.4) |
| API Protocol | **OpenAI-compatible** `/v1/chat/completions` (NOT the raw `/completion` endpoint) |
| Base GGUF File | `Qwen3.6-35B-A3B-UD-IQ4_XS.gguf` (17.0 GB) — MoE, ~3B active params/token (≈35B total) |
| Speculative Decoding | Built into the GGUF (MTP heads): `--spec-type draft-mtp --spec-draft-n-max 3` (~90% draft acceptance measured) |
| Server Binary | **BeeLlama v0.4.7** (`E:\Models\beellama-v0.4.7-bin-win-cuda-12.4-x64\llama-server.exe`) — llama.cpp fork adding KVarN KV quantization + MTP |
| Multimodal Projector | `Qwen3.6-35B-A3B-mmproj-F16.gguf` (~0.86 GB) — **served on the GPU** (image prefill ~269 tok/s vs ~42 from RAM) |
| Quantization | **IQ4_XS** (4-bit, importance matrix) |
| KV Cache Type | **KVarN** (`--cache-type-k kvarn4 --cache-type-v kvarn2 --kv-tail-tokens 1024`) |
| Context Window | **60,000 tokens** (`--ctx-size 60000`, `config.N_CTX=60000`) |
| GPU Offload | Attention/dense/embeddings + mmproj on GPU; routed MoE experts split (`--n-gpu-layers 99 --n-cpu-moe 28`); `--threads 8` (P-cores), `--ubatch-size 512`; Faster-Whisper kept off the GPU |
| Flash Attention | **Enabled** (`--flash-attn on`) |
| API Endpoint | `http://localhost:8080/v1/chat/completions` |
| Token Counting | **Character heuristic** (~4 chars per token) |
| Jinja Template | **froggeric v22.5** (`E:\Models\qwen36_chat_template.jinja`) — fixes Qwen's official template tool-arg bug and empty `<think>` blocks |
| Reasoning Mode | **Off** (`--reasoning off`) — responses arrive as plain `content` |

#### Single-Engine Architecture

| Instance | Endpoint | Purpose |
|---|---|---|
| llama-server | `http://localhost:8080/v1/chat/completions` | All text, vision, tool calls, chat, Discord, sentry |

A single llama-server instance handles everything. The `/v1/chat/completions` OpenAI-compatible endpoint receives messages in standard chat format with `image_url` content arrays for multimodal. There is no more `llama-cpp-python`, no `Llava15ChatHandler`, and no dual-engine architecture.

#### Chat Format & Image Handling

Messages are sent in standard OpenAI chat format via `_execute_llm_completion()` in [`core/brain.py`](core/brain.py:79):

- **System messages:** `{"role": "system", "content": "..."}`
- **User messages:** `{"role": "user", "content": [...]}` — content arrays for multimodal
- **Assistant messages:** `{"role": "assistant", "content": "..."}`
- **Image injection:** Uses `image_url` content blocks:
  ```json
  {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{b64}"}}
  ```
- **No marker injection:** the chat template places vision tokens itself; the brain adds no `<image>` text marker and strips no INST tokens (both were Gemma-template workarounds, removed in the Qwen swap).
- **No more manual prompt building:** The old `<start_of_turn>user`/`<start_of_turn>model` string concatenation and `image_data` tensor injection have been fully replaced by the standardized OpenAI chat format.

#### Chat Template (`E:\Models\qwen36_chat_template.jinja`)

The fixed **froggeric v22.5** template is served via `--chat-template-file`. It repairs the official Qwen template (tool arguments used a Python-only `|items` filter that fails in C++ Jinja runtimes; `preserve_thinking` wrapped empty `<think>` blocks that filled context) and handles vision-token placement natively.

#### Hallucination Detection (v2)

When the model claims to execute a tool ("Playing on Spotify", "Taking a screenshot") but emits **no actual tool call**, a `_claims_tool_execution()` detector fires:
1. Cross-references user request patterns ("play X on spotify", "what's on my screen") against response execution claims
2. Also catches residual tool call syntax (`<|tool_call|>`, `call:func{...}`) that can appear when the model leaks raw tags
3. Injects a system nudge and retries the LLM **once** (`hallucination_retried` flag prevents infinite loops)
4. Display cleanup strips residual `call:func{...}` fragments before returning to TTS

### 1.5 Whisper STT Configuration

| Parameter | Value |
|---|---|
| Model | `medium.en` (English-only) |
| Quantization | **int8** (CTranslate2) |
| Device | CUDA (`device="cuda"`) |
| Model Path | `whisper-models/medium.en/` |
| Instancing | **Single shared instance** via `local_stt.get_whisper_model()` — loaded once at startup, used by BOTH Telegram voice and the LiveKit call (no duplicate VRAM copy) |
| Additional Model Present | `models--Systran--faster-distil-whisper-large-v3` (unused at runtime) |

### 1.6 Kokoro TTS Configuration

| Parameter | Value |
|---|---|
| Model | Kokoro 82M (`KPipeline`) |
| Language | English (`lang_code='a'`) |
| Sample Rate | 24,000 Hz |
| Voice | `aster2.pt` (custom blended voice tensor) |
| Voice Clone Reference | `Aster_Vault/Voice/aster_audio_ref.wav` + `aster_text_ref.txt` |
| Instancing | **Single shared, ref-counted pipeline** in `tools/audio.py` (`acquire_kokoro_pipeline` / `release_kokoro_pipeline`) — lazy-loaded on first use, **offloaded from VRAM when no consumer holds it**. Used by both `generate_kokoro_voice` (Telegram) and `LocalKokoroTTS` (call). |

---

## 2. Implemented Features & Capabilities

### 2.1 Runtime Architecture (Headless CLI Mode)

**Status:** FastAPI/Dashboard fully disabled. System runs in headless CLI mode.

**Startup Behavior:**
- Verifies llama-server connectivity via `config.load_engine()` with a warmup completion
- Launches Telegram bot, Discord DM listener, and WebRTC bridge in background daemon threads
- Sentry daemon is **disabled** (commented out for debugging)
- CLI input loop runs on the main thread

**Previously (disabled now):**
- FastAPI server (uvicorn, port 8000) — all endpoints commented out
- Dashboard auto-open (`http://localhost:5173`) — disabled
- `/api/token`, `/api/telemetry`, `/api/stream` — commented out
- `stream_dispatch_loop` and `_telemetry_stream_loop` async tasks — disabled

### 2.2 Telegram C2 Bridge

**Status:** Fully implemented.

- **Authorization:** Hardcoded `AUTHORIZED_CHAT_ID = 1066305111`. All non-authorized users receive "Unauthorized user. Access denied."
- **Handlers Implemented:**
  - `/status` — Returns context health (token usage estimate)
  - `/compact` — Triggers manual context compaction
  - `/sentry on|off` — Toggles Sentry Mode
  - `/stop` — Sets `config.STOP_REQUESTED`; brain loop checks it between tool rounds and exits gracefully; requires `threaded=True` on TeleBot so handler fires concurrently
  - `/diagnostics on|off` — Toggles `config.DIAGNOSTICS_MODE`; when on, all `sys.stdout` lines are batched and forwarded to Telegram every 2 s via `tools/diagnostics.py`
  - `/screenshot` — Captures primary monitor via `mss`, sends as Telegram photo immediately
  - `/peek [N]` — Dumps last N (default 10, max 40) messages from `core.brain.messages` as HTML `<pre>` block; base64 image_url payloads replaced with `[IMG]` placeholder
  - `/gpu` — Reports true board VRAM (used / total / free + %) via `nvidia-smi`; falls back to a PyTorch-only view if `nvidia-smi` is unavailable
  - `/gesture on|off` — Toggles real-time hand gesture control; auto-disables Sentry (mutually exclusive)
  - `/intervention on|off` — Toggles Intervention Mode (proactive focus daemon)
  - `/initiative [0-3|more|less]` — Adjusts the awareness initiative dial (how often Aster speaks unprompted)
  - `/note <text>` — Appends a note to the notes store
  - `/find <thing>` — Assist Mode: locates the thing on screen and shows a dim-and-highlight overlay on the PC; replies with the match count
  - **Voice messages** — Downloads OGG, transcribes via Faster-Whisper on CUDA, routes text to Brain
  - **Photo messages** — Downloads image, converts to Base64, injects as `[NATIVE_IMAGE_PAYLOAD:...]` with caption
  - **Text messages** — Routes directly to `process_user_input()`
  - **Intruder naming** — When `sentry.WAITING_FOR_ID` is true, text input is intercepted; `_execute_llm_completion()` extracts the proper name from conversational input before saving the biometric profile

- **Audio Payload Interceptor:** `_dispatch_telegram_response()` checks for `[NATIVE_AUDIO_PAYLOAD:filepath]` tags in LLM output. If the file exists, sends as Telegram voice note and suppresses text. If the path doesn't exist (hallucinated tag), strips the tag and sends remaining text.

### 2.3 Discord Integration

**Status:** Fully implemented (two subsystems).

#### 2.3.1 Discord DM Listener (`tools/discord_listener.py`)
- Uses `discord.py` client with DM intents
- Whitelist-based: only processes messages from `ALLOWED_FRIENDS` (9 contacts: ninja, tiger, masky, barakat, george, emily, farah, tolba, Adham)
- Routes incoming DMs to `process_discord_chat()` (separate context from admin brain)
- Publishes events to realtime stream for dashboard display

#### 2.3.2 Discord REST API Sender (`tools/discord_api.py`)
- Uses raw HTTP requests to Discord API v10 (no discord.py dependency for sending)
- Contact map: 9 named contacts with Discord user IDs
- Flow: POST `/users/@me/channels` (open DM) → POST `/channels/{id}/messages`
- Returns structured success/error strings

#### 2.3.3 Discord Chat Brain (`core/brain.py:process_discord_chat`)
- **Separate conversation context** per friend (`discord_chat_histories` dict, keyed by lowercase name)
- **Separate system prompt** (`DISCORD_CHAT_SYSTEM_PROMPT`) with personality calibration per friend
- **Separate tool set** (3 tools only: `forward_to_owner`, `save_personal_fact`, `get_current_track`)
- **Honorific enforcement:** Friends listed in `config.DISCORD_FEMALE_NAMES` (`self_config.yaml → contacts.female_names`) are never addressed as "Sir" — guardrail regex rewrites output
- **4-round max tool loop** with `_execute_llm_completion()` (native REST endpoint)
- **Output sanitization:** Strips XML tags, bracketed thoughts, and leaked model routing tags
- **Memory staging:** Discord friend facts are saved to `discord_memories.json` (not directly to ChromaDB). Admin can sync via `sync_discord_memories` tool.

### 2.4 WebRTC Audio Bridge (`webrtc_bridge.py`)

**Status:** Fully implemented and **ACTIVE**. Runs as a daemon thread in `main.py`.

- **Architecture:** LiveKit Agents SDK with `ManualWebRTCBridge` class
- **Pipeline per participant:**
  1. `rtc.AudioStream` (16kHz mono) → Silero VAD → `StreamAdapter` → `LocalWhisperSTT` (Faster-Whisper CUDA int8)
  2. Final transcript → `process_user_input()` (brain) via `asyncio.to_thread()`
  3. Response text → `LocalKokoroTTS` (Kokoro 82M) → `rtc.AudioSource` → published track
- **Barge-in support:** `START_OF_SPEECH` event cancels active response pipeline
- **Epoch-based response cancellation:** `_response_epoch` counter prevents stale responses from playing
- **Status callbacks:** Tool acknowledgments (e.g., "Capturing your screen.") are spoken mid-pipeline via TTS
- **Audio payload stripping:** `_clean_response()` removes `[NATIVE_AUDIO_PAYLOAD:...]` tags before TTS

**Standalone entry point:** `livekit_agent.py` — runs `webrtc_bridge.start_agent()` directly.

#### 2.4.1 Wake Word (openWakeWord)

**Status:** Fully implemented. Runs as part of the WebRTC bridge.

- **Engine:** openWakeWord — a tiny CPU-only model, so the GPU/Whisper STT stays idle while asleep. Fully offline and free (models auto-download once on the first run).
- **Default model:** `hey_jarvis` (configurable in `self_config.yaml`; also supports `alexa`, `hey_mycroft`, `hey_rhasspy`, or a custom `.onnx` path).
- **Behavior:** The voice agent starts **asleep** and ignores all speech until the wake word is detected. After `WAKE_INACTIVITY_TIMEOUT` seconds of silence (default 300s), it re-mutes automatically.
- **Config:** `WAKE_WORD_ENABLED` (default `True`), `WAKE_WORD_MODEL`, `WAKE_WORD_THRESHOLD` (0.5), `WAKE_INACTIVITY_TIMEOUT` (300s from `self_config.yaml`).
- **Desktop UI:** The reactor face shows a grey "asleep" state when the wake word hasn't been detected, overriding any mood colour. `tools/realtime_stream.py` publishes `wake` events consumed by `useWakeState` in the frontend.

### 2.5 Sentry Mode (`tools/sentry.py`)

**Status:** Fully implemented but **daemon disabled** in `main.py` (line 359, commented out for image pipeline debugging).

- **Trigger:** `/sentry on` via Telegram or `toggle_sentry_mode` tool
- **Loop:** `sentry_daemon()` runs every 5 seconds when `SENTRY_ACTIVE` is True
- **Camera Access:** `cv2.VideoCapture(0)` — opens webcam, discards 5 warm-up frames, captures 1
- **Analysis Pipeline:**
  1. `capture_frame_base64()` captures webcam frame
  2. `_analyze_frame_with_llm()` sends to `_execute_llm_completion()` with `image_url` for scene description
  3. Fallback to `face_recognition` if vision analysis fails
- **Face Recognition Fallback:**
  1. Detect faces via `face_recognition.face_locations()`
  2. Encode faces via `face_recognition.face_encodings()`
  3. Compare against `known_face_encodings` (loaded from `Aster_Vault/Faces/`)
  4. **Known as "Mohamed":** Ignore (no notification)
  5. **Known as other:** Send Telegram alert with 5-minute cooldown per person
  6. **Unknown:** Save frame as `Aster_Vault/temp_intruder.jpg`, send photo to Telegram, set `WAITING_FOR_ID = True`

### 2.5.1 Intervention Mode (`tools/intervention.py`)

**Status:** Fully implemented. Daemon started in `main.py`, idle until `/intervention on`.

- **Trigger:** `/intervention on` via Telegram or the `toggle_intervention_mode` tool
- **Loop:** `intervention_daemon()` polls the foreground window every `INTERVENTION_CHECK_INTERVAL`s (config; default 30s) when `INTERVENTION_ACTIVE` is True
- **Focus accumulator:** `classify_window(title)` (pure, unit-tested) matches the active window title against `config.DISTRACTION_KEYWORDS` (YouTube/Reddit/Twitter/Instagram/TikTok/Twitch/Netflix/Facebook). Continuous time on the same distraction is accumulated; one glance-away poll is tolerated before the timer resets.
- **Trigger condition:** accumulated time ≥ `INTERVENTION_THRESHOLD` (default 1800s) AND not in `INTERVENTION_QUIET_HOURS` AND not snoozed AND past `INTERVENTION_COOLDOWN` since the last one
- **Delivery:** `_trigger_intervention()` flags the window, then routes a `[System Internal: …]` nudge through the brain so Aster speaks in persona — over a live LiveKit call via `webrtc_bridge.speak_intervention()`, else a Telegram text + Kokoro voice note
- **Tools:** `toggle_intervention_mode` (on/off), `close_distraction_window` (closes the flagged window via `pygetwindow` `.close()` → `WM_CLOSE`), `snooze_intervention(minutes)`
- **Coexistence:** unlike Sentry/Gesture it uses no webcam/GPU, so it is *not* mutually exclusive with them.

### 2.5.2 Awareness Mode (`tools/awareness.py`)

**Status:** Fully implemented (Phase A). Daemon started in `main.py`, active by default.

A background daemon polls the screen + webcam every `AWARENESS_INTERVAL` seconds (default 300s, from `self_config.yaml`) and maintains a `current_context` snapshot that is injected into the brain at the top of every turn via `render_context_block()`. Also fires occasional proactive nudges through the brain.

- **Context block injected into every turn:**
  - **A0 — Time-aware block:** local time, day, session duration, whether late-night has been remarked on.
  - **A3 — Ambient block:** screen description, webcam description (person + posture), lighting assessment, mood read, unsurfaced observations.

- **Scene capture (`_brief_scene_describe`):** One low-token LLM call (~120 tokens) captures both screen and webcam simultaneously. Bypasses tools and never appends to the main `messages` list.

- **Mood inference (`_infer_mood`):** Keyword-only (no extra LLM call), mirroring `tools/sentiment.py`. Uses webcam posture keywords ("slumped", "tired", "yawn"), message frequency/length, emoji presence, and late-night heuristics. Moods: `focused`, `tired`, `animated`, `terse`, `neutral`.

- **Proactive nudges (gated by initiative level):**
  - **A5 — Return-from-absence compliment:** Fires when Mohamed returns after ≥ `AWARENESS_ABSENCE_THRESHOLD` seconds (default 900s). Uses a tiny LLM call (~40 tokens) to detect one concrete change between pre-absence and post-return webcam descriptions (clothing, hair, posture, room). Delivered via WebRTC if a call is live, else Telegram text + Kokoro voice note.
  - **A6 — Environment nudge:** Fires when lighting shifts to "dim." Rate-limited by `AWARENESS_ENV_COOLDOWN` (default 2 hours). Suggests raising monitor brightness or turning on a lamp.

- **Brain-busy semaphore:** The daemon defers its LLM call when the main brain is mid-turn (`_brain_busy` event), preventing races on the single llama-server instance.

- **Initiative dial (`set_initiative`):** 0=silent, 1=low, 2=medium (default), 3=high. Per-level capability matrix controls `unsolicited_max_per_hour`, `compliment`, `env_nudges`, `context_questions`. Toggled via the `set_initiative` admin tool or `/initiative` Telegram command. Natural-language phrases supported: "less initiative", "more initiative", "silent", "max initiative", etc.

- **Tools:** `set_initiative(level)`. The `render_context_block()` and related functions are called internally by `core/brain.py` — they are not exposed as their own admin tools.

### 2.5.3 Self-Knowledge Layer (`tools/self_knowledge.py`)

**Status:** Fully implemented.

A read-only introspection layer that gives Aster runtime awareness of his own systems. Six admin tools backed by pure functions that never touch the brain's main `messages` list:

| Tool | Function |
|---|---|
| `get_my_config(section)` | Returns `self_config.yaml` (or one section) as YAML text |
| `list_my_contacts(platform)` | Lists Discord contacts (from `tools/discord_api.py`) and Telegram authorized user |
| `list_my_capabilities()` | Structured JSON snapshot: vision, audio, memory, PC control, integrations, modes |
| `get_my_status()` | Live runtime: uptime, active daemons (read from live module flags), VRAM/RAM via PyTorch + psutil, initiative level, personality mode |
| `get_my_memory_stats()` | ChromaDB fact count, memory.md size + line count, RAG vault document count (via `core/memory.get_collection_stats()`) |
| `describe_my_tool(tool_name)` | Returns the full JSON schema of any admin tool by name |

**Design split:** Static facts (identity, paths, intended integrations) come from `self_config.yaml`; live runtime state (active daemons, initiative level, library availability) is read directly from live module globals — never from the YAML, since those values can drift the moment a toggle fires.

### 2.5.4 Notes System (`tools/notes.py`)

**Status:** Fully implemented.

A lightweight personal note-taking system backed by `Aster_Vault/notes.md`. Separate from the memory pipeline — intended for reminders, ideas, and one-off to-dos rather than long-term vector facts.

| Tool | Function |
|---|---|
| `save_note(text)` | Appends a timestamped entry (`- **YYYY-MM-DD HH:MM** — text`) to `Aster_Vault/notes.md` |
| `get_notes()` | Returns the full contents of `Aster_Vault/notes.md` |

Also available via Telegram: `/note <text>` and `/find <thing>` (unchanged).

### 2.6 Vision Mode (`tools/vision.py`)

**Status:** Fully implemented. Native multimodal vision via `image_url` content arrays (Qwen 3.6 mmproj).

#### 2.6.1 Screen Capture & Analysis (`look_at_screen`) + UI Element Locator

**`look_at_screen`** (vision model): `capture_screen_base64()` grabs primary monitor via `mss` → Base64 JPEG → `_execute_llm_completion()` with `image_url`, temperature 0.2, prompt asking for full screen description.

**`locate_ui_element_ex(goal)`** — the core for `smart_click`, `smart_type`. Three-track architecture (2026-07-11 rework); returns `{"x","y","source","text","score",...}` in logical pixels — `locate_ui_element` is the `(x, y)` back-compat wrapper. Goal parsing/scoring shared across all tracks via `tools/locator_common.py`: `parse_goal` strips filler words ("the search bar at the top" → tokens `{search}`), extracts **spatial hints** (top/bottom/left/right/center → soft position prior, ×0.8 on contradiction) and **control-type hints** ("button" → ButtonControl); `score_text` = `max(SequenceMatcher ratio, token overlap × 0.85, substring 0.9-0.95)`.

**Track 0 — Windows UI Automation accessibility tree (`tools/uia.py`, primary, ~0.1-2s, zero GPU):**
1. Walks the foreground top-level window (2.0s budget) + taskbar (0.8s budget) via the `uiautomation` package, collecting visible named controls (Name, ControlTypeName, BoundingRectangle)
2. Chromium/Electron apps enable accessibility lazily — the walk itself is the wake-up call; a sparse result (<12 named controls) triggers one retry after 0.8s (verified live: 27 controls cold → 1035 warm)
3. Fuzzy scoring + type-hint bonus (+0.05) + spatial prior; threshold **0.70**
4. UIA rects are physical desktop px, scaled by `pyautogui.size() / root-rect` for DPI safety
5. Per-thread COM init (`UIAutomationInitializerInThread`) since `execute_tool` runs on CLI/Telegram/LiveKit threads. Any failure → `None` → fall through (never crashes the brain). Limits: elevated windows unreadable from a non-elevated process; secure desktop off-limits.

**Track 1 — Full-screen OCR (~2-4s, cached):**
1. Screenshot decoded to BGR via OpenCV, `cv2.resize` 2× upscale
2. `pytesseract.image_to_data(..., '--oem 3 --psm 11')` × 2 (normal + inverted for dark UIs), words grouped into lines by `(block_num, par_num, line_num)`
3. Best line ≥ **0.5** returned; DPI corrected (÷2 upscale × logical/physical)
4. The capture+OCR pass is cached ~4s (`_get_screen_ocr`); **every GUI action calls `invalidate_screen_cache()`** so consecutive locates on an unchanged screen are free but post-action locates re-capture

**Track 2 — OmniParser v2 YOLO + icon captioning (last resort):**
1. `microsoft/OmniParser-v2.0` icon_detect YOLOv8 (via `huggingface_hub` + `ultralytics`, CUDA, released from VRAM after each use) detects element boxes
2. Boxes get text labels by intersecting the **cached Track-1 OCR lines** (`_texts_in_box`) — the old one-tesseract-call-per-crop approach cost ~0.4s × N boxes (~45s on a busy screen)
3. If no labeled box scores ≥ **0.45** (`_YOLO_SCORE_THRESHOLD`; the old 0.3 let nonsense goals match random elements — regression-tested), the top-8 most-confident **text-less** boxes (≤15% screen area — real icons) are captioned by `_caption_crop` and matched on function descriptions ("settings gear" ↔ goal "settings")
4. Track 2 is demoted by `config.USE_PIXEL_FALLBACK` (default true; false never loads the YOLO model). Captioner via `config.ICON_CAPTIONER` (`vision.icon_captioner` in self_config.yaml): `"llm"` (default — resident model, zero extra VRAM, ~1-2s/crop) / `"off"`. The Florence-2 experiment was dropped (never wired).

Tesseract executable configured at module import time from `C:\Program Files\Tesseract-OCR\tesseract.exe` (env override → default paths → fail gracefully). The old `parse_screen()` / `UI_ELEMENT_COORDS` / YOLO coordinate pipeline was deleted in the Qwen swap (2026-09-27).

**Eval harness (`engine_testing/locator_eval.py`):** `capture <name>` saves labeled screenshots to `locator_cases/`, `run [--captions]` scores Tracks 1/2 offline against `cases.json` (`expected_box` hit-testing, per-track hit-rate + latency), `live "<goal>"` runs the full three-track locate on the live screen and moves the mouse to the match (never clicks). Threshold/captioner decisions are made here, with data; `run --no-pixel` measures the demoted-Track-2 A/B.

#### 2.6.2 Webcam Capture (`capture_webcam_base64()`)
- Captures frame, detects faces, identifies known persons from vault
- Returns `(base64_string, detected_names_list)` tuple
- Used by `look_through_webcam` tool which now sends the raw image to the vision model via `image_url` while injecting facial recognition identities into the prompt

#### 2.6.3 Face Vault (`Aster_Vault/Faces/`)
- Images named by person (e.g., `Mohamed.jpg`, `George.jpg`)
- Encodings loaded at module import time via `load_known_faces()`
- Hot-reload supported via `load_known_faces()` call after adding new faces

#### 2.6.4 Screen Watcher (`start_screen_watcher()`)
- **Background daemon thread** that captures and analyzes the screen every 10 seconds for a target word
- Now uses `capture_screen_base64()` + `_execute_llm_completion()` (the archived `parse_screen()` pipeline was deleted in the Qwen swap)
- **Timeout:** 360 iterations (1 hour) to prevent zombie threads
- **Alert:** Sends Telegram message via `config.bot` when target text is detected
- **Memory Injection:** On success, appends timestamped event to `Aster_Vault/memory.md` so the main LLM loop is aware
- **Tool:** `watch_screen(target_text)` — registered in brain, auto-terminates on match

#### 2.6.5 Cold Storage Vision (`re_examine_image()`)
- **Vault Directory:** `Aster_Vault/images/` — created automatically on first save
- **Save Pipeline:** `save_image_to_cold_storage(base64_data, prefix)` decodes Base64, validates via OpenCV, writes JPEG with timestamp filename (e.g., `user_img_1716012345678.jpg`)
- **Tombstone Update:** After saving, `purge_media_cache()` replaces the image with a tombstone containing the saved file path
- **Retrieval Tool:** `re_examine_image(filepath, specific_question)` performs a stateless 1-turn visual inference:
  - Reads image from disk → converts to Base64 → sends to LLM with question via `image_url` → returns text answer
  - Image never enters main conversation history — VRAM stays clean after the call
  - Temperature: 0.2 for deterministic visual analysis
- **Use Case:** User sends image → Aster describes it → VRAM flushes image to disk → User asks follow-up question → Aster uses `re_examine_image` to answer from cold storage

#### 2.6.6 Assist Mode — Screen Highlight (`tools/assist.py`)
- **Purpose:** When the user asks *where* something is on screen, Aster dims the
  whole display and highlights the located item(s) — a visual "point at it" aid.
- **`locate_ui_elements_boxed(goal, threshold=0.5)`** (`tools/vision.py`) — a
  multi-match, box-returning sibling of `locate_ui_element`. Reuses the same
  capture/scale preamble and two-track pipeline: Track 1 (pytesseract) collects
  **every** line scoring ≥ threshold; Track 2 (YOLO `icon_detect`) runs only as a
  fallback when Track 1 finds nothing. Boxes are DPI-corrected to logical pixels
  (Track 1 keeps the `/2` for the 2× OCR upscale; Track 2 does not). Near-identical
  boxes (centres within ~10 px) are deduped. Returns `[{box:(l,t,w,h), score, text}]`.
- **`highlight_regions(boxes, duration=6.0)`** (`tools/assist.py`) — spawns a
  stdlib-`tkinter` overlay on a dedicated daemon thread (tkinter is not
  thread-safe, so the window *and* its `mainloop` run on that one thread). The
  overlay is a fullscreen, `overrideredirect`, topmost window at `-alpha 0.80`
  (desktop dimmed to ~20%); each match's interior is painted in a
  `-transparentcolor` key so it shows through at full brightness, framed by a gold
  outline. Auto-dismisses after ~6 s or on any key/click; a fresh trigger replaces
  the prior overlay. tkinter errors are caught — the brain never crashes.
- **Triggers:** the `highlight_on_screen` admin tool (LLM-invoked when the user
  asks where something is) and the `/find <thing>` Telegram command.
- **Limitation:** primary monitor only (`capture_screen_base64()` grabs
  `sct.monitors[1]`).

### 2.7 GUI Automation — ACTIVE

**Status:** Fully active. Five tools registered in `ADMIN_TOOLS` and dispatched in `execute_tool()`.

| Tool | Parameters | Behaviour |
|---|---|---|
| `smart_click` | `goal: str`, `confirm_send: bool?` | When `USE_DOM_MOTOR` is on and a focused browser is attached, a DOM fast path (Playwright ARIA shortlist; System-1 kernel optional) runs first; send/submit/destructive clicks return `FAILED — BLOCKED` until `confirm_send: true`. Otherwise `locate_ui_element_ex(goal)` (Track 0 UIA → Track 1 OCR → Track 2 YOLO+caption) → pre-frame snapshot → `pyautogui.click` → cache invalidate → 0.8s sleep → **blockwise pre/post diff** (`screens_differ`) → result carries match provenance (source/text/score, low-confidence flag <0.65), foreground window title, and a no-visible-change WARNING when the diff is flat. Locate miss returns `FAILED — ...`. |
| `smart_type` | `goal: str`, `text: str` | Locate field → click → **UIA focus check** (`focused_control_type()`): Edit/ComboBox (or UIA unavailable) → Ctrl+A + paste; DocumentControl → paste WITHOUT select-all (never wipes a document); any other type → `FAILED`, nothing typed. Result carries provenance + foreground title. |
| `type_text` | `text: str` | Pastes at current cursor (pyperclip + Ctrl+V). Much faster than `smart_type` — skips the screen-search step. Use when the correct field is already focused. Result carries foreground title. |
| `smart_scroll` | `direction: str`, `clicks: int` | Scrolls mouse wheel; pre/post diff warns "already at the top/end" when the screen did not change. |
| `press_key` | `key: str` | `pyautogui.press(key)` — Enter, Tab, Escape, arrows, function keys, etc. Result carries foreground title. |
| `highlight_on_screen` | `goal: str` | Assist Mode — `locate_ui_elements_boxed(goal)` (all matches as boxes, cached-OCR + YOLO-text tracks, no captioning) → `assist.highlight_regions()` dims screen + highlights matches. See Section 2.6.6. |
| `browse_web` | `url?`, `site?`, `query?`, `max_chars?` | Opens a live page in Aster's own browser (real Chrome/Edge on the dedicated persistent profile) and returns title + visible text + HTTP status. **No arguments reads the CURRENT page** (after interacting). `site='linkedin'`/`'amazon.eg'`/`'youtube'` target the site's own search; no query → home page. Read-only. `automation.web_browse: false` hides it. With `use_real_profile: true` it refuses loudly with the `login_once.py` alternative (the owner's real profile can NEVER be driven — see the DOM motor notes below). On a login wall the model tells the owner to run `python login_once.py <site>` once; the session persists in the dedicated profile and every later browse is logged in. |
| `smart_type` (web) | `goal`, `text`, `submit?` | On a web page the DOM motor fills the field; `submit=true` presses Enter to run the search. Chain: `browse_web(site=…)` → `smart_type(goal, text, submit=true)` → `browse_web()` to read results. |

**Loop-level guard:** the ReAct loop counts `smart_click` goals per turn and appends a STOP-hammering warning from the 3rd identical click. All `FAILED —` results feed the failure-blind-claim guard (the model cannot report success over a failed GUI action).

**Implementation split:**
- `locate_ui_element_ex()` / Tracks 1-2 live in [`tools/vision.py`](tools/vision.py); Track 0 + focus/foreground helpers in [`tools/uia.py`](tools/uia.py); shared scoring in [`tools/locator_common.py`](tools/locator_common.py)
- `verify_action_result()` lives in [`tools/vision.py`](tools/vision.py) (model free-text, `n_predict=80`) — NOT called by the dispatch path; per-click verification is the deterministic diff

**DOM-first automation motor (Stages 0–3, opt-in, default OFF):**
- `tools/dom.py` — Playwright ARIA snapshot (web, attached via CDP; only the focused page is actionable) or one shared UIA walk (Windows) → structural filter (interactive/visible/enabled) → role+lexical rank → ≤18 shortlist → deterministic execution (model output never becomes selectors/JS/coordinates). Send/submit/destructive clicks require `confirm_send=true` after owner confirmation (`DOM_MOTOR_SEND_POLICY=confirm`).
- `core/system1.py` — optional Laya kernel (lazy + ref-counted, CPU) picks among ambiguous shortlist candidates and provides the neutral-key yes/no gate API (`check_state`; not yet wired to a production caller); low-margin or failed decisions escalate to the LLM/pyautogui path. Decisions log to `Aster_Vault/system1_log.jsonl`; `engine_testing/calibrate_system1.py` fits temperatures and reports ECE.
- Flags: `USE_DOM_MOTOR`, `USE_LAYA_KERNEL`, `USE_PIXEL_FALLBACK` (Track-2 demotion), `DOM_MOTOR_SEND_POLICY`, `DOM_MOTOR_SHORTLIST_K`, `DOM_MOTOR_MIN_SCORE`, `BROWSER_CDP_PORT`, `LAYA_MARGIN_THRESHOLD`, `LAYA_KEEP_RESIDENT`, `LAYA_LOG_PATH`, `WEB_BROWSE_ENABLED`, `DOM_MOTOR_OWN_BROWSER`, `BROWSER_HEADLESS`, `BROWSER_PROFILE_DIR`, `BROWSER_USE_REAL_PROFILE`, `BROWSER_HUMAN_SEARCH`. Promotion is gated on the per-stage QA checklists in `engine_testing/qa/`.
- **Human-style site search (`BROWSER_HUMAN_SEARCH`, `automation.human_search`, 2026-09-26):** when ON, `browse_web(site, query)` lands on the site's HOME page and the model drives the site's own search bar (open site → `smart_type(search bar, q, submit=true)` → `browse_web()`) — the Astra-like interactive flow. The pre-mapped search URLs (`_SITE_SEARCH`) remain as the rollback path (flag off). Unknown bare site names still Bing-search (discovery must not guess dead hosts). E2E: amazon.eg + eggs → home → site search box (`Search Amazon.eg`) → `/s?k=eggs` with prices read from the page.
- **Real browser profile — settled impossible (2026-09-26, experimental):** Chrome ≥136 ignores `--remote-debugging-port` on the default user-data-dir (verified: window opens, port never binds), and its v20 app-bound cookie encryption refuses to decrypt through ANY copy or directory-junction of that dir — Chrome then PURGES the undecryptable cookies (a junction test wiped the owner's `Default` + `Profile 3` sessions; `Local State` key survived — re-login only). Edge is identical (v20). Therefore `automation.use_real_profile: true` now raises `RealProfileUnavailable` (tools/dom.py) with the supported alternative — NO silent logged-out guest fallback; `browse_web`/`web_click`/`web_type` all relay it.
- **`login_once.py`** — the supported way to browse logged in: opens `<site>`'s login page in Aster's dedicated persistent profile (`browser_profile_dir`), polls for the session cookie (li_at/SID/user_session) or a page marker (WhatsApp Web), and closes itself on success; `--status` lists saved sessions. One manual login per site; sessions persist on disk.
- **Browser lifecycle hygiene (2026-09-26):** Aster's own launches always pass `--disable-background-mode` and `close_web_context()`/detach kills the browser by `--user-data-dir` match (`_kill_browser_tree` — the Popen pid can hand off to a child, observed live), so a closed session can never leave background squatters holding the port/profile lock (the stale-guest-on-9222 class). `open_application("chrome")` debug-launches the DEDICATED profile only; with `use_real_profile` it opens the owner's browser normally instead of a dead debug attempt.
- **`browse_web`** — live web reading: navigates a URL, or `site` + optional query, and returns title + visible text. Query → search (`amazon.eg`/`potatoes` → `https://www.amazon.eg/s?k=potatoes`); no query → the site's home page (`site='youtube'` → youtube.com). Prefers a real system Chrome/Edge with a dedicated profile (bundled Chromium is bot-blocked). Detects a closed browser (`_is_web_alive`) and reconnects + retries once. E2E harness `engine_testing/e2e_browse_web.py`.
- **Enabled on this install (2026-09-26):** `dom_motor: true`, `laya_kernel: true`, `laya_margin_threshold: 0.01`, `laya_keep_resident: false`, `use_real_profile: false`; Laya 0.3.20 installed (downgraded `huggingface_hub` to 0.36.2, verified compatible). Live E2E `engine_testing/e2e_dom_motor.py run --query ball` on amazon.eg: motor found the search box, Laya picked the `Go` button (margin 0.79), first result ball price EGP 285.00 — see `engine_testing/qa/artifacts/e2e_amazon_result.json`. 2026-09-26 E2E (browser-profile fix): refusal path launches nothing; dedicated browse + type/submit + read works; detach leaves zero Aster chrome processes (12/12 checks).
- Dispatch and `pyautogui` calls live in [`core/brain.py`](core/brain.py) `execute_tool()`
- [`tools/gui.py`](tools/gui.py) still exists with `ui_type()` and `ui_press_key()` wrappers but is not imported — `press_key` and `smart_type` are dispatched directly via `pyautogui` in brain.py
- Tests: [`tests/test_locator.py`](tests/test_locator.py) (per-feature classes, no llama-server)

### 2.8 Spotify Control (`tools/media.py`)

**Status:** Fully implemented.

| Tool | Function |
|---|---|
| `get_current_track()` | Returns "{name} by {artist}" |
| `pause_spotify()` | Pauses playback |
| `resume_spotify()` | Resumes playback |
| `play_spotify_track(query)` | Searches and plays single track |
| `play_spotify_playlist(name)` | Fuzzy-matches playlist name, plays context |
| `play_liked_songs()` | Plays user's saved tracks |
| `skip_spotify_track()` | Next track |
| `previous_spotify_track()` | Previous track |
| `shuffle_spotify(state)` | Toggle shuffle |

**Device Management:** `ensure_active_spotify_device()` auto-transfers playback to first available device if none is active.

### 2.9 System Control (`tools/system.py`)

**Status:** Fully implemented.

| Tool | Implementation |
|---|---|
| `get_current_time()` | `datetime.now().strftime()` |
| `read_local_file(path)` | `open(path).read()` |
| `write_local_file(path, content)` | `open(path, 'w').write(content)` |
| `set_system_state(action)` | Win32 `rundll32` commands (lock/sleep/restart/shutdown) with 3-second delay |
| `set_volume(level, mute)` | `pycaw` COM interface to Windows audio endpoint |
| `boss_key()` | `pyautogui.hotkey("win", "d")` |
| `open_application(name, action)` | Start Menu shortcut scanning with fuzzy match (`difflib.get_close_matches`), fallback to `subprocess.Popen` |
| `list_directory_tree()` | Recursive walk, skips `.git`, `__pycache__`, `node_modules`, `.venv` |
| `list_running_processes(sort_by)` | `psutil.process_iter()`, top 25 by memory or CPU |
| `aster_shutdown_protocol(shutdown_os, delay)` | Sets `config.SHUTDOWN_REQUESTED` flag, optionally schedules `shutdown /s /t` |

### 2.10 Timers & Alarms (`tools/media.py`)

**Status:** Fully implemented.

- `set_timer(minutes, reason)` — Background daemon thread, fires `winotify` toast notification
- `set_alarm(time_str, reason)` — Parses flexible time formats (`%I:%M %p`, `%H:%M`), rolls to next day if past, fires toast

### 2.11 RAG / Web Research (`tools/rag.py`)

**Status:** Fully implemented.

| Tool | Function |
|---|---|
| `check_vault(topic)` | Searches `Aster_Vault/database/` for topic-matching `.md` files. Returns content if < 90 days old; returns "STALE" if older. |
| `deep_web_search(topic)` | 3-query DuckDuckGo search (topic, "what is X", "X latest news 2026"). Returns aggregated dossier + forced `save_to_vault` instruction. |
| `save_to_vault(topic, summary)` | Writes new `.md` file to `Aster_Vault/database/` with `TopicName_MM_DD_YY.md` naming. Deletes older versions of same topic. |

**Current RAG Vault Contents (20 entries):** AI_Model_for_Web_Research, Aster_Localization_Project, Elden_Ring, Gemma_4_Model_Family, Genifor_model, identification_of_rolledup_brown_ridged_leather_object, LiveKit_Voice_Agents_Library, LLM_Versioning_and_Capabilities, Memory_Management_System_Architecture, native_audio_tensor_capabilities, NieR_Automata, OnlyFans_business_model, Proprietary_LLM_Landscape, Rickroll_YouTube_URL, Tiger, Tokyo_Weather, Tolba, trim, TurboQuant, VRM_consumption.

### 2.12 Memory Pipeline

**Status:** Fully implemented. Hybrid dual-write architecture.

#### 2.12.1 Storage Layers

| Layer | Technology | Purpose |
|---|---|---|
| Semantic Vector Store | **ChromaDB** (PersistentClient) | Fuzzy semantic search (`memory_collection.query(query_texts, n_results=3)`) |
| Human-Readable Log | **Markdown file** (`Aster_Vault/memory.md`) | Append-only timestamped log, Obsidian-compatible |
| Discord Staging | **JSON file** (`discord_memories.json`) | Per-user fact staging with sync flags |

#### 2.12.2 Write Path (`memorize_fact`)
1. Append `- **[YYYY-MM-DD HH:MM:SS]** {fact}` to `memory.md`
2. Add document to ChromaDB collection with ID `mem_{timestamp}`

#### 2.12.3 Read Path (`recall_memory`)
1. `memory_collection.query(query_texts=[query], n_results=3)`
2. Returns pipe-delimited string of top 3 matches

#### 2.12.4 Deduplication Gate
- **Pre-write:** `is_fact_already_known()` in `brain.py` reads `memory.md`, performs:
  - Exact substring match (bidirectional)
  - Fuzzy match via `difflib.SequenceMatcher` with **85% similarity threshold**
- **Session consolidation:** `evaluate_and_memorize()` uses the LLM to extract atomic facts, cross-references against `memory.md` blacklist before writing

#### 2.12.5 Sliding Window (`trim_memory`)
- Token-aware sliding window using **character-based heuristic** (~4 chars per token)
- Default budget: 90% of `N_CTX` (~58,982 tokens of 65,536), leaving 10% headroom for generation
- Preserves index 0 (system prompt) — trims oldest non-system messages first
- Applied after every user message and assistant response

#### 2.12.6 Context Compaction (`/compact`)
- Triggered manually via `/compact` command
- Safety lock: refuses if < 2,000 estimated tokens
- LLM generates dense summary, replaces history with `[System Memory Restored: ...]`

#### 2.12.7 Media Cache Purge (Cold Storage Vision)
- `purge_media_cache()` runs after every `process_user_input()` call
- Surgically removes `images` and `audio` keys from message history to prevent VRAM bloat
- **Before flushing:** Images are decoded from Base64 and saved to `Aster_Vault/images/` as JPEG files with timestamp filenames
- Tombstone includes the saved file path: `"[System Visual Memory: ...archived locally at: {path}... use 're_examine_image' tool...]"`
- **Second pass:** Strips stale `[Image Memory:]` text blocks from assistant messages that survived the first purge

#### 2.12.8 Discord Memory Sync
- `sync_discord_memories()` → reads `discord_memories.json`, iterates unsynced facts, writes each to ChromaDB via `memorize_fact()`, marks as synced

### 2.13 Desktop Companion (`aster-ui/`)

**Status:** Active. A **Tauri** desktop app — Aster's "face". The old 3-panel dashboard
(`App.tsx`) was replaced by a single-screen companion window.

| Element | Function |
|---|---|
| **AsterFace** | Procedural 8-bit reactor core (SVG pixel rings + glowing core). Rings rotate; core pulses with Aster's voice amplitude (asymmetric-smoothed to kill flicker). States: idle / connecting / connected / asleep. Colour reacts to conversational mood — see below. |
| **CallControls** | Start Call ⇄ End Call toggle; Mute (toggles the local mic track). |
| **LogConsole** | Resizable bottom strip (drag handle) — live stdout stream via `WS /api/logs`. |
| **mini window** | Minimizing the main window shows a small always-on-top circular reactor; clicking it restores the main window. |

**Connection Architecture:**
- `tools/face_server.py` (FastAPI on `:8000`) serves `GET /api/token` and `WS /api/logs`.
- On "Start Call" the UI fetches a LiveKit JWT and joins room `aster-command-center`
  as identity `aster-web-client`. The token embeds `RoomConfiguration` →
  `RoomAgentDispatch(agent_name="aster")`, which **explicitly dispatches** the voice
  agent (required — the agent has `agent_name` set, so auto-dispatch is off).
- A Web Audio `AnalyserNode` on Aster's remote audio track drives the mouth animation.
- WebView2 mic access is enabled via the `--use-fake-ui-for-media-stream` flag set in
  `src-tauri/src/lib.rs`.

**Tauri:** two windows — `main` (`420×640`, normal) and `mini` (`140×140`, borderless,
transparent, always-on-top, hidden until the main window is minimized). Identifier
`com.aster.face`. Run with `npm run tauri dev`. Requires Rust toolchain + MSVC build
tools. WebView2 mic access is enabled via `--use-fake-ui-for-media-stream` in `lib.rs`.

**Custom Color Tokens:** `clinical-cyan` (#06B6D4), `obsidian` (#0F172A), `deepSlate-*`

**Sentiment-Reactive Face:** The reactor face changes colour with the mood of the
conversation. `tools/sentiment.py` keyword-classifies Aster's final response into
one of five moods — `calm` (blue), `working` (gold), `alert` (red), `music`
(white), `success` (green) — with **no extra LLM call**. `core/brain.py` emits the
result via `realtime_stream.publish_sentiment()`, plus a `working` flash the
moment any tool is dispatched. The frontend `useSentiment` hook consumes the
`{type:"sentiment"}` event off `WS /api/logs` and drives a colour palette in
`AsterFace.tsx` (SVG ring fill, core gradient, glow — all framer-motion eased,
no hard cuts). The `music` state is a smooth white shimmer; `success` and `music`
auto-revert to calm blue after ~4 s. The asleep grey (wake-word state) overrides
any mood colour.

### 2.14 Realtime Stream (`tools/realtime_stream.py`)

**Status:** Fully implemented.

- **Event Types:** `terminal`, `discord`, `telemetry`, `wake`, `sentiment`
- **Queue:** `queue.Queue(maxsize=500)` with overflow eviction (drop oldest)
- **Dispatch:** `stream_dispatch_loop()` reads queue, broadcasts JSON to all connected WebSocket clients
- **Cleanup:** Auto-removes disconnected clients on send failure

### 2.15 Memory Manager (`tools/memory_manager.py`)

**Status:** Fully implemented.

- **Storage:** `discord_memories.json` — flat JSON dict keyed by username
- **Schema per fact:** `{"fact": "...", "synced": bool}`
- **Thread-safe:** `_memory_lock` threading.Lock
- **Dedup:** Exact string match before write
- **Migration:** Backward-compatible with legacy string-only records

### 2.16 Audio Voice Note Generation (`tools/audio.py`)

**Status:** Fully implemented — Kokoro TTS 82M.

- Uses **Kokoro TTS 82M** (`kokoro` Python package, `KPipeline`)
- Lazy-loaded on first use to avoid startup overhead
- Voice: Custom `aster2.pt` tensor (falls back to `af_bella` if missing)
- Output: WAV files at 24kHz sample rate
- Returns `[NATIVE_AUDIO_PAYLOAD:{filepath}]` tag for Telegram dispatch
- The `generate_kokoro_voice` tool is registered in `ADMIN_TOOLS` and fully functional

---

## 3. Hardware & Optimization State

### 3.1 Target Hardware Profile

| Component | Specification |
|---|---|
| GPU | NVIDIA RTX 3080 (12GB VRAM) — inferred from code comments and 12GB max |
| OS | Windows 11 |
| CUDA | Present (nvidia cublas, cuda_nvrtc DLL paths patched at startup) |

### 3.2 VRAM Management Strategies

| Strategy | Implementation | Location |
|---|---|---|
| **Single Engine** | One llama-server instance serving the GGUF + mmproj model | `config.py`, `core/brain.py` |
| **KVarN KV Cache Quantization** | `--cache-type-k kvarn4 --cache-type-v kvarn2 --kv-tail-tokens 1024` — ~0.9 GB at 60k context (Qwen's hybrid attention keeps KV small) | llama-server (`start.bat`) |
| **Model Permalock** | llama-server loaded once, stays in VRAM permanently | `config.py`, `main.py` |
| **Whisper int8 Quantization** | Faster-Whisper loaded with `compute_type="int8"` — halves Whisper VRAM vs fp16 | `main.py`, `local_stt.py` |
| **Shared STT/TTS instances** | One Whisper model (Telegram + call) and one ref-counted Kokoro pipeline — no duplicate model copies during a call; Kokoro offloads when idle | `local_stt.py`, `tools/audio.py`, `local_tts.py` |
| **Cold Storage Vision** | Images archived to `Aster_Vault/images/` before VRAM flush; tombstone stores filepath for on-demand retrieval via `re_examine_image()` | `brain.py`, `vision.py` |
| **Media Cache Purge** | `purge_media_cache()` strips Base64 image data from conversation history after every turn (voice notes are text now) | `brain.py` |
| **Context Window** | 60k tokens (`--ctx-size 60000` in `start.bat`, `config.N_CTX=60000`) with KVarN KV cache | llama-server |
| **Character Heuristic Trimming** | `trim_memory()` uses `len() // 4` for token estimation | `core/memory.py` |
| **Context Compaction** | `/compact` command — LLM summarizes history, replaces messages array | `brain.py` |
| **CUDA DLL Path Patching** | `main.py` and `local_stt.py` manually inject NVIDIA DLL paths into `PATH` for CTranslate2 runtime | `main.py`, `local_stt.py` |

### 3.3 Estimated VRAM Budget

| Component | Estimated VRAM |
|---|---|
| llama-server/BeeLlama: Qwen 3.6 35B-A3B (IQ4_XS, 60k ctx, KVarN KV, ~26 MoE layers on CPU) + mmproj in RAM | **~9.4 GB (measured)** |
| Faster-Whisper medium.en (int8) — voice notes on **CPU** (~1.5 GB RAM); CUDA only during a LiveKit call | ~1.2 GB GPU **during a call only** — never resident |
| SpeechBrain ECAPA (voice recognition, eager CUDA) | ~80 MB |
| openWakeWord (CPU-only, negligible GPU) | ~0 MB GPU |
| Silero VAD | ~50 MB |
| OmniParser YOLO (loaded on first `smart_click` unless `USE_PIXEL_FALLBACK=false`, then released after use) | ~200 MB when active |
| Kokoro TTS 82M (loaded only during a call / voice note, then offloaded) | ~0.3-0.6 GB when active |
| **Total (idle, no call)** | **~8-9.5 GB** |
| **Total (on a call)** | **~8.5-10.5 GB** |

`/gpu` (Telegram) reports true board usage + per-process VRAM via `nvidia-smi`
(`torch.cuda.*` is not used — it only sees PyTorch and misses llama-server/Whisper).

### 3.4 Telemetry Reporting

The `/api/telemetry` endpoint is **currently disabled** along with the rest of the FastAPI stack. When active, it returns **hardcoded mock values** with no live GPU monitoring.

---

## 4. Known Work-In-Progress (WIP) & Bugs

### 4.1 Old FastAPI/Dashboard Block in `main.py` — Still Disabled

**Location:** `main.py`

The original in-`main.py` FastAPI server (telemetry loop, `/api/stream`, dashboard
auto-launch) remains commented out. **However**, a separate minimal FastAPI now runs:
`tools/face_server.py` (uvicorn on `:8000`, started as a daemon thread) serving the
LiveKit token + log WebSocket for the Tauri desktop companion. The face server IS
active; the old `main.py` block is not.

### 4.2 Sentry Daemon — Disabled

**Location:** `main.py:359`

The sentry daemon thread is commented out for image pipeline debugging:
```python
# threading.Thread(target=sentry.sentry_daemon, daemon=True).start()  # TEMP: disabled for image pipeline debugging
```
Sentry mode can still be toggled ON via the `toggle_sentry_mode` tool — the daemon just won't auto-start on boot.

### 4.3 Telemetry — Static Mock Data

**Location:** `main.py` (disabled)

`_get_telemetry_snapshot()` returns hardcoded strings. No actual `nvidia-smi`, `torch.cuda.mem_get_info()`, or similar live GPU monitoring is implemented.

### 4.4 Memory Facts Display — Removed

The old dashboard's hardcoded "Active Memory Facts" panel no longer exists — `App.tsx`
was rewritten as the single-screen Tauri face app.

### 4.5 Web Search Tool — Duplicate Implementation

**Location:** `tools/web.py` vs `tools/rag.py`

`tools/web.py` contains a standalone `search_web()` function using `DDGS().text()`. However, this function is **never imported or called** anywhere in the codebase. The brain uses `deep_web_search()` from `tools/rag.py` instead. `tools/web.py` is dead code.

### 4.6 Discord Honorific Regex — Fragile

**Location:** `brain.py:_enforce_discord_honorific()`

Uses regex to rewrite "Sir" → "Ma'am" and "Mr. {name}" → "Ms. {name}" in LLM output. This operates on the raw output string and could produce false positives on unrelated content (e.g., "Sirius" → "Ma'amus").

### 4.7 `whisper-models/` — Unused Model Present

**Location:** `whisper-models/models--Systran--faster-distil-whisper-large-v3/`

A Distil-Whisper Large v3 model directory exists but is **never loaded by any code**. Only `medium.en` is used.

### 4.8 `Aster_Models/` — Empty Directory

The `Aster_Models/` directory exists but contains zero files. Models are stored in `Aster_Vault/Models/` instead.

### 4.9 Spotify Credentials — Hardcoded

**Location:** `config.py:13-14`

`SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` are hardcoded in plaintext. Similarly, `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, and LiveKit credentials are all plaintext in source.

### 4.10 `discord_memories.json` — Present at Root

**Location:** `E:\LLM testing\Aster-localization\discord_memories.json`

The Discord memory staging file exists at the workspace root. This is the runtime state file for `tools/memory_manager.py`.

### 4.11 `kokoro_test.py` — Standalone Test Harness

This is a standalone interactive CLI for testing Kokoro TTS voice blending and synthesis. Not integrated into the main system. Supports voice blending, speed adjustment, and direct audio playback.

### 4.12 `index.html` — Vestigial

A standalone `index.html` exists at the workspace root. This is unrelated to the React dashboard (which lives in `aster-ui/`). Likely a test artifact.

### 4.13 GUI Automation Latency — `smart_click` / `smart_type`

**Location:** `tools/vision.py:locate_ui_element_ex()` / `tools/uia.py:uia_locate()`

Since the 2026-07-11 rework, most locates resolve on Track 0 (UIA) in ~0.1-2s with zero OCR. When UIA misses, Track 1 costs ~2-4s for the two full-screen pytesseract passes — and that result IS now cached (~4s TTL, invalidated on every GUI action), so a click-then-type sequence pays for OCR once. Track 2 no longer runs per-crop tesseract at all (box labels come from the cached full-screen lines); its cost is the YOLO load/predict plus up to 8 icon-caption completions (~1-2s each) on the rare caption path.

The `smart_click` tool description was updated and no longer references Florence-2. Functionally it uses UIA + OmniParser v2 + pytesseract (and, opt-in, the DOM motor). The `smart_scroll` tool has also been refactored — it no longer requires a `goal` parameter, taking `direction` + `clicks` instead.

### 4.14 System Prompt — Tool Execution Directives

**Location:** `brain.py` system prompt

The system prompt's CHAINING RULES section uses "AUTONOMOUS AGENT LAW" (rule 3) which instructs the LLM to:
1. Provide conversational acknowledgment alongside every tool call
2. Never hallucinate/roleplay tool results — wait for actual data before summarizing

The former "AUTONOMOUS GUI NAVIGATION LAW" has been updated to reflect the new tool names (`smart_click`, `press_key`, `smart_type`).

**Discord hard override updated:** `"Discord hard override: For Discord messaging requests, NEVER call look_at_screen or open_application."`

### 4.15 Agentic Loop — Post-Tool Ghost Prod

**Location:** `brain.py` agentic loop

The 15-round agentic tool loop includes a failsafe for "Post-Tool Apathy" (routinely observed on Gemma-4; a mild form survived the swap) — when the model returns an empty string after successfully executing tools. A `tools_executed` boolean tracks whether any tool ran during the session. If the model exits the loop with an empty response and tools were executed, a neutral `[System Internal]` user message is injected as a nudge, one final `_execute_llm_completion()` call is fired, and the prod message is wiped from history via `.remove()` so only the user request, tool execution, and final response remain.

---

## Appendix A: Complete Tool Registry

The following **69 tools** are registered in `ADMIN_TOOLS` ([`core/brain.py`](core/brain.py)) and available to the LLM via native OpenAI tool calling (`tools=ADMIN_TOOLS` param):

| # | Tool Name | Category |
|---|---|---|
| 1 | `get_current_time` | System |
| 2 | `read_local_file` | File I/O |
| 3 | `write_local_file` | File I/O |
| 4 | `set_system_state` | System |
| 5 | `set_volume` | System |
| 6 | `boss_key` | System |
| 7 | `open_application` | System |
| 8 | `get_current_track` | Spotify |
| 9 | `pause_spotify` | Spotify |
| 10 | `resume_spotify` | Spotify |
| 11 | `play_spotify_track` | Spotify |
| 12 | `play_spotify_playlist` | Spotify |
| 13 | `play_liked_songs` | Spotify |
| 14 | `skip_spotify_track` | Spotify |
| 15 | `previous_spotify_track` | Spotify |
| 16 | `shuffle_spotify` | Spotify |
| 17 | `set_timer` | Timer |
| 18 | `set_alarm` | Timer |
| 19 | `memorize_fact` | Memory |
| 20 | `recall_memory` | Memory |
| 21 | `aster_shutdown_protocol` | System |
| 22 | `check_context_health` | Diagnostics |
| 23 | `look_at_screen` | Vision |
| 24 | `send_discord_message` | Discord |
| 25 | `toggle_sentry_mode` | Sentry |
| 26 | `toggle_gesture_mode` | Gesture |
| 27 | `check_vault` | RAG |
| 28 | `deep_web_search` | RAG |
| 29 | `save_to_vault` | RAG |
| 30 | `generate_kokoro_voice` | Audio |
| 31 | `list_directory_tree` | File I/O |
| 32 | `list_running_processes` | System |
| 33 | `sync_discord_memories` | Memory |
| 34 | `watch_screen` | Vision |
| 35 | `look_through_webcam` | Vision |
| 36 | `re_examine_image` | Vision (Cold Storage) |
| 37 | `smart_click` | GUI Automation |
| 38 | `smart_type` | GUI Automation |
| 39 | `smart_scroll` | GUI Automation |
| 40 | `press_key` | GUI Automation |
| 41 | `type_text` | GUI Automation |
| 42 | `toggle_intervention_mode` | Intervention |
| 43 | `close_distraction_window` | Intervention |
| 44 | `snooze_intervention` | Intervention |
| 45 | `highlight_on_screen` | GUI Automation (Assist Mode) |
| 46 | `set_initiative` | Awareness |
| 47 | `get_my_config` | Self-Knowledge |
| 48 | `list_my_contacts` | Self-Knowledge |
| 49 | `list_my_capabilities` | Self-Knowledge |
| 50 | `get_my_status` | Self-Knowledge |
| 51 | `get_my_memory_stats` | Self-Knowledge |
| 52 | `describe_my_tool` | Self-Knowledge |
| 53 | `save_note` | Notes |
| 54 | `get_notes` | Notes |

**Removed tools (archived):** `parse_screen`, `ui_click`, `ui_type`, `ui_press_key`, `analyze_screen` (old YOLO-coordinate pipeline — replaced by OmniParser + `smart_click`/`press_key`).

**Tool Call Mechanism:** native OpenAI function calling — `ADMIN_TOOLS` (full JSON-Schema dicts) travel in the `tools` param of `/v1/chat/completions`; llama-server returns a structured `tool_calls` array (read by `_extract_native_tool_call`/`_extract_all_native_tool_calls`) and tool results go back as `role:"tool"` messages with `tool_call_id`. The legacy XML-in-text path was removed in the Qwen swap. Up to 15 rounds of tool execution per user turn.

## Appendix B: Thread Architecture

| Thread | Target | Daemon | Status |
|---|---|---|---|
| Main Thread | CLI input loop | No | Active |
| Thread 1 | Telegram bot `infinity_polling()` | Yes | Active |
| Thread 2 | Discord DM listener `client.run(token)` | Yes | Active |
| Thread 3 | WebRTC bridge (LiveKit agent) | Yes | Active |
| Thread 4 | Gesture daemon (MediaPipe; inactive until `/gesture on`) | Yes | Active |
| Thread 5 | Face server (`tools/face_server.py`, uvicorn on `:8000`) | Yes | Active |
| Thread 6 | Sentry daemon (5-second polling loop) | Yes | **Disabled** (commented out) |
| Thread 7 | Screen watcher daemon (10-second polling, 1-hour timeout) | Yes | On-demand |
| Thread 8 | Intervention daemon (foreground-window polling; inactive until `/intervention on`) | Yes | Active |
| Thread 9 | Assist Mode overlay (`tools/assist.py` tkinter `mainloop`; spawned per `/find` / `highlight_on_screen`, self-destructs after ~6s) | Yes | On-demand |
| Thread 10 | Awareness daemon (`tools/awareness.py`; screen+webcam polling every `AWARENESS_INTERVAL`s, proactive nudges gated by initiative dial) | Yes | Active (default) |

Async tasks (in WebRTC bridge only):
- `_process_user_speech()` — speech-to-text → brain → TTS pipeline
- `_play_tts_response()` — audio frame publishing loop

## Appendix C: File Inventory

| File | Lines | Purpose |
|---|---|---|
| `main.py` | ~580 | Entry point, Telegram handlers, engine warmup, headless CLI mode, awareness daemon boot |
| `config.py` | ~227 | Single-engine config: llama-server, KV cache, ChromaDB, credential loading, wake-word, awareness, intervention settings, `self_config.yaml` loader |
| `self_config.yaml` | ~107 | Single source of truth for identity, paths, integrations, daemon boot defaults, tunable knobs |
| `core/brain.py` | ~3100 | LLM router (`/v1/chat/completions`), native OpenAI tool calling, ReAct agent loop, hallucination detector, system prompts, Discord brain, GUI tool dispatch, 69-tool registry |
| `core/memory.py` | ~110 | Hybrid memory read/write, character-heuristic `trim_memory()`, `get_collection_stats()` for self-knowledge |
| `tools/system.py` | 218 | OS control, file I/O, process list |
| `tools/media.py` | 223 | Spotify control, timers, alarms |
| `tools/sentry.py` | ~146 | Vision-model sentry daemon (with face_recognition fallback) |
| `tools/intervention.py` | ~210 | Intervention Mode — foreground-window focus daemon; `classify_window()`, toggle/snooze/close, brain-routed in-persona delivery |
| `tools/awareness.py` | ~559 | Awareness Mode — ambient screen+webcam polling daemon, context-block injector, mood inference, proactive nudges, initiative dial |
| `tools/self_knowledge.py` | ~257 | Self-knowledge layer — 6 introspection functions (`get_my_config`, `list_my_contacts`, `list_my_capabilities`, `get_my_status`, `get_my_memory_stats`, `describe_my_tool`) |
| `tools/notes.py` | 39 | Lightweight note-taking — `save_note()` / `get_notes()` backed by `Aster_Vault/notes.md` |
| `tools/vision.py` | ~1330 | Screen capture (Base64), webcam capture + face recognition, cold storage, screen watcher. DOM-motor Track -1 + UIA + OCR + OmniParser/YOLO track locator (`locate_ui_element_ex`; Track 2 gated by `USE_PIXEL_FALLBACK`); `locate_ui_elements_boxed()` multi-match box locator for Assist Mode. The old YOLO coordinate pipeline's archived `'''...'''` blocks were deleted in the Qwen swap. |
| `tools/dom.py` | ~1135 | DOM-first motor: ARIA snapshot parser, structural filter + role/lexical shortlist (K≤18), Playwright-over-CDP web execution with send gate + focus requirement, UIA shortlist with walk reuse; dedicated-profile own-launch with `--disable-background-mode` + profile-dir-matched kill on detach; `RealProfileUnavailable` refusal for `use_real_profile` (the real profile is impossible — Chrome ≥136 + app-bound cookies); `web_cookies()` for login_once. Flag `USE_DOM_MOTOR` (default off). |
| `core/system1.py` | ~330 | System-1 decision kernel (Laya): neutral-key choice schemas, margin gating, escalation, JSONL decision log. Flag `USE_LAYA_KERNEL` (default off). |
| `tools/assist.py` | ~115 | Assist Mode — stdlib-tkinter dim overlay (`highlight_regions()`) highlighting located screen elements; auto-dismiss on timeout/key/click |
| `tools/gui.py` | 29 | `ui_type()` and `ui_press_key()` pyautogui wrappers (not imported — kept as reference) |
| `tools/discord_api.py` | 107 | Discord REST API sender |
| `tools/discord_listener.py` | 85 | Discord.py DM listener |
| `tools/realtime_stream.py` | 88 | WebSocket event queue + broadcast loop — feeds the desktop UI log strip |
| `tools/face_server.py` | ~95 | FastAPI on `:8000` — `GET /api/token` (LiveKit JWT w/ agent dispatch) + `WS /api/logs` |
| `tools/memory_manager.py` | 166 | Discord friend memory staging |
| `tools/rag.py` | 90 | RAG vault check, web search, vault save |
| `tools/audio.py` | ~95 | Kokoro TTS 82M — shared ref-counted pipeline (`acquire`/`release_kokoro_pipeline`), offloads from VRAM when idle |
| `tools/web.py` | 20 | Standalone web search (dead code — not imported) |
| `tools/diagnostics.py` | ~80 | `sys.stdout` DiagnosticsStream shim, batched Telegram forwarder thread, `send_error()` always-on error relay |
| `tools/gesture.py` | ~180 | MediaPipe Hands gesture daemon; Tier-1 gesture map (volume/pause/skip/boss/lock); camera held open while active; mutually exclusive with Sentry |
| `tools/sentiment.py` | ~30 | Keyword-only `classify_sentiment()` — maps Aster's final response to a reactor-face mood (`calm`/`working`/`alert`/`music`/`success`); no LLM call; unit-tested in `tests/test_sentiment.py` |
| `webrtc_bridge.py` | 456 | LiveKit WebRTC bridge (active daemon) |
| `local_stt.py` | ~140 | Faster-Whisper STT — `get_whisper_model()` shared singleton (Telegram + LiveKit) + `LocalWhisperSTT` adapter |
| `local_tts.py` | ~75 | Kokoro TTS adapter for LiveKit — uses the shared `tools/audio.py` pipeline |
| `livekit_agent.py` | 7 | Standalone LiveKit agent entry point |
| `kokoro_test.py` | 173 | Kokoro TTS test harness |
| `tests/test_migration.py` | ~814 | PyTest suite: 32 tests covering engine, tools, multimodal |
| `tests/test_intervention.py` | ~25 | PyTest: 5 tests for `classify_window()` distraction detection |
| `tests/test_sentiment.py` | ~30 | PyTest: 7 tests for `classify_sentiment()` mood classifier |
| `checklist_test.md` | ~180 | Manual testing checklist for live validation |
| `aster-ui/src/App.tsx` | 529 | Full dashboard React component |


  Quick reference for the gestures:

  ┌──────────────────────┬─────────────────────────────────────────┐
  │       Gesture        │                 Action                  │
  ├──────────────────────┼─────────────────────────────────────────┤
  │ ☝️  Index finger only │ Raise/lower hand to control volume     │
  │                      │ (live)                                  │
  ├──────────────────────┼─────────────────────────────────────────┤
  │ 🖐️  Open palm, hold   │ Pause / Play toggle (queries actual    │
  │ 0.5s                 │ Spotify state)                          │
  ├──────────────────────┼─────────────────────────────────────────┤
  │ ✊ Fist, hold 0.5s   │ Pause Spotify (definitive)              │
  ├──────────────────────┼─────────────────────────────────────────┤
  │ 👉 Swipe right       │ Skip track                              │
  ├──────────────────────┼─────────────────────────────────────────┤
  │ 👈 Swipe left        │ Previous track                          │
  ├──────────────────────┼─────────────────────────────────────────┤
  │ 👎 Thumbs down, hold │ Boss key — hides all windows            │
  │  1.5s                │                                         │
  ├──────────────────────┼─────────────────────────────────────────┤
  │ ✌️  V sign, hold 2s   │ Screen lock                            │
  └──────────────────────┴─────────────────────────────────────────┘