---
name: aster-run-and-operate
description: Load this to start, stop, or operate the Aster system - launching llama-server and main.py, understanding the start.bat flags, using Telegram commands, running the desktop UIs, finding where artifacts/logs land, or safely operating a live instance. Triggers - "start Aster", "run the system", "which bat file", "where does X get saved", "what does /peek do", "is it safe to run this while Aster is live".
---

# Aster Run and Operate

**When NOT to use this skill:** creating the environment from scratch →
`aster-build-and-env`; a component is failing → `aster-debugging-playbook`;
measuring → `aster-diagnostics-and-tooling`.

## Operational safety first

A running Aster is the owner's live assistant with real-world reach. When operating
it (or testing near it):
- Tools SEND things: Telegram messages, Discord DMs, Gmail (tier-gated), and GUI
  automation moves the real mouse/keyboard. Do not trigger outward-facing tools
  casually.
- Do not start a second `main.py` — daemons (Telegram polling, mic, face server
  port 8000) do not tolerate duplicates.
- Offline work (pytest, imports, the mock harnesses) needs NO llama-server and
  touches nothing live — prefer it.

## Boot order

1. **llama-server first** (one of the three launchers below). Wait until it serves
   `http://localhost:8080/health`.
2. **`python main.py`** — verifies the server (`config.load_engine()`, main.py:49),
   registers tools, starts daemons, fires a warmup completion, then runs the CLI
   input loop on the main thread.
3. Optional UIs (see below).

`Start-all.bat` does 1+2 together with a fixed 30 s wait between them.

### The three launchers — NOT interchangeable (verified 2026-07-05)

| Launcher | Model served | Extra flags |
|---|---|---|
| `start.bat` | `Aster_Vault/Models/gemma-e4b-q4km.gguf` (the model CLAUDE.md documents) | — |
| `start_new.bat` | **`E:\Models\gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf`** (QAT variant, path OUTSIDE the repo) | `-ngld 99 --parallel 1` |
| `Start-all.bat` | same QAT model as start_new.bat, then launches `main.py` | 30 s sleep between |

The docs describe the q4km model; the `*new*`/`all` launchers point at a newer QAT
file. **Check what is actually being served** before reasoning about model
behavior: `Invoke-RestMethod http://localhost:8080/props` (look at the model path).

### Flag anatomy (common to all launchers)

```
llama-server.exe
  --model <gguf>                      the LLM weights
  --mmproj Aster_Vault/Models/mmproj-F16.gguf   vision projector (REQUIRED for image turns)
  --port 8080                         the only port the brain knows
  -ngl 99                             all layers on GPU (-ngld 99: same for the draft/projector in newer builds)
  --ctx-size 128000                   context window — must match config.N_CTX (default 131072 — KNOWN mismatch, see aster-architecture-contract)
  --chat-template-file Aster_Vault/gemma4-multimodal.jinja   custom template: <__media__> placement + native tool grammar
  -ctk q4_0 -ctv q4_0                 4-bit KV cache (the thing that makes 128k fit)
  -fa on                              flash attention
  --slot-save-path Aster_Vault/kv_cache/   KV slot persistence
```

At boot llama-server logs `detected an outdated gemma4 chat template, applying
compatibility workarounds` — **expected and harmless** (CLAUDE.md).

### Healthy main.py boot looks like

- `[Tesseract] Using executable: C:\Program Files\Tesseract-OCR\tesseract.exe`
- `[Aster Internal: Booting Facial Recognition Engine...]` + learned faces
- `[Aster Core] Kokoro TTS 82M Voice Engine registered (lazy — loads on first use).`
- `[Aster Core] 68 tools registered for native tool calling.`
- `[Aster Engine] llama-server reachable at http://localhost:8080.`
- warmup success (main.py ~:699)

## Daemon roster (started in main.py:649-672)

| Daemon | Default state |
|---|---|
| Telegram `infinity_polling` | active (if token configured) |
| Discord DM listener | active (if token configured) |
| WebRTC bridge (LiveKit) | active — agent starts ASLEEP (wake word) |
| Gesture (MediaPipe) | thread up, idle until `/gesture on` |
| Intervention | thread up, idle until `/intervention on` |
| Awareness | active by default (`daemons.awareness_mode`) |
| Ambient audio | thread up, idle/no-mic until `/ambientaudio on` |
| Face server (uvicorn :8000) | active |
| Sentry | **auto-start commented out** (main.py:656); `toggle_sentry_mode` still works |
| Screen watcher | on-demand (`watch_screen`), 1-hour timeout |

