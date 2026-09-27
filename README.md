# Aster

**A fully local, always-on AI companion â€” voice, vision, memory, and 69 autonomous tools, running entirely on a single consumer GPU with no data ever leaving the machine.**

Aster is a personal AI agent I designed and built from scratch: a local LLM (Gemma-4 via `llama.cpp`) wired into a real agentic loop with native function-calling, long-term memory, multimodal perception (screen, webcam, voice), and interfaces across CLI, Telegram, Discord, a live WebRTC voice call, and two custom desktop apps. Nothing is routed through a third-party API â€” inference, speech, vision, and memory all run on-device.

I'm submitting Aster as my project for the **Claude Builder / Life Sciences Program** application. It isn't a life-sciences tool itself, but it's the clearest evidence I have of the skills that track cares about: building *reliable* agentic systems on top of an LLM â€” tool orchestration, multimodal reasoning, long-horizon memory, and privacy-preserving architecture â€” all of which transfer directly to building trustworthy AI agents for sensitive, high-stakes domains like healthcare and life sciences, where **data never leaving a controlled environment** and **auditable, gated tool use** aren't nice-to-haves, they're the whole point.

---

## Why this project, for this program

| What Life Sciences agent work demands | What Aster demonstrates |
|---|---|
| Data must stay local / auditable, never silently leave the environment | Every model (LLM, STT, TTS, vision, speaker/face/emotion ID) runs on-device; nothing is sent to a third-party API |
| Agents must call tools safely, not hallucinate actions | A native OpenAI-style function-calling loop with a dedicated hallucination detector that catches the model *claiming* to have acted without an actual tool call, and retries once |
| Multi-signal reasoning (not just text) | Fuses screen OCR/vision, webcam face ID, voice biometrics, and prosody-based emotion detection into one context before the LLM ever sees a turn |
| Long-horitzon state, not just a single prompt | A dual-write memory system (vector DB + append-only log) with dedup, sliding-window context trimming, and time-gated trend summarization (e.g. mood logged over weeks, not just per-turn) |
| Systems must degrade gracefully, not crash | Every optional integration (Spotify, Gmail, Discord, ambient audio, face emotion) is gated behind a config flag and fails closed to a no-op rather than an exception |
| Human-in-the-loop approval for consequential actions | A shared pending-approval gate in front of anything irreversible (sending an email, responding to a calendar invite) â€” nothing autonomous fires without an explicit tier the owner chose |

---

## What it actually does

- **Talks and listens** â€” a live WebRTC voice call (LiveKit) with wake-word activation, barge-in, and sub-second local STT/TTS, plus a Telegram bridge for text, voice notes, and photos.
- **Sees** â€” reads the screen (OCR + YOLO-based UI element detection) to click/type/scroll on command, describes webcam frames, and recognizes known faces.
- **Remembers** â€” a hybrid ChromaDB + markdown memory pipeline that auto-consolidates facts every few turns, survives crashes, and can recall anything from a conversation held down.
- **Feels the room** â€” a five-tier emotion pipeline (text sentiment, voice prosody, ambient room audio, facial expression) fuses into one mood signal that quietly shapes tone, and is logged over time into a trend summary.
- **Acts autonomously, safely** â€” 69 tools spanning system control, Spotify, Gmail/Calendar, screen automation, live web browsing (it opens sites and drives their own search bars like a human), and note-taking, all behind a single dispatch point with tiered permission (read-only â†’ ask-first â†’ autonomous-with-guardrails).
- **Notices things unprompted** â€” a background awareness daemon watches for prolonged distraction, sustained negative mood, or unread mail and proactively nudges the owner â€” rate-limited and quiet-hours-aware so it never becomes noise.

## Architecture

```
User input (CLI / Telegram / WebRTC voice)
  â†’ process_user_input()
    â†’ trim_memory()                 sliding-window context management
    â†’ mood/context tagging          text + voice + face + ambient fusion
    â†’ LLM completion                POST /v1/chat/completions (native tool schema)
    â†’ tool_calls extraction         structured, not text-parsed
    â†’ execute_tool()                dispatch to one of 69 tools
    â†’ loop up to 15 rounds, with a hallucination-retry safety net
    â†’ response                      routed back to whichever interface asked
```

