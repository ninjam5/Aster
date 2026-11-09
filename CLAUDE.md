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
- `llama-server` (**BeeLlama** — `E:\Models\beellama-v0.4.7-bin-win-cuda-12.4-x64\llama-server.exe`, a llama.cpp fork with KVarN KV quantization) at `http://localhost:8080`, serving **Qwen 3.6 35B-A3B** (`E:\Models\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`, IQ4_XS MoE, ~3B active params/token) **plus** `E:\Models\Qwen3.6-35B-A3B-mmproj-F16.gguf` for vision (**served on the GPU** — image prefill ~269 tok/s vs ~42 tok/s from RAM). **KVarN KV cache** (`--cache-type-k kvarn4 --cache-type-v kvarn2 --kv-tail-tokens 1024`), **60k context** (`--ctx-size 60000`; must match `config.N_CTX`), MTP speculative decoding (`--spec-type draft-mtp`), **thinking OFF** (`--reasoning off`), `--n-cpu-moe 28`, `--threads 8` (P-cores only; E-cores hurt MoE throughput), `--ubatch-size 512`. **Vision-first layout (shipped 2026-09-27, controlled A/B):** mmproj on GPU = text 304 tok/s / image 269 tok/s (clean); mmproj in RAM = text 398 / image 42 — i.e. RAM buys ~1 s on text and costs ~22 s per screenshot. Keep ≥1.5 GB VRAM free: below ~500 MB free, compute buffers starve and prefill collapses (227 → 63 tok/s at 360 MB free). `--ubatch-size 1024` was measured and rejected. **Faster-Whisper is deliberately kept OFF the GPU** — a resident CUDA Whisper eats VRAM and starves llama-server's compute buffers; voice notes use the CPU instance (~5.6 s/note) and calls load CUDA Whisper `small.en` (~550 MB VRAM) for the duration of the call only. Start with `start.bat`; `Start-all.bat` launches llama-server then **polls `/health` (up to 300 s) before starting Aster**, avoiding the boot-time memory race. (The Gemma-era engine is preserved on branch `gemma-4-e4b-lightweight`.)

```powershell
# Start Aster (headless CLI mode — main entry point)
python main.py

# Standalone LiveKit WebRTC voice agent
python livekit_agent.py

# Run the test suites (pytest, mocked LLM — no llama-server needed)
pytest tests/test_migration.py -v   # engine adapter + tool calling + failure guards
pytest tests/ -v                    # full test suite (~900 tests)
pytest tests/test_playbooks.py -v   # UI playbooks + UWP suite
pytest tests/test_locator.py -v     # GUI locator (UIA + Qwen 3.6 vision grounding)
pytest tests/test_dom.py -v         # DOM motor (aria parser, shortlist, send gate)
pytest tests/test_system1.py -v     # System-1 kernel (mocked Laya)
pytest tests/test_console_logger.py -v # Console debug logging (stdout/stderr tee)

# Run a single test
pytest tests/test_migration.py -v -k "test_name_here"

# Rebuild llama-cpp-python with CUDA (unused legacy helper for the old path)
python build_cuda.py
```

