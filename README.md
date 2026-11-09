# Aster

**A fully local, always-on AI companion — voice, vision, memory, and 67 autonomous tools, running entirely on a single consumer GPU with no data ever leaving the machine.**

Aster is a personal AI agent I designed and built from scratch: a local LLM (Qwen 3.6 35B-A3B via `llama.cpp`) wired into a real agentic loop with native function-calling, long-term memory, multimodal perception (screen, webcam, voice), and interfaces across CLI, Telegram, Discord, a live local WebSocket / WebRTC voice call, and two custom desktop apps. Nothing is routed through a third-party API — inference, speech, vision, and memory all run on-device.

I'm submitting Aster as my project for the **Claude Builder / Life Sciences Program** application. It isn't a life-sciences tool itself, but it's the clearest evidence I have of the skills that track cares about: building *reliable* agentic systems on top of an LLM — tool orchestration, multimodal reasoning, long-horizon memory, and privacy-preserving architecture — all of which transfer directly to building trustworthy AI agents for sensitive, high-stakes domains like healthcare and life sciences, where **data never leaving a controlled environment** and **auditable, gated tool use** aren't nice-to-haves, they're the whole point.

---

## Why this project, for this program

| What Life Sciences agent work demands | What Aster demonstrates |
|---|---|
| Data must stay local / auditable, never silently leave the environment | Every core model (LLM, STT, TTS, vision, speaker/face ID) runs on-device; nothing is sent to a third-party API |
| Agents must call tools safely, not hallucinate actions | A native OpenAI-style function-calling loop with a dedicated hallucination detector that catches the model *claiming* to have acted without an actual tool call, and retries once |
| Multi-signal reasoning (not just text) | Fuses desktop UIA accessibility trees, direct native Qwen 3.6 vision grounding, webcam face ID, and voice biometrics into one context before the LLM ever sees a turn |
| Long-horizon state, not just a single prompt | A dual-write memory system (vector DB + append-only log) with dedup, sliding-window context trimming, atomic tool-pair popping, and automatic compaction above 75% context threshold |
| Systems must degrade gracefully, not crash | Every optional integration (Spotify, Gmail, Discord, LiveKit, Google Calendar) is gated behind a config flag and fails closed to a no-op rather than an exception |
| Human-in-the-loop approval for consequential actions | A shared pending-approval gate in front of anything irreversible (sending an email, responding to a calendar invite, multi-line text paste) — nothing autonomous fires without an explicit tier the owner chose |

---

## What it actually does

- **Talks and listens** — a direct local WebSocket audio bridge (`WS /api/voice/ws`) and benched LiveKit WebRTC call with wake-word activation, barge-in, and sub-second local STT/TTS (Faster-Whisper `small.en` + Kokoro 82M), plus a Telegram bridge for text, voice notes, and photos.
- **Sees** — reads the desktop screen via Windows UI Automation (Track 0) and Direct Qwen 3.6 Vision Grounding (Track 1) to click/type/scroll on command, describes webcam frames, and recognizes known faces.
- **Remembers** — a hybrid ChromaDB + markdown memory pipeline that auto-consolidates facts every few turns, survives crashes, and recalls historical conversations with exact token budgeting.
- **Navigates UIs with memory** — a Two-Tier UI Playbook engine (18 pre-seeded JSON playbooks across desktop apps and web domains) with bidirectional alias resolution and Gemini 3.8 Flash dynamic learning and multimodal self-healing.
- **Acts autonomously, safely** — 67 tools spanning system control, Spotify, Gmail/Calendar, screen automation, live web browsing (it opens sites and drives their own search bars like a human), and note-taking, all behind a single dispatch point with tiered permissions (read-only → ask-first → autonomous-with-guardrails).
- **Notices things unprompted** — a background awareness daemon watches for prolonged distraction or unread mail and proactively nudges the owner — rate-limited and quiet-hours-aware so it never becomes noise.

## Architecture

```
User input (CLI / Telegram / Local WS / WebRTC voice)
  → process_user_input()
    → trim_memory()                 sliding-window context management (atomic tool-pair preservation)
    → ambient context tagging       clock + active foreground window + system state
    → LLM completion                POST /v1/chat/completions (native tool schema)
    → tool_calls extraction         structured, not text-parsed
    → execute_tool()                dispatch to one of 67 tools
    → loop up to 15 rounds, with a hallucination-retry safety net
    → response                      routed back to whichever interface asked
```

A single `llama-server` instance (Qwen 3.6 35B-A3B MoE, IQ4_XS, 60k context, KVarN-quantized KV cache, MTP speculative decoding) serves **every** text, vision, and tool-calling request across every interface — there's no separate vision pipeline or duplicate model load. The tool-calling path is the OpenAI-compatible native `tools` schema, which is why the whole system is built around a single dispatch abstraction.

### Interfaces, all backed by the same brain

