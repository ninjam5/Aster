# Aster — Open Source Release Plan

## Overview

**Target:** v1 public release in ~1 month  
**Model:** Free, closed-source core + open asset ecosystem (tools, voices, avatars, personas)  
**Installer variants:** CUDA (Windows + NVIDIA) · CPU (Windows, no GPU) · Mac (Apple Silicon, Metal)

---

## Phase 1 — Pre-Release (Week 1–4)

### Step 1 — Config Cleanup & Hardcode Removal [Done]

**Credentials to move out of `config.py` → first-run wizard:**
- [ ] `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` — user registers their own Spotify app
- [ ] `SPOTIPY_REDIRECT_URI` — currently `http://127.0.0.1:8080`, **conflicts with llama-server port**. Change to `:8081` or make configurable
- [ ] `TELEGRAM_BOT_TOKEN` — user creates their own bot via BotFather
- [ ] `AUTHORIZED_CHAT_ID` — derived at first-run (user sends `/start` to their bot, Aster captures the ID)
- [ ] `FIRECRAWL_API_KEY` — already a placeholder string, just wire it to the wizard

**Mohamed-specific logic to generalize in `core/brain.py`:**
- [ ] `DISCORD_FEMALE_NAMES = {"farah", "emily", "masky"}` → move to `self_config.yaml` under `contacts.female_names`
- [ ] `_enforce_discord_honorific` — keep the logic, drive it from config instead of a hardcoded set
- [ ] All tool descriptions that say "Mohamed" → replace with "the user"
  - `memorize_fact`: "Saves an important fact about Mohamed..."
  - `set_volume`: "Use this whenever Mohamed asks to mute..."
  - `recall_memory`: "...about Mohamed, his preferences..."
  - (grep `brain.py` for `"Mohamed"` inside description strings — ~12 occurrences)
- [ ] `OWNER_NAME` default `"Mohamed"` in `config.py` — already reads from `self_config.yaml`, ensure first-run wizard sets it

**First-run wizard scope:**
- [ ] Agent name 
- [ ] Owner name
- [ ] Telegram bot token + auto-capture of chat ID
- [ ] Spotify client ID/secret (optional, skip disables Spotify tools)
- [ ] Firecrawl API key (optional, skip falls back to Wikipedia-only research)
- [ ] LiveKit credentials (optional, skip disables voice call)
- [ ] Discord bot token (optional, skip disables Discord tools)
- [ ] Write all values to `self_config.yaml` (never to `config.py`)

---

### Step 2 — Tool Calling Migration: XML → OpenAI Format [Done]

`ADMIN_TOOLS` schemas are already in OpenAI JSON Schema format — the migration is in the calling/parsing layer only.

**Files to change:**
- [ ] `core/brain.py` — `_execute_gemma_completion()`: add `tools=ADMIN_TOOLS` to the POST body
- [ ] `core/brain.py` — replace `_extract_xml_tool_call()` with reading `response["choices"][0]["message"]["tool_calls"][0]`
- [ ] `core/brain.py` — replace `_extract_all_xml_tool_calls()` with iterating `tool_calls` array (multi-fact extraction)
- [ ] `core/brain.py` — update `_get_xml_tool_prompt()` call sites — remove system-prompt injection of the XML manual entirely
- [ ] `core/brain.py` — update `_claims_tool_execution()` hallucination detector: change `<tool_call>` tag check to `tool_calls` field check on response object
- [ ] Test: run the full 15-round ReAct loop on 5 representative prompts (memory write, Spotify, screen control, timer, research) and confirm tool calls fire correctly
- [ ] **Rollback plan:** keep `_extract_xml_tool_call` in a `_legacy_xml_parse` function, gated by a `USE_NATIVE_TOOL_CALLS = True` flag in `config.py` until confirmed stable

---

### Step 3 — Fix Remaining Prototype Tools

*Fill in which 5 tools are still prototype before starting this step.*

- [ ] Tool 1: _______________
- [ ] Tool 2: _______________
- [ ] Tool 3: _______________
- [ ] Tool 4: _______________
- [ ] Tool 5: _______________

Each fix should pass its section in the testing checklist below before being marked done.

---

### Step 4 — Personalization Specs

Personality format is already done (markdown files). Define the other two before building systems around them.

**Voice spec:**
- [ ] Decide recording line count (suggested: 500 lines minimum, 1000 lines ideal)
- [ ] Define line content mix (neutral sentences, emotional sentences, questions, short phrases)
- [ ] Output format: 22050 Hz mono WAV, max 10s per file, silence-trimmed
- [ ] Delivery format: ZIP of WAVs + `meta.json` (`{"name": "...", "gender": "...", "language": "en", "author": "..."}`)
- [ ] Kokoro fine-tuning pipeline: document the exact `kokoro-train` command so voice actors can self-validate