The desktop companion UI (`aster-face/`, package name `aster-ui`) is a **Tauri** app — an animated 8-bit
reactor-core "face" with a LiveKit voice call, mute, and a resizable live log strip.
Minimizing the main window pops a small always-on-top circular reactor (the `mini`
window); clicking it restores. It talks to `main.py` via `tools/face_server.py`
(FastAPI on `:8000`). Requires the Rust toolchain + MSVC build tools installed once.
```powershell
cd aster-face
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

**LiveKit devmode subprocess check:** `webrtc_bridge.py` runs `server.run(devmode=False)` (line 1137), preventing worker subprocess forks that duplicate Python+torch. LiveKit is benched by default (`daemons.webrtc_bridge: disabled`), with voice calls defaulting to direct local WebSocket audio (`WS /api/voice/ws` on port 8000).

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
1. Add the schema dict to `ADMIN_TOOLS` list (`core/brain.py:446`)
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
  behavior). `gogi.md` provides a warm, conversational best-friend persona. (The legacy emotion recognition and ambient audio subsystem was removed in commit `f65d3b1`).
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

- **Admin brain** — `process_user_input()`, uses `ADMIN_TOOLS` (67 tools), full conversation history, 15-round loop.
- **Discord brain** — `process_discord_chat()`, per-friend history in `discord_chat_histories` (keyed by lowercase name), separate `DISCORD_CHAT_SYSTEM_PROMPT`, 4 tools (`forward_to_owner`, `save_personal_fact`, `remember_pronoun`, `get_current_track`), 4-round loop.

These histories are fully independent. Discord replies pass through `_enforce_discord_honorific()`, which rewrites "Sir" → "Ma'am" / "Mr." → "Ms." for friends listed in `config.DISCORD_FEMALE_NAMES` (sourced from `self_config.yaml → contacts.female_names`, empty by default — per-install data, not hardcoded). This regex is fragile (can mangle words like "Sirius").

---

## Subsystems

### Interfaces

- **CLI** — input loop on the main thread (`main.py`), headless mode.
- **Telegram C2 bridge** — hardcoded `AUTHORIZED_CHAT_ID`; handles `/status`, `/compact`, `/sentry on|off`, `/stop`, `/diagnostics on|off`, `/screenshot`, `/peek [N]`, `/gpu`, `/gesture on|off`, `/intervention on|off`, `/note <text>`, `/find <thing>` (Assist Mode highlight), voice (Faster-Whisper STT), photos, text. `_dispatch_telegram_response()` intercepts `[NATIVE_AUDIO_PAYLOAD:path]` tags and sends voice notes.
- **Discord** — two subsystems: `tools/discord_listener.py` (discord.py DM listener, 9-contact whitelist) for inbound DMs, while outbound Discord messaging is routed through Desktop GUI Automation (Option B) via `open_application('discord')` and playbooks (`send_discord_message` bot relay is archived in `ADMIN_TOOLS`). Outbound relay intent sends immediately for an exact contact name **or a typo in the addressee position** (`resolve_typo`: `geroge`→george — owner decision 2026-09-29, a clear misspelling must just send); anything fuzzy/semantic, or a name only mentioned in the message body, returns `[CONFIRM REQUIRED]` and sends nothing until `confirm=true` on a later turn. **Conversation-ending ("Aster's self-respect", 2026-09-29)** — spec: [`conversation-ending.md`](conversation-ending.md); policy in `tools/conversations.py` (`Aster_Vault/conversation_state.json`). A Laya-bound gate (`core.brain._discord_hostility`, one `check_state` at margin 0.75, fail-open) counts strikes from a hostile DM; at **3 strikes** (decaying after `strike_window_minutes`) Aster **ends the conversation** — a canned cold FINAL line, then a temporary mute (`mute_minutes`, default 60). While muted an inbound DM gets a **code-only countdown** (no LLM) and is logged to a **permanent record** (never forwarded). Re-mutes mention "for the {n}th time". Owner override `/unmute <name>`; record recall via `/discordlog [name]` and the `get_discord_incidents` admin tool. Kill switch `discord_conversation.enabled`.
- **Voice call bridge** (`webrtc_bridge.py` + `tools/face_server.py`) — **Mainstream transport is Direct Local WebSocket (`WS /api/voice/ws` on `:8000` via `LocalWebSocketVoiceBridge`)**; LiveKit Agents SDK (`ManualWebRTCBridge`) has `devmode=False` (no multi-GB subprocess fork) and is **benched by default** (`daemons.webrtc_bridge: disabled`, `voice.transport: local_ws`) for future mobile app use. Pipeline: 16 kHz PCM stream → Silero VAD → Faster-Whisper STT (`small.en`) → brain → Kokoro TTS (24 kHz PCM stream). Supports barge-in (`{"type": "clear_audio"}` flushes browser audio buffers immediately) and epoch-based stale-response cancellation. **Chunk-streaming TTS** (`runtime.tts_chunk_streaming`, default on): `local_tts.py` pushes each Kokoro segment's audio into the call as it is synthesized (first-chunk latency logged as `[Aster Perf] First audio chunk in Xms`) instead of concatenating the whole reply first; a stop-event halts synthesis at the next segment on barge-in. STT and TTS are **shared process-wide**: one CUDA Faster-Whisper `small.en` instance for calls (`local_stt.get_whisper_model()` — Telegram voice notes reuse it when a call is live, otherwise `local_stt.transcribe_file` uses a lazy **CPU** instance so voice notes cost zero VRAM) and one ref-counted Kokoro pipeline — no per-call duplicate model loads. **Cloud voice providers (opt-in, `voice:` in `self_config.yaml`):** `voice.stt_provider` / `voice.tts_provider` may be `local` (default, the above) or `deepgram` — cloud **Deepgram Flux** STT (`deepgram.STTv2`, `flux-general-en`, `/v2/listen`) + **Flux TTS** voice (`flux-colin-en` = British "Colin") via the hand-rolled `deepgram_tts.py` adapter (`/v2/speak`; the installed `livekit-plugins-deepgram` 1.5.7 predates Flux TTS and upgrading it would drag `livekit-agents` off 1.5.x). Resolution is **fail-closed** — a missing `deepgram.api_key` in `secrets.yaml` silently keeps the local engine. Cloud mode sends call audio to Deepgram and drops the on-call `[Speaker:]` voice-ID tag and the cloned Kokoro voice; Telegram voice notes and the CLI always stay local. See `engine_testing/qa/voice-cloud-swap.md`. Module-level `_active_bridge`/`_active_loop` expose `call_is_active()` and `speak_intervention(text)` so other threads can push an unprompted turn into a live call (used by Intervention Mode and Awareness).

### Vision & UI Automation (`tools/vision.py`)

- **`look_at_screen`** — `mss` screen capture → Base64 JPEG → the vision model describes the screen (temp 0.2).
- **`locate_ui_element_ex(goal)`** — core of `smart_click` / `smart_type`. Streamlined pipeline returning `{"x","y","source","text","score",...}` in logical pixels (`locate_ui_element` is the `(x, y)` back-compat wrapper). PyTesseract OCR and OmniParser v2 YOLOv8 + crop captioning were completely stripped in favor of UIA + native Qwen 3.6 vision grounding:
  - **Track -1 — DOM motor (opt-in):** `tools/dom.py` Playwright ARIA / UIA shortlist when enabled (`config.USE_DOM_MOTOR`).
  - **Track 0 — Windows UI Automation accessibility tree (`tools/uia.py`, primary, ~0.1–2s, zero GPU):** walks the foreground window + taskbar via the `uiautomation` package, matching exact element Names/rects from the OS. Threshold 0.70. Chromium/Electron apps enable accessibility lazily — the walk itself wakes it; a sparse first walk (<12 named controls) triggers one retry after 0.8s. Never raises: any failure falls through to Track 1.
  - **Track 1 — Direct Qwen 3.6 Vision Grounding via `_qwen_vision_locate` (~0.5–2s):** sends the full screenshot directly to llama-server via native multimodal chat completions, asking the model for normalized `0..1000` coordinates of the target element, which are parsed and scaled into logical desktop pixels. Zero extra VRAM, instant native vision inference, eliminates brittle OCR upscaling and heavy YOLO weights.
  - Coordinates are DPI-corrected: UIA rects scale by `logical / physical-desktop-rect`; Qwen vision grounding scales `0..1000` normalized coordinates to logical desktop dimensions.
- **Deterministic action verification** (`core/brain.py` dispatch + vision helpers): every `smart_click` diffs a small pre/post grayscale frame (`capture_screen_small_gray` + blockwise `screens_differ`) and warns the model when the screen did not change; every GUI result carries the match provenance (source/text/score, low-confidence flagged) and the current foreground window title (`tools.uia.get_foreground_window_title`). `smart_type` checks keyboard focus via UIA before Ctrl+A: form fields get select-all, `DocumentControl` gets append-only (never wipes a document), anything else returns `FAILED` without typing. All locate misses return `FAILED — ...` so the failure-blind-claim guard sees them. The ReAct loop warns from the 3rd identical `smart_click` goal per turn.
- **`verify_action_result(goal)`** — post-click model free-text check (`n_predict=80`); not called by the dispatch path (per-click verification is the deterministic diff above).
- **Eval harness** — `engine_testing/locator_eval.py`: `capture <name>` saves labeled screenshots, `run [--captions]` scores locator offline on `locator_cases/cases.json`, `live "<goal>"` runs the full locate against the live screen (moves mouse, never clicks). Locator-tuning decisions are made here with data.
- **DOM-first automation motor (opt-in, Stages 0–3):** `tools/dom.py` builds a role/name/bbox shortlist from Playwright's ARIA snapshot (web, attached over CDP when `config.USE_DOM_MOTOR` is on; only the focused page is actionable) or one shared UIA walk (Windows), ranks it with `locator_common` scoring, and executes via Playwright/UIA. Send/submit/destructive web clicks are refused under `config.DOM_MOTOR_SEND_POLICY` (`confirm` default) until `smart_click(..., confirm_send: true)`. `core/system1.py` (Laya, lazy+ref-counted, CPU) optionally picks among ambiguous candidates and answers neutral-key yes/no gates, escalating to the normal path on low margin (`config.USE_LAYA_KERNEL`). Its public decisions are `pick_element` (DOM shortlist), `pick_operation`, `check_state` (binary), plus the generic integration points added 2026-09-28: **`choose(question, criteria)`** (any bounded option set; dict or list of labels), **`score_candidates(levels, instruction, candidates)`** (ordinal scoring — Laya's `score` is the *expected level index* over an ordered worst→best list, so ranking N candidates = N batched questions in one pass), and **`ask_batch(questions)`** (independent gates, one forward pass). Every one returns an `escalate` verdict the caller must honor. **Laya costs ~2.2 GB RSS resident and 34.6 s to cold-load** (0.31 s per predict), so per-turn use needs `laya_keep_resident: true`; its confidence is **uncalibrated for choice questions with 11+ options** (the checkpoint ships invalid temperatures). Full use-case plan: root [`laya-integration.md`](laya-integration.md). **Laya is also wired into six live gates (Phase 1, 2026-09-28):** the memory-consolidation call now sends only the `memorize_fact` schema (was all 69 tools) and is skipped entirely by a conservative `_session_may_hold_new_facts` pre-filter (`/memorize` passes `force=True`); `smart_click`/`smart_type`/`type_text`/`press_key`/`smart_scroll` attach the post-action screenshot only when `_needs_action_screenshot` says the report is not trustworthy enough to proceed; `look_at_screen` answers from window/UIA text when `_screen_answerable_from_text` is confident (any appearance word forces vision); `tools/dom.py::is_send_like` keeps the word set and adds a Laya paraphrase net that **fails closed**; and `research()` picks the Wikipedia article with `_laya_pick_article` and labels/re-routes results with the scored `_answerability` gate. Every one of these falls back to its previous behavior when the kernel is off, escalates, or fails. **Phase 2 (memory writes):** `core.memory.memorize_fact` now runs a Laya write gate at the single choke point every writer uses — it SKIPS a write only when confident the fact is **junk** (an action log / system event) or a **duplicate/paraphrase**, using `min_margin=0.5`; anything uncertain is written. Contradiction/supersede detection was **measured and deliberately NOT shipped** (Laya's margins were 0.00–0.28 — inert), and its plumbing is kept as a future seam. `memorize_fact` doc ids are now millisecond+hash (they collided at second granularity and ChromaDB dropped them silently). **Contact routing (ID 15, owner-required):** `tools/discord_api.py` resolves outbound targets in three steps — an **exact** case-insensitive contact name sends immediately (this fixed the live `'Adham'` bug: the JSON key is capitalised but every lookup lowercased the input first, so that contact was unreachable), a **typo** goes to `difflib` (Laya's margins on typos measured 0.15–0.37 and it guessed "tiger" for "my brother"), and a **semantic/nickname** reference goes to Laya (`laya_pick_contact`, candidates as criteria). Anything that is not an exact name is a **guess** and `send_discord_message(..., confirm=true)` is required — the first call returns `[CONFIRM REQUIRED]` and sends nothing; the relay directive asks the owner first. `_laya_relay_intent` is a conservative Part-C gate that recognises phrasings the verb regex misses ("let George know…"), after a cheap pre-filter and only when confident. **People record (ID 13/14, 2026-09-28):** `tools/people.py` owns `Aster_Vault/people.json` — a structured per-person record (`pronoun`, `gender`, `honorific`, `relationship`, `asked`, `source`). It did not exist before: contacts were a name→Discord-ID map plus a flat `config.DISCORD_FEMALE_NAMES` set, so a pronoun could not be *stored*. `_discord_honorific` now reads it (config set stays as the fallback); the Discord flow resolves a sender's pronouns as stored → one Laya `{male, female, unknown}` guess → **ask the person once** (recorded so it never nags), and the new **`remember_pronoun(name, pronoun)`** tool persists the answer (registered in both ADMIN_TOOLS and DISCORD_TOOLS — 67 admin tools, 4 discord tools). The Discord prompt's hardcoded "his friend" is now a `{relationship}` placeholder resolved the same way. **Phase 3 also:** `get_user_facts(name, query=…)` injects only the friend-facts relevant to the incoming message (ID 16), and `recall_memory` retrieves 10 and reranks to the best 3 (ID 12). All of these return the previous behaviour exactly on kernel-off/escalate/failure. **Phase 4 (live call + daemons, 2026-09-28):** `webrtc_bridge._addressed_to_aster` stops a woken call from turning every stray transcript (side conversation, TV) into a 15-round turn — word-bounded "aster" shortcut, else a 2-way gate at margin 0.6, run via `asyncio.to_thread` so it never blocks the RTC loop; `awareness._good_moment_to_speak` gates proactive nudges (and the intervention daemon's separate delivery path) against speaking into a call or interrupting a mid-turn brain; `_brief_scene_describe` derives the screen field from window/UIA text and drops the screen image (webcam frame stays); and Sentry classifies people from `face_recognition` match text before falling back to the vision call. `agent.predict` is now serialized under a lock (four threads share the resident model). **A five-agent QA review (2026-09-28) audited phases 0–4 + ID 8 and found/fixed, among others:** a contact confirm-gate bypass (a name anywhere in a sentence counted as *exact*, so a guess could be DMed without confirmation — now whole-token and position-aware); `remember_pronoun` being registered but rejected by the Discord tool whitelist (the friend half of the pronoun flow was dead); `sync_unsynced_facts` marking a gate *skip* as synced (permanent silent fact loss); the junk filter being owner-scoped while Discord routes friend facts through it; the Laya Wikipedia pick bypassing the lexical relevance gate; Sentry checking the owner by bare substring before `KNOWN:`; the send gate inheriting the loosest threshold (0.25); and two score consumers ignoring `escalate`. Details + the deliberately-unfixed list: [`laya-integration-testing.md`](laya-integration-testing.md) §8. **Laya Backwards Integration & Loop Hardening (2026-11-04, branch `laya-backwards-integration`, spec [`laya-backwards-integration.md`](laya-backwards-integration.md)):** stripped Laya from perception/co-pilot backseating while keeping defensive choke-point gates — decommissioned Round-0 `pick_tool_for_request` hint injection (ID 8), restored unconditional post-action screenshots in `_attach_action_screenshot` (ID 1) and real vision pixels in `look_at_screen` (ID 2), added `RESERVED_VERBS` blacklist (`email`, `send`, `accountant`, etc.) in `tools/discord_api.py` so `"email"` never fuzzy-matches `"emily"`, changed duplicate memory skips in `core/memory.py` to return affirmative `[Memory Confirmed: ...]`, streamlined `jarvis.md` + `_shared_tool_laws.md` by 57% with an explicit **Clarification Gate**, contextualized retry guards (`tools_executed` on `_claims_tool_execution`, `research_executed` on `_claims_knowledge_cutoff`, `_tool_result_is_safety_gate` on `failure_retried`, `play_spotify_playlist`/`play_liked_songs` in `_TERMINAL_TOOLS`), preserved the KV prefix cache in `_prefix_preserving_summarizer`, enabled parallel non-GUI tool execution in a single round, and added search query-permutation + search-family budgets to `core/loop_guard.py`. Both flags default OFF; stage plan + QA gates live in `engine_testing/qa/`. **`browse_web(url | site+query)`** opens a page in that browser (launching a real Chrome/Edge when CDP attach isn't available) and returns its visible text + HTTP status — the tool for live prices/pages; read-only (`config.WEB_BROWSE_ENABLED`, default on). **Called with no arguments it reads the current page** without navigating, so the model can interact then review: `browse_web(site='linkedin')` → `smart_type('the search bar', '<name>', submit=true)` → `browse_web()` → `smart_click('<profile>')`. Known sites map to their own search (`linkedin` → people search, `amazon.<tld>` → `/s?k=`, `youtube`, `wikipedia`, `google`); no query → the site home page. **`automation.human_search: true`** (set on this install) makes `site`+`query` land on the site's HOME page so the model drives the site's own search bar like a human (`browse_web(site=…)` → `smart_type('the search bar', q, submit=true)` → `browse_web()`); the mapped URLs are the rollback path (flag off), and unknown bare names still search rather than guess a dead host. The owner's real browser profile can NEVER be driven (settled 2026-09-26: Chrome ≥136 ignores the debug port on the default user-data-dir, and any copy or junction of it loses the app-bound encrypted logins — Chrome purges them, so it must never be attempted); `automation.use_real_profile: true` makes `browse_web` refuse loudly (`RealProfileUnavailable`, no silent logged-out guest) with the supported alternative: **`python login_once.py <site>`** (e.g. `linkedin`) opens the login page in Aster's dedicated persistent profile once — the session persists and every later `browse_web` browses that site logged in (`login_once.py --status` lists saved sessions). Aster's own browser launches with `--disable-background-mode` and detach kills it by `--user-data-dir` match, so closed sessions never leave port-squatting background processes.
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

### GUI Automation (active)

`smart_click`, `smart_type`, `smart_scroll`, `press_key`, `get_ui_playbook` are registered in `ADMIN_TOOLS` and dispatched in `execute_tool()` — `pyautogui` calls live directly in `brain.py`'s dispatch, on top of the three-track locator + deterministic verification described under **Vision & UI Automation** above (UIA tree → Direct Qwen 3.6 Vision Grounding; pre/post screen diff, foreground-title ground truth, UIA focus check before Ctrl+A, `FAILED —` results on every miss, repeat-click warning from the 3rd identical goal per turn). **`press_key` supports both single keys (`enter`, `tab`, `escape`, `space`, `f`, `k`, `/`) and hotkey combinations joined with `+` (`ctrl+t`, `ctrl+l`, `ctrl+w`, `ctrl+shift+t`, `ctrl+k`, `ctrl+shift+m`, `alt+left`, `win+a`, `ctrl+shift+esc`) via `pyautogui.hotkey(*parts)`.** **Important:** keyboard keys and hotkeys must use `press_key`, NOT `smart_click` (clicking the word "Enter" found by OCR is a known past bug). `tools/gui.py` still has `ui_type`/`ui_press_key` wrappers but the file is **not imported** anywhere.

### Two-Tier UI Playbooks & Gemini 3.8 Flash Self-Healing (`tools/playbooks.py`, `Aster_Vault/playbooks/`)

Persistent, self-learning UI navigation memory (`Aster_Vault/playbooks/<slug>.json`) organized in a **Two-Tier Playbook Architecture** (18 pre-seeded playbooks with bidirectional alias resolution and UWP app support):
- **Tier 1 — Desktop App Playbooks** (`tier: "desktop"`, 13 apps): native/Electron/UWP apps (`chrome`, `discord`, `instagram`, `stremio`, `steam`, `epic_games`, `riot_client`, `league_of_legends`, `spotify`, `telegram`, `vscode`, `file_explorer`, `windows_shell`).
- **Tier 2 — Web Domain Playbooks** (`tier: "web"`, 5 domains): web applications and domains (`web_youtube`, `web_google`, `web_github`, `web_reddit`, `web_gmail`). When a browser window is in focus and its title/description matches a known web domain (e.g. `"YouTube - Google Chrome"`), `resolve_active_playbook()` combines the browser shell shortcuts with the active web domain's shortcuts, tasks, and `AVOID` traps.
- **Automatic Active-Window Hint Injection:** `_get_ui_guide()` (in `look_at_screen`) and `_attach_action_screenshot()` (after every `smart_click`, `smart_type`, `type_text`, `press_key`, `smart_scroll`) call `get_active_playbook_hint()` to inject a compact `[UI Playbook: ...]` block with top shortcuts and `AVOID` rules for the foreground window.
- **`get_ui_playbook(app_or_site, task="", failed_step="")` admin tool:**
  - Looks up the local YAML playbook in `Aster_Vault/playbooks/`.
  - **Dynamic Learning via Gemini 3.8 Flash (`config.GEMINI_MODEL = "gemini-3.8-flash"`):** if the app/site or requested `task` is missing from the local vault (and `config.GEMINI_AVAILABLE` is true via `gemini.api_key` in `secrets.yaml`), queries the Gemini REST API to generate structured `shortcuts`, `tasks`, `landmarks`, and `avoid` rules, then merges and saves them permanently to `Aster_Vault/playbooks/<slug>.json`.
  - **Multimodal Self-Healing (`failed_step`):** when a UI step fails because an app updated its layout or a modal/popup blocked the target, passing `failed_step` captures a live screenshot (`capture_screen_base64()`) and sends it with the existing playbook to `gemini-3.8-flash` to diagnose the failure and patch the JSON playbook on disk.
  - **Fail-closed offline fallback:** if `gemini.api_key` is unset or the API call errors, `get_ui_playbook` degrades gracefully to local playbooks / generic UIA+vision guidance without raising.

### Console Debug Logging (`tools/console_logger.py`)

When `runtime.debug: true` in `self_config.yaml` (or `debug: true` at root), Aster mirrors all terminal stdout and stderr live to date-stamped log files in `Aster_Vault/console-logs/console_YYYY-MM-DD_HH-MM-SS.log`.
- **Thread-Safe & Non-Blocking:** Re-entrant lock (`threading.RLock`) protects concurrent file writes and flushes across background threads.
- **Pristine Output:** Strips terminal ANSI color escape sequences (`\x1b[...]`) so log files remain clean in plain-text editors.
- **Unicode Resilient:** Handles arbitrary UTF-8 characters without encoding crashes on Windows console codepages.
- **Lifecycle Banners:** Prepends session start header (with timestamp and PID) and appends session ended footer on exit.
- **Zero Overhead When Off:** If `debug` is `false` (default), `console_logger.install()` is an immediate no-op, touching neither streams nor creating any log directories.

### Sentry Mode (`tools/sentry.py`)

Webcam intruder detection. `sentry_daemon()` polls every 5s when active: vision-model scene analysis with `face_recognition` fallback. Known-as-"Mohamed" ignored; others alert with 5-min cooldown; unknown faces save `temp_intruder.jpg` and set `WAITING_FOR_ID`. The daemon **auto-start is commented out** in `main.py` (~line 664), but `toggle_sentry_mode` still works.

### Intervention Mode (`tools/intervention.py`)

Proactive focus daemon. `intervention_daemon()` polls the foreground window every `INTERVENTION_CHECK_INTERVAL`s when active, accumulating continuous time on apps whose title matches `config.DISTRACTION_KEYWORDS`. One glance-away poll is tolerated before the accumulator resets. Once `INTERVENTION_THRESHOLD` is crossed (and not in quiet hours / snoozed / within `INTERVENTION_COOLDOWN`), `_trigger_intervention()` flags the window and routes a `[System Internal: …]` nudge through the brain — spoken over the call via `webrtc_bridge.speak_intervention()` if one is live, else a Telegram text + Kokoro voice note. Toggled via `/intervention on|off` or `toggle_intervention_mode`; `close_distraction_window` / `snooze_intervention` handle the accept / push-back replies. **Not** mutually exclusive with Sentry/Gesture (no webcam/GPU use).

### Assist Mode (`tools/assist.py`)

Screen-highlight helper. When the user asks *where* something is on screen,
`locate_ui_elements_boxed(goal)` (`tools/vision.py`) returns **all** matches as
logical-pixel boxes — backed by `_qwen_vision_locate_boxes(goal)` using Qwen 3.6
normalized `box_2d` vision grounding (`[ymin, xmin, ymax, xmax]`), scaled to
logical desktop pixels. `highlight_regions(boxes)` then spawns a
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
- **Ambient nudges** (`tools/awareness.py`) — `_maybe_trigger_email_checkin()` follows the standard gate order (enabled → initiative → condition → dedup → quiet/budget), nudging when unread mail in Primary crosses `EMAIL_CHECKIN_THRESHOLD` (only re-fires as the count grows, not every tick). `_maybe_trigger_reauth_reminder()` nudges once per lapse when `reauth_needed()` is true. Both run from the awareness daemon's fixed periodic trigger sequence.
- **Dashboard** (`Aster-UI`) — `GoogleCard` in the Socials section (`src/sections/GoogleCard.tsx`) shows connection status and a 3-tier picker, backed by `GET/POST /api/google/status|connect|tier` in `tools/face_server.py`. Tier changes persist via `tools/config_writer.set_config_values()`, matching persona/voice — not the ephemeral Skills-toggle pattern, since access level is a deliberate standing choice, not daemon runtime state.
- **Config**: `integrations.google` in `self_config.yaml` (`access_tier`, `autonomous_daily_cap`, `reauth_reminder`, `email_checkin.*`); `google.client_id`/`client_secret` in `secrets.yaml`.

### Other Tools

- **Spotify** (`tools/media.py`) — Spotipy OAuth; `ensure_active_spotify_device()` auto-transfers playback, and when **no device exists at all** (Spotify closed) it self-heals: launches the desktop app via `tools.system.open_application` and polls `sp.devices()` for up to ~25 s until it registers (`_launch_spotify_and_wait`). All Spotify/timer tools return `ToolResult(ok, text)` envelopes (`core/tool_result.py`) — the failure-blind-claim guard reads `.ok` exactly; failure texts keep their LLM-instructive `FAILED` prefix. Unmigrated tools still return plain strings, classified by the legacy FAILED/Error prefix regex in `core.brain._normalize_tool_result` (`runtime.structured_tool_results: false` reverts to regex-everywhere). `execute_tool()` strips envelopes for legacy callers; the ReAct loops use `execute_tool_ex()` → `(ok, payload)`.
- **System** (`tools/system.py`) — Win32 control: `set_system_state` (rundll32 lock/sleep/restart), `set_volume` (pycaw), `open_application` (Start Menu fuzzy match), process list (psutil).
- **Timers/Alarms** (`tools/media.py`) — daemon threads firing `winotify` toasts.
- **RAG** (`tools/rag.py`) — single `research(topic)` tool. **Pass a URL and it reads that exact page** through the Firecrawl API (`scrape_url`, no browser, no RAM) with `browse_web` as the fallback — so "read this link" works even when the browser is RAM-blocked. Otherwise the pipeline is time-aware (changed 2026-09-28): a **current-events topic** (recency words, or any year >= the current year) goes **straight to live web** (Firecrawl → `browse_web` fallback) and never touches Wikipedia or the vault; anything else goes vault cache (90-day staleness) → Wikipedia API → live web. Wikipedia results are only accepted when the article **title** shares a distinctive token with the query (`_wiki_title_relevant`; fuzzy — prefix + typo tolerance). Without that gate the old 0.15-Jaccard matcher answered a Netanyahu/UNGA-2026 question with the article on **Matteo Renzi** and cached it under the question's filename for 90 days. Sources are labelled with the **real article title** (`[Source: Wikipedia | <title>]`, `[Source: Firecrawl | live web]`, `[Source: browse_web | live web]`); scraped pages have their nav/logo block trimmed and start at the article H1; current-events answers are never written to the vault; and when every path fails the model is told the lookup failed and must not guess. `browse_web` itself falls back to `scrape_url` when the browser cannot launch (e.g. the low-RAM guard).
- **Audio** (`tools/audio.py`) — Kokoro TTS 82M. **One shared, ref-counted pipeline** (`acquire_kokoro_pipeline` / `release_kokoro_pipeline`) used by both the `generate_kokoro_voice` tool and the LiveKit call (`local_tts.py`); lazy-loaded on first use, **offloaded from VRAM when no consumer holds it**. Emits `[NATIVE_AUDIO_PAYLOAD:path]`.

---

## Disabled / Archived Code — Do Not Assume It Runs

- **FastAPI dashboard block in `main.py`** — still commented out. The old 3-panel `aster-ui` dashboard was replaced by the Tauri face app. A *separate* minimal FastAPI lives in `tools/face_server.py` (token + logs only) — that one IS active.
- **Sentry daemon auto-start** — commented out (`main.py` ~359).
- **Removed OCR & YOLO locator pipelines** — PyTesseract OCR, OmniParser v2 YOLOv8 (`yolov8n.pt`, `_yolo_track`, `_caption_crop`), `USE_PIXEL_FALLBACK`, and `ICON_CAPTIONER` were completely removed in favor of UIA + Direct Qwen 3.6 Vision Grounding (`_qwen_vision_locate`).
- **`tools/gui.py`** — present but not imported.
- **`tools/web.py`** — file does not exist; live research uses `research()` in `tools/rag.py` and `browse_web()` in `tools/dom.py`.
- **`send_discord_message`** — commented out in `ADMIN_TOOLS` in favor of Desktop GUI Automation (Option B).
- **Emotion Recognition & Ambient Audio** — completely removed in commit `f65d3b1` (`tools/emotion_recognition.py`, `tools/ambient_audio.py`, `tools/mood_actions.py`, `tools/mood_memory.py`).
- **`whisper-models/models--Systran--faster-distil-whisper-large-v3/`** and **`whisper-models/medium.en/`** — present on disk but not loaded by default; `small.en` (`voice.whisper_model: small.en`) is now the active STT model.
- **Native audio input (`input_audio`) — removed in the Qwen swap.** Gemma's audio head is gone; voice notes now go through Faster-Whisper (`local_stt.transcribe_file`) and enter the brain as text.

---

## Tool Registry (67 admin tools registered at boot)

Categories: System (7), File I/O (3), Spotify (9), Timers (2), Memory (3), Vision (4), GUI Automation (7), RAG (1), Web (1), Audio (1), Notes (2), Awareness/Initiative (2), Intervention (3), Sentry/Gesture (2), Self-knowledge (6), Diagnostics (1), Discord (1), People (1), Gmail (7), Calendar (4) = 67 tools.

To regenerate full list: `python -c "import core.brain as b; [print(t['function']['name']) for t in b.ADMIN_TOOLS]"`. Full per-tool table in `summary.md` Appendix A.

Discord brain has only 4 tools: `forward_to_owner`, `save_personal_fact`, `remember_pronoun`, `get_current_track`.

---

## Thread Architecture

Main thread runs the CLI loop. Daemon threads: Telegram `infinity_polling`, Discord DM listener, Gesture daemon, Intervention daemon (idle until `/intervention on`), Awareness daemon, Face server (`tools/face_server.py`, uvicorn on `:8000`, also hosts `WS /api/voice/ws`), Health watchdog (`tools/health_watchdog.py` — pings llama-server `/health` every 30s; 3 consecutive failures → Telegram alert + capped auto-relaunch of `start.bat`; 503 = model loading, never restarts into it; flags `daemons.health_watchdog` / `health_watchdog_autorestart`) — all active; Sentry daemon (disabled); Screen watcher (on-demand, 1-hour timeout).

---

## Known Gotchas

- **Credentials live in `secrets.yaml`, not `config.py`.** Spotify, Telegram, Discord, LiveKit, Deepgram, and Firecrawl credentials are loaded via `config._secret()` from `secrets.yaml` (gitignored, per-install — see `secrets.example.yaml` for the template). `config.py` no longer contains any real credential; do not add new ones there or to any tracked file. `self_config.yaml` (identity/contacts — also gitignored, see `self_config.example.yaml`) and `secrets.yaml` can both be populated by running `python first_run_setup.py`. Every integration is optional — a blank/missing credential disables that integration gracefully (`SPOTIFY_AVAILABLE` / `TELEGRAM_AVAILABLE` / `LIVEKIT_CONFIGURED` / `DEEPGRAM_AVAILABLE` / Discord's own empty-token check) rather than crashing.
- **`uiautomation` package required for locator Track 0** — `python -m pip install uiautomation`. Without it `tools/uia.py` degrades gracefully (every locate falls through to Track 1 Qwen vision grounding), but GUI automation requires an LLM vision call. UIA cannot read elevated (admin) windows from Aster's non-elevated process, and the secure desktop (UAC prompts / lock screen) is off-limits entirely.
- **Vault path casing is fixed** — all `Aster_vault` (lowercase `v`) literals were replaced with `config.VAULT_DIR` / `config.FACES_DIR` (2026-07-12); the faces dir is `paths.faces_dir` (default `Aster_Vault/Faces`). Use these constants for any new vault path — never a hardcoded string.
- **`build_cuda.py`** — legacy; the engine migrated from `llama-cpp-python` to a standalone `llama-server` binary.
- **Firecrawl key required for live web search** — `research()` reaches Firecrawl (live web) for current-events topics, to read a specific URL (`scrape_url`), and as the fallback when Wikipedia has no relevant article; if it fails, `browse_web` (Aster's own Chrome) is the last resort. `FIRECRAWL_API_KEY` is read from `secrets.yaml` (`firecrawl.api_key`) via `config._secret()`; when it is unset the tool degrades to Wikipedia-only and reports that live lookup failed. Free tier: 500 credits/month; 3 credits per search invocation, 1 per page scrape. **The SDK is firecrawl 4.x — `SearchData.web/.news` with `Document.markdown` + `metadata.title/url` for search, `app.scrape(url, formats=["markdown"])` for a page; the old v1 `result.data` access raised AttributeError and silently disabled live search.**
- **`wikipedia` package required** — `python -m pip install wikipedia`. The `research` tool fails gracefully without it but Wikipedia lookup will always fall through to Firecrawl.
- **LiveKit Agents devmode subprocess** — `webrtc_bridge.py` calls `server.run(devmode=True)`. Devmode may fork a worker subprocess that duplicates Python+torch, adding ~3-5 GB RAM. To check: after a call connects, run `Get-Process python | Format-Table Id, WorkingSet64` — two python.exe entries with multi-GB footprints confirms the subprocess. In `webrtc_bridge.py:1137`, `devmode=False` is set by default to prevent supervisor/worker multi-GB process forks.
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
| `tools/vision.py` | Screen/webcam capture, face recognition, `locate_ui_element_ex` (Track 0 UIA via `tools/uia.py` → Track 1 Direct Qwen 3.6 Vision Grounding via `_qwen_vision_locate`), `locate_ui_elements_boxed` (`box_2d` grounding), `screens_differ` action diffing, cold storage |
| `tools/uia.py` | Locator Track 0 — Windows UI Automation accessibility tree (`uia_locate`, Chromium warm-up retry); `get_foreground_window_title`, `focused_control_type` (smart_type Ctrl+A safety) |
| `tools/locator_common.py` | Shared goal parsing/scoring for all locator tracks: `parse_goal` (filler stripping, spatial + control-type hints), `score_text`, `spatial_weight` |
| `engine_testing/locator_eval.py` | Locator eval harness: `capture` / `run [--captions]` / `live "<goal>"` — decides captioner/threshold questions with data on real screenshots |
| `tools/voice_recognition.py` | SpeechBrain ECAPA speaker recognition (CUDA, eager-preloaded); per-speaker subfolders under `Voices/`; centroid-averaged embeddings; `load_known_voices()`, `identify_speaker()`, `identify_and_maybe_learn()`, `accumulate_sample()` — auto-accumulates high-confidence utterances (cap 15/speaker) |
| `tools/system.py` `tools/media.py` | OS control / Spotify + timers |
| `tools/sentry.py` | Webcam intruder daemon |
| `tools/intervention.py` | Intervention Mode — foreground-window focus daemon; `classify_window()`, toggle/snooze/close, brain-routed in-persona delivery |
| `tools/assist.py` | Assist Mode — stdlib-tkinter dim overlay (`highlight_regions()`) that highlights located screen elements; backed by `locate_ui_elements_boxed()` (`_qwen_vision_locate_boxes`) in `tools/vision.py` |
| `tools/playbooks.py` / `Aster_Vault/playbooks/*.yaml` | Two-Tier UI Playbook engine (Desktop Apps + Web Domains), active-window `[UI Playbook: ...]` hint formatter, `get_ui_playbook` tool, and Gemini 3.8 Flash (`gemini-3.8-flash`) dynamic playbook learning & multimodal self-healing |
| `tools/discord_listener.py` `tools/discord_api.py` | Discord inbound / outbound |
| `tools/google_auth.py` | Shared Gmail + Calendar OAuth/token management, tier gating (`tier_allows`, `send_policy`), shared pending-approval slot |
| `tools/gmail_tool.py` | Gmail read/modify/reply tools; draft-then-send with autonomous-tier guardrail |
| `tools/google_calendar.py` | Calendar read/modify/invite tools; guest-affecting actions share Gmail's send policy |
| `tools/rag.py` `tools/audio.py` `tools/memory_manager.py` | Web research / Kokoro TTS / Discord fact staging |
| `tools/diagnostics.py` | `sys.stdout` shim → Telegram forwarder; `/diagnostics on\|off` toggle; `send_error()` / `send_alert()` relays; also feeds the desktop UI log strip |
| `tools/face_server.py` | FastAPI on `:8000` — backend API suite: `WS /api/voice/ws` (direct local audio), `GET /api/token` (LiveKit JWT), `WS /api/logs`, `/api/chat/stream`, `/api/memory/*`, `/api/notes/*`, `/api/skills/*`, `/api/google/*`; powers the Tauri desktop companion and web dashboard |
| `tools/realtime_stream.py` | WS broadcast queue (`publish_terminal` / `publish_wake` / `publish_sentiment` / `stream_dispatch_loop`) feeding the desktop UI |
| `tools/sentiment.py` | Keyword-only `classify_sentiment()` — maps Aster's final response to a reactor-face mood (`calm`/`working`/`alert`/`music`/`success`); no LLM call |
| `aster-face/` | (formerly `aster-ui/`) Tauri desktop companion — 8-bit reactor-core face, LiveKit voice call, mute, resizable log strip, minimize→mini-circle window. Face colour reacts to wake state (`useWakeState`) and conversational mood (`useSentiment`, fed by `{type:"sentiment"}` events) |
| `Aster-UI/` | Full dashboard Tauri app — eight section panels (Activity Log, Dashboard, Memory CRUD, Skills toggles, VoiceChat SSE stream, Notes, Socials status). API layer in `Aster-UI/src/api/`. Aster character companion with two renderers: 8-bit pixel canvas (default, `src/character/pixel/`, choreographed mood transitions) and the original cartoon puppet — switch in Settings → Appearance. Connects to `tools/face_server.py` on `:8000`. 57 vitest tests, build+lint clean. |
| `tools/gesture.py` | MediaPipe Hands gesture daemon; 7 Tier-1 gestures (volume/pause/skip/boss/lock); mutually exclusive with Sentry |
| `webrtc_bridge.py` `local_stt.py` `local_tts.py` | LiveKit voice bridge + STT/TTS adapters |
| `tests/test_migration.py` | PyTest suite (mocked LLM) |
| `tests/test_locator.py` | PyTest suite for GUI locator (UIA + Direct Qwen 3.6 Vision Grounding; mocked LLM, no llama-server) |