A single `llama-server` instance (Gemma-4-E4B, Q4_K_M, 128k context, full GPU offload) serves **every** text, vision, and tool-calling request across every interface â€” there's no separate vision pipeline or duplicate model load. The tool-calling path is the OpenAI-compatible native `tools` schema; a full legacy XML-in-text fallback path exists behind one config flag as a rollback, which forced the entire system to be designed around a single dispatch abstraction rather than two parallel code paths.

### Interfaces, all backed by the same brain

| Surface | Notes |
|---|---|
| CLI | Headless main loop, primary dev interface |
| Telegram | Full remote control â€” status, screenshots, GPU telemetry, voice notes, mode toggles |
| Discord | Separate sandboxed conversation context per contact, 3-tool allowlist, no shared memory with the admin brain |
| WebRTC voice call | LiveKit Agents SDK, wake-word gated, barge-in capable |
| Desktop companion (`aster-face/`) | Tauri app â€” animated reactor-core face reacting to mood/wake state, live call, minimizable to a small always-on-top widget |
| Full dashboard (`Aster-UI/`) | Tauri app â€” memory CRUD, skills toggles, activity log, connected-services status |

## Engineering choices worth noting

- **One LLM adapter function**, `_execute_gemma_completion()`, is the single place every caller (chat, vision, sentry, persona generation) goes through â€” every non-tool-calling caller has to explicitly unwrap the same dict shape, which was a deliberate choice to keep one calling convention instead of two.
- **Modular persona system** â€” the entire personality (tone, response length, mood-awareness) lives in swappable markdown files, selected by one config key, with zero code branching on "which persona."
- **Ref-counted, lazy-loaded models** â€” Kokoro TTS and the voice-emotion model are acquired/released like a semaphore so VRAM is only paid for while something is actually using them, instead of holding every model resident all the time on a 12GB card.
- **Fusion over precedence, not overwrite** â€” mood detection combines multiple signals (text > face > ambient voice) so a confident text read is never clobbered by a noisier secondary signal, but a still-neutral read gets filled in by whatever's available.
- **Everything optional fails closed** â€” every third-party integration (Spotify, Gmail, Discord, Firecrawl) checks for its credential at boot and disables itself gracefully rather than crashing the whole system when a key is missing.

## Tech stack

| Layer | Choice |
|---|---|
| LLM inference | `llama-server` (llama.cpp), Gemma-4-E4B Q4_K_M, 128k ctx, full CUDA offload |
| STT | Faster-Whisper (`medium.en`, CTranslate2, CUDA) |
| TTS | Kokoro 82M |
| Voice ID | SpeechBrain ECAPA-TDNN speaker embeddings |
| Vision / OCR | `mss` + pytesseract + OmniParser v2 (YOLOv8) for UI grounding, `face_recognition` for identity |
| Emotion | `j-hartmann/emotion-english-distilroberta-base` (text), IEMOCAP wav2vec2 (voice), `hsemotion-onnx` (face) |
| Memory | ChromaDB (vector) + markdown (human-readable) dual write |
| Voice transport | LiveKit Agents SDK, Silero VAD, openWakeWord |
| Desktop apps | Tauri 2 + React 19 + TypeScript + Vite + TailwindCSS |
| Bridges | Telegram (pyTelegramBotAPI), Discord (discord.py + REST v10) |

## Repository layout

```
core/            LLM adapter, agentic tool-calling loop, memory read/write
tools/           69 admin tools: vision, GUI automation, voice ID, emotion, Gmail/Calendar, Spotify, system control, RAG...
Aster_Vault/     Per-install runtime data + shippable assets (system prompts, custom Jinja template)
aster-face/      Tauri desktop companion â€” animated reactor-core face + live call
Aster-UI/        Tauri full dashboard â€” memory CRUD, skills toggles, activity log
tests/           pytest suite (mocked LLM, no server required)
engine_testing/  Standalone prompt-tuning harness for the legacy tool-calling path
main.py          Entry point â€” CLI loop + Telegram/Discord/WebRTC daemon threads
webrtc_bridge.py Standalone LiveKit voice agent
```

## Status

This is a live, actively-used personal system (not a demo repo) â€” I run it daily as my own assistant. Model weights, voice enrollments, and personal config are gitignored; see `secrets.example.yaml` and `self_config.example.yaml` for the setup shape. A from-scratch install additionally requires a running `llama-server` instance serving Gemma-4-E4B + its multimodal projector, and Tesseract OCR â€” see `first_run_setup.py` for the guided setup wizard.

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

---

*Built solo by Mohamed Saeed.*