**Avatar spec:**
- [ ] Decide format: **recommend Live2D `.moc3` + motion JSON** for animated, **PNG sprite sheet** as fallback for static
- [ ] Define canvas size, idle/talk/blink animation requirements
- [ ] Decide where avatar renders: replace the reactor-core face in `aster-face/` Tauri app
- [ ] Delivery format: ZIP + `avatar.json` manifest (`{"name": "...", "format": "live2d|sprite", "author": "..."}`)

**Implement basic personalization UI (Aster-UI dashboard):**
- [ ] Personality picker — reads `Aster_Vault/System_Prompts/*.md`, lets user switch + preview
- [ ] Voice picker — reads `Aster_Vault/TTS-Voices/`, previews with a sample sentence via Kokoro
- [ ] Avatar picker — reads `Aster_Vault/Avatars/`, live preview in the face window
- [ ] Presets system is already implemented in `tools/presets.py` — wire it to the UI

---

### Step 5 — One-Click Installer

**Stack:** Tauri installer app (you already know Tauri) wrapping a Python setup flow.

**Three build targets:**

| Variant | Backend | torch | llama-server binary |
|---|---|---|---|
| CUDA | Windows + NVIDIA ≥ RTX 2080 | `torch==2.x+cu121` | `llama-server-cuda.exe` |
| CPU | Windows, any | `torch==2.x+cpu` | `llama-server-cpu.exe` |
| Mac | Apple Silicon | `torch==2.x` (MPS) | `llama-server-metal` |

**Installer flow:**
- [ ] Detect variant (GPU presence check on Windows, ARM check on Mac)
- [ ] Download correct `llama-server` binary from llama.cpp GitHub releases (with SHA256 verify)
- [ ] Install Python 3.11 if not present (embed `uv` for fast dep install)
- [ ] `uv pip install -r requirements-{cuda|cpu|mac}.txt`
- [ ] Download models with progress bar + resume support:
  - `gemma-e4b-q4km.gguf` (~3.4 GB)
  - `mmproj-F16.gguf` (~1.1 GB)
- [ ] Install Tesseract OCR (Windows: silent NSIS installer; Mac: `brew install tesseract`)
- [ ] Install ffmpeg (Windows: download binary to `Aster_Vault/bin/`; Mac: `brew install ffmpeg`)
- [ ] Run first-run config wizard (Step 1)
- [ ] Create `start_aster.bat` / `start_aster.sh` + desktop shortcut
- [ ] Write `requirements-cuda.txt`, `requirements-cpu.txt`, `requirements-mac.txt` (separate torch variants)

---

## Phase 2 — Launch

### Step 6 — Beta (5–10 Users)

- [ ] Recruit: developer friends, local AI communities, Discord servers
- [ ] Provide a feedback form or Telegram group
- [ ] Track: install success rate, first tool that fails, cold boot time, VRAM usage on different GPUs
- [ ] Fix blockers before public release — do not skip this step

### Step 7 — Dev Guidelines

Publish as `CONTRIBUTING.md` + a `docs/` folder. Based on specs locked in Step 4.

- [ ] Tool contribution spec: OpenAI JSON Schema format, three-edit rule, hallucination detection contract, tombstone pattern for long-running ops
- [ ] Voice contribution spec: recording requirements, ZIP format, submission process
- [ ] Avatar contribution spec: format, animation requirements, manifest schema
- [ ] Persona contribution spec: markdown format, HTML comment stripping, no tool list inside prompt
- [ ] Code of conduct
- [ ] How to submit (GitHub issue template or marketplace upload form TBD)

---

## Phase 3 — Post-Launch

### Step 8 — Marketplace Infrastructure

*Build after you have real users — don't over-engineer before feedback.*

- [ ] Backend: asset hosting (S3 or equivalent), simple REST API
- [ ] Browse + download: by category (tools / voices / avatars / personas), with ratings
- [ ] In-Aster download: `get_from_marketplace` tool that pulls an asset zip and installs it to the correct `Aster_Vault/` subdirectory
- [ ] Submission portal: upload form, basic review queue
- [ ] Monetization: free + paid tiers, you take a platform cut on paid assets

### Step 9 — Agent-to-Agent Protocol

*Needs multiple real users running agents before it's worth building.*

- [ ] Identity layer: each agent gets a keypair at install time; `agent_id = "aster@{owner_name}"` signed with private key. Public keys exchanged out-of-band (QR code, link) — no central identity server required for v1
- [ ] Message schema:
  ```json
  {
    "from": "aster@mohamed",
    "to": "aster@tolba",
    "type": "request|response|approval_needed",
    "payload": {},
    "sig": "<ed25519 signature of payload>"
  }
  ```