| Surface | Notes |
|---|---|
| CLI | Headless main loop, primary dev interface |
| Telegram | Full remote control — status, screenshots, GPU telemetry, voice notes, mode toggles |
| Discord | Separate sandboxed conversation context per contact, 4-tool allowlist (`forward_to_owner`, `save_personal_fact`, `remember_pronoun`, `get_current_track`), no shared memory with the admin brain |
| Direct Local Voice Call | Local WebSocket (`WS /api/voice/ws` on `:8000`), wake-word gated, barge-in capable with immediate audio buffer flush |
| WebRTC voice call | LiveKit Agents SDK (`ManualWebRTCBridge` with `devmode=False`, benched by default for future mobile use) |
| Desktop companion (`aster-face/`) | Tauri app — animated reactor-core face reacting to wake state and mood, live voice call, minimizable to a small always-on-top widget |
| Full dashboard (`Aster-UI/`) | Tauri app — memory CRUD, skills toggles, activity log, connected-services status, voice call UI |

## Engineering choices worth noting

- **One LLM adapter function**, `_execute_llm_completion()`, is the single place every caller (chat, vision, sentry, persona generation) goes through — every non-tool-calling caller has to explicitly unwrap the same dict shape, which was a deliberate choice to keep one calling convention instead of two.
- **Modular persona system** — the entire personality (tone, response length) lives in swappable markdown files (`jarvis.md`, `gogi.md`), selected by one config key, with zero code branching on "which persona."
- **Ref-counted, lazy-loaded models** — Kokoro TTS and CUDA Faster-Whisper are acquired/released like a semaphore so VRAM is only paid for while something is actually using them, instead of holding every model resident all the time on a 12GB card.
- **Zero VRAM for Telegram voice notes** — voice notes use a lazy CPU instance of Faster-Whisper `small.en`, preserving all GPU compute buffers for llama-server.
- **Console Debug Logging** — when `runtime.debug: true`, `tools/console_logger.py` mirrors all stdout/stderr live to date-stamped files in `Aster_Vault/console-logs/` with ANSI color escape stripping and thread-safe file tees.
- **Everything optional fails closed** — every third-party integration (Spotify, Gmail, Discord, Firecrawl, Deepgram) checks for its credential at boot and disables itself gracefully rather than crashing the whole system when a key is missing.

## Tech stack

| Layer | Choice |
|---|---|
| LLM inference | `llama-server` (llama.cpp; BeeLlama v0.4.7 fork), Qwen 3.6 35B-A3B IQ4_XS (MoE, ~3B active/token), 60k ctx, KVarN KV cache, MTP speculative decoding |
| STT | Faster-Whisper (`small.en`, CTranslate2, CUDA int8 on calls, CPU on voice notes; optional cloud Deepgram Flux) |
| TTS | Kokoro 82M (single voice pipeline; optional cloud Deepgram Flux TTS `flux-colin-en`) |
| Voice ID | SpeechBrain ECAPA-TDNN speaker embeddings |
| Vision & Locator | Windows UI Automation (Track 0) + Direct Qwen 3.6 Vision Grounding (Track 1) for UI interaction, `face_recognition` for webcam identity |
| Memory | ChromaDB (vector) + markdown (human-readable) dual write with atomic tool-pair pop trimming |
| Voice transport | Direct Local WebSocket (`WS /api/voice/ws`) on port 8000; LiveKit Agents SDK (`devmode=False`) |
| Desktop apps | Tauri 2 + React 19 + TypeScript + Vite + TailwindCSS (`aster-face` and `Aster-UI`) |
| Bridges | Telegram (pyTelegramBotAPI), Discord (discord.py inbound listener; Option B desktop GUI automation for outbound) |

## Repository layout

```
core/            LLM adapter, agentic tool-calling loop, memory read/write, loop guard, system-1 kernel
tools/           67 admin tools: vision, GUI automation, voice ID, Gmail/Calendar, Spotify, system control, RAG, playbooks, console logger...
Aster_Vault/     Per-install runtime data + shippable assets (system prompts, playbooks, images)
aster-face/      Tauri desktop companion — animated reactor-core face + live call
Aster-UI/        Tauri full dashboard — memory CRUD, skills toggles, activity log, voice chat
tests/           pytest suite (~900 tests, mocked LLM, no server required)
engine_testing/  Standalone native tool-calling harness (52 scenarios) + QA gates
main.py          Entry point — CLI loop + Telegram/Discord/WebRTC daemon threads
webrtc_bridge.py Standalone LiveKit voice agent and local WebSocket audio bridge
```

## Status

This is a live, actively-used personal system (not a demo repo) — I run it daily as my own assistant. Model weights, voice enrollments, and personal config are gitignored; see `secrets.example.yaml` and `self_config.example.yaml` for the setup shape. A from-scratch install additionally requires a running `llama-server` instance serving Qwen 3.6 35B-A3B + its multimodal projector — see `first_run_setup.py` for the guided setup wizard.

## Quickstart

```powershell
# 1. Python deps (global pip — no venvs)
python -m pip install -r requirements.txt   # or install per-import as you hit them

# 2. Secrets + personal config (never committed; .example files show the shape)
Copy-Item secrets.example.yaml secrets.yaml       # fill in your tokens
Copy-Item self_config.example.yaml self_config.yaml

# 3. Start the LLM backend, wait for /health, then Aster + the UIs
.\Start-all.bat        # or: python main.py once llama-server is healthy
```

## License

Aster is licensed under the [Apache License, Version 2.0](LICENSE).

---

*Built solo by Mohamed Saeed.*