## Telegram C2 command reference (handlers verified in main.py, 2026-07-05)

| Command | Effect |
|---|---|
| `/status` | Context health (token estimate via the 4-chars/token heuristic) |
| `/compact` | LLM-summarize history → `[System Memory Restored:]` block (refuses < 2000 est. tokens) |
| `/stop` | Aborts the current tool loop between rounds |
| `/diagnostics on\|off` | Forward all stdout to Telegram (2 s batches) |
| `/screenshot` | Immediate primary-monitor capture |
| `/peek [N]` | Last N (≤40) brain messages, base64 shown as `[IMG]` |
| `/gpu` | True board VRAM via nvidia-smi (+ per-process) |
| `/sentry on\|off`, `/gesture on\|off` | Webcam daemons (mutually exclusive with each other) |
| `/intervention on\|off` | Focus daemon |
| `/moodactions`, `/checkins`, `/faceemotion`, `/ambientaudio` | Opt-in emotion features |
| `/initiative [0-3\|more\|less\|silent…]` | Unprompted-speech dial |
| `/note <text>` | Append to `Aster_Vault/notes.md` |
| `/find <thing>` | Assist-mode screen highlight on the PC; replies with match count |
| `/enroll <name>` | Enroll the last unknown voice sample |
| voice note / photo / text | STT → brain / vision → brain / brain |

Only the configured `authorized_chat_id` is accepted; everyone else gets denied.

## The two desktop UIs (both need main.py running for live data)

```powershell
cd aster-face; npm run tauri build   # companion face — production build for daily use
cd Aster-UI;  npm run dev            # dashboard in a browser at :5173 (no Rust needed)
cd Aster-UI;  npm run tauri build    # dashboard as a native app (Rust+MSVC required)
```

Both talk to `tools/face_server.py` on `:8000` (LiveKit token, WS `/api/logs`, REST
for memory/notes/skills/stats; chat streams over SSE `POST /api/chat/stream`).

## Standalone entry points

- `python livekit_agent.py` — voice agent alone.
- `python engine_testing/run_engine_test.py` — model eval battery (needs
  llama-server; legacy XML path). See `aster-validation-and-qa`.
- `python conversation_testing.py` — persona style sweep (needs llama-server only).

## Artifact map — what lands where

| Artifact | Producer | Safe to delete? |
|---|---|---|
| `Aster_Vault/memory.md` | memorize_fact (dual-write with ChromaDB) | NO — long-term memory |
| `Aster_Vault/chroma_db/` | ChromaDB persistent store | NO |
| `Aster_Vault/Conversations/YYYY-MM-DD.md` | log_raw_turn (verbatim daily transcript) | Historical record — keep |
| `Aster_Vault/images/` | purge_media_cache cold storage | Old entries prunable |
| `Aster_Vault/emotion_log.jsonl`, `mood_trends.md`, `mood_flush_state.json` | mood pipeline | Auto-pruned (30 d) |
| `Aster_Vault/database/*.md` | research tool (RAG vault, 90-day staleness) | Regenerable |
| `Aster_Vault/notes.md` | /note, save_note | NO — owner content |
| `Aster_Vault/Faces/`, `Voices/<Name>/` | biometric enrollments | NO — owner data |
| `Aster_Vault/Voice/temp/*.wav` | Kokoro synthesis temps | Yes |
| `Aster_Vault/kv_cache/` | llama-server slot saves | Yes (regenerated) |
| `Aster_Vault/google_token.json` | Google OAuth cache | Deleting forces re-auth |
| `discord_memories.json` (repo root) | Discord fact staging | NO — unsynced facts live here |
| `engine_testing/results/*.txt` | eval harness | NO — frozen baselines |
| `Aster_Vault/conversation-tests/` | style sweeps | Keep as baselines |

## Shutdown

- CLI: Ctrl+C (daemons are daemon-threads; they die with the process).
- In-persona: `aster_shutdown_protocol` tool sets `config.SHUTDOWN_REQUESTED`
  (optionally schedules an OS `shutdown /s`).
- `/stop` only aborts the current tool run — it does not shut down.

## Provenance and maintenance

Authored 2026-07-05 against live code.

- Which model is actually served: `Invoke-RestMethod http://localhost:8080/props`
- Launcher drift: `Get-Content start.bat, start_new.bat, Start-all.bat`
- Telegram handlers: `Select-String -Path main.py -Pattern "commands=\['"`
- Daemon starts: `Select-String -Path main.py -Pattern "threading.Thread\(target="`
- Tool count at boot: look for `[Aster Core] 68 tools registered` in startup output