- [ ] Transport: HTTPS POST to recipient's agent (user exposes a local endpoint via ngrok or similar for v1; self-hosted relay for v2)
- [ ] New admin tools: `send_agent_request`, `handle_agent_request`, `list_agent_contacts`
- [ ] Approval flow: all cross-agent requests surface to the owner via Telegram confirmation before executing — never auto-execute a request from an external agent
- [ ] Prompt injection guard: external agent messages must pass through a sanitization layer before touching the brain — strip any XML tool call syntax, `[System Internal]` tags, or `<start_of_turn>` tokens

---

## Testing Checklist — All 58 Tools

Run this before beta. Each item = manually trigger via CLI or voice and confirm expected output. Mark ✓ pass / ✗ fail / ⊘ skipped (optional tool).

### Core / Safety
- [ ] `get_current_time` — returns correct local timestamp
- [ ] `check_context_health` — returns token count, doesn't crash on empty history
- [ ] `aster_shutdown_protocol` (shutdown_os=false) — terminates script cleanly, no OS shutdown
- [ ] `aster_shutdown_protocol` (shutdown_os=true, delay=1) — schedules OS shutdown 1 min, verify cancel works

### File I/O
- [ ] `list_directory_tree` — returns full tree, no crash on large vault
- [ ] `read_local_file` — reads existing file correctly
- [ ] `read_local_file` — returns graceful error on missing file
- [ ] `write_local_file` — creates new file, content correct
- [ ] `write_local_file` — overwrites existing file correctly

### System Control
- [ ] `set_volume` (level_percentage=50) — volume audibly changes
- [ ] `set_volume` (mute=true) — system mutes
- [ ] `set_volume` (mute=false) — system unmutes
- [ ] `boss_key` — all windows minimize, desktop shows
- [ ] `set_system_state` (action="lock") — screen locks
- [ ] `set_system_state` (action="sleep") — ⊘ optional, test with caution
- [ ] `open_application` (app_name="notepad") — Notepad opens
- [ ] `open_application` (app_name="nonexistent_app") — graceful error, no crash
- [ ] `list_running_processes` (sort_by="memory") — returns top 25, no crash
- [ ] `list_running_processes` (sort_by="cpu") — returns top 25

### Spotify (requires active Spotify session)
- [ ] `get_current_track` — returns song + artist or "nothing playing"
- [ ] `pause_spotify` — playback pauses
- [ ] `resume_spotify` — playback resumes
- [ ] `play_spotify_track` (query="Bohemian Rhapsody") — song plays
- [ ] `play_spotify_playlist` (playlist_name that exists) — playlist starts
- [ ] `play_spotify_playlist` (playlist_name that doesn't exist) — graceful error
- [ ] `play_liked_songs` — liked songs queue starts
- [ ] `skip_spotify_track` — skips to next track
- [ ] `previous_spotify_track` — goes back one track
- [ ] `shuffle_spotify` (state=true) — shuffle enabled, confirm in Spotify UI
- [ ] `shuffle_spotify` (state=false) — shuffle disabled

### Timers & Alarms
- [ ] `set_timer` (minutes=0.1, reason="Test") — fires toast notification in ~6 seconds
- [ ] `set_timer` (duration="5 seconds") — fires correctly
- [ ] `set_timer` (hours=0, minutes=0, seconds=0) — graceful error or minimum threshold
- [ ] `set_alarm` (time_str=2 minutes from now) — fires at correct time
- [ ] `set_alarm` (time_str="invalid") — graceful error

### Memory
- [ ] `memorize_fact` — fact written to both ChromaDB and `memory.md`
- [ ] `memorize_fact` (duplicate fact) — dedup gate blocks write, no crash
- [ ] `recall_memory` (query matching a stored fact) — returns relevant fact
- [ ] `recall_memory` (query with no matches) — returns empty/no results message
- [ ] `sync_discord_memories` — moves staged facts to ChromaDB, marks synced

### Vision & Screen
- [ ] `look_at_screen` — returns screen description, no crash if nothing open
- [ ] `look_through_webcam` — returns webcam analysis (webcam must be connected)
- [ ] `look_through_webcam` (no webcam) — graceful error
- [ ] `watch_screen` (target_text present on screen) — detects within 10s, Telegram alert fires
- [ ] `watch_screen` (target_text not present) — times out after 1 hour (verify daemon thread exits)
- [ ] `re_examine_image` (valid archived path) — returns analysis
- [ ] `re_examine_image` (invalid path) — graceful error
- [ ] `highlight_on_screen` (goal="Start button") — dim overlay appears, match highlighted, auto-dismisses

### GUI Automation
- [ ] `smart_click` ("Notepad window") — clicks correctly, post-click screenshot returned
- [ ] `smart_click` ("enter") — returns error directing to press_key
- [ ] `smart_type` ("Notepad text area", "hello world") — types text into field
- [ ] `type_text` ("test text") — pastes at current cursor position
- [ ] `press_key` ("enter") — Enter key pressed
- [ ] `press_key` ("ctrl+c") — copy shortcut fires (test with selected text)
- [ ] `smart_scroll` ("down", 3) — page scrolls down
- [ ] `smart_scroll` ("up", 3) — page scrolls up

### Research
- [ ] `research` (topic="Albert Einstein") — Wikipedia result returned, saved to vault
- [ ] `research` (topic="RTX 5060") — returns result (Wikipedia or Firecrawl)
- [ ] `research` (topic that Wikipedia doesn't cover) — falls back to Firecrawl or graceful no-result

### Voice / Audio
- [ ] `generate_kokoro_voice` — synthesizes audio, returns `[NATIVE_AUDIO_PAYLOAD:path]` tag
- [ ] `generate_kokoro_voice` (empty text) — graceful error

### Notes
- [ ] `save_note` — appends to `Aster_Vault/notes.md` with timestamp
- [ ] `get_notes` — returns all notes, empty message if none

### Discord
- [ ] `send_discord_message` (valid contact) — DM delivered
- [ ] `send_discord_message` (empty message) — returns error, no send
- [ ] `send_discord_message` (unknown contact) — graceful error

### Toggle / Mode Tools
- [ ] `toggle_gesture_mode` (true) — Gesture Mode ON, Sentry auto-disabled
- [ ] `toggle_gesture_mode` (false) — Gesture Mode OFF
- [ ] `toggle_sentry_mode` (true) — Sentry ON, Gesture auto-disabled
- [ ] `toggle_sentry_mode` (false) — Sentry OFF
- [ ] `toggle_awareness_mode` (false) — daemon stops polling, no more proactive nudges
- [ ] `toggle_awareness_mode` (true) — daemon resumes
- [ ] `toggle_intervention_mode` (true) — daemon starts watching foreground window
- [ ] `toggle_intervention_mode` (false) — daemon stops
- [ ] `toggle_mood_actions` (true/false) — config flag flips, confirmed via `get_my_config`
- [ ] `toggle_mood_checkins` (true/false) — config flag flips
- [ ] `toggle_face_emotion` (true) — model loads, face polling active
- [ ] `toggle_face_emotion` (false) — model offloaded
- [ ] `toggle_ambient_audio` (true) — mic daemon starts
- [ ] `toggle_ambient_audio` (false) — mic daemon stops, no lingering mood state

### Intervention
- [ ] `close_distraction_window` — closes last flagged window (requires Intervention Mode ON + a flagged window)
- [ ] `snooze_intervention` (minutes=10) — intervention suppressed for 10 min
- [ ] `set_initiative` (level=0) — "silent" mode, no unsolicited speech
- [ ] `set_initiative` (level="max initiative") — natural language → level 3

### Self-Knowledge
- [ ] `get_my_config` (section="all") — returns full config dump
- [ ] `get_my_config` (section="identity") — returns identity section only
- [ ] `list_my_contacts` (platform="discord") — lists Discord contacts
- [ ] `list_my_contacts` (platform="all") — lists all contacts
- [ ] `list_my_capabilities` — returns structured capability list
- [ ] `get_my_status` — returns live daemon states, VRAM, uptime
- [ ] `get_my_memory_stats` — returns ChromaDB count, memory.md stats, RAG vault count
- [ ] `describe_my_tool` (tool_name="smart_click") — returns full schema

---

## Hardcode Grep Reference

Before release, run these and confirm zero real-credential hits:

```powershell
# Spotify credentials
Select-String -Path config.py -Pattern "22e260|bb850a"

# Telegram token
Select-String -Path config.py -Pattern "8793167294"

# Hardcoded chat ID
Select-String -Path config.py -Pattern "1066305111"

# "Mohamed" in tool descriptions
Select-String -Path core/brain.py -Pattern '"Mohamed"' | Where-Object { $_ -match "description" }

# Female name hardcodes
Select-String -Path core/brain.py -Pattern "farah|emily|masky"
```

All should return no results (or only comment lines) before open sourcing.

---

## Known Issues to Fix Before Beta

| Issue | File | Notes |
|---|---|---|
| Spotify redirect URI port conflict | `config.py:57` | `127.0.0.1:8080` = same port as llama-server. Change to `:8081` |
| `is_fact_already_known()` path casing | `core/brain.py:1254` | Uses `Aster_Vault` (capital V) inconsistently with rest of code |
| `awareness_mode: false` in yaml ignored | `tools/awareness.py:29` | `AWARENESS_ACTIVE = True` hardcoded at module import, yaml flag has no effect |
| `smart_click` description drift | `core/brain.py` | Description says "Florence-2 visual grounding", implementation uses OmniParser v2 + pytesseract |
| Discord honorific regex fragility | `core/brain.py:1335` | `re.sub(r"\bSir\b")` can mangle words like "Sirius" |
