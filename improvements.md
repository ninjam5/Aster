# improvements.md — Dead Code & Cleanup Backlog

Static-analysis audit of the Aster repo (dead code, unused symbols, stale docs).
**Nothing here has been changed** — this is a backlog. Each item is a candidate,
not an instruction; see the "judgment calls" note before deleting.

- **Audited:** 2026-09-29
- **Method:** full read of `core/brain.py`; reference-count of every top-level
  function/constant across all `.py` files; module-reachability scan; config-flag
  scan; `ruff check . --select F401,F811,F841,F821,F822,F823,ERA001`; targeted
  greps for file/asset references. Static only — the app was not run.
- **Caveat:** "test-only" symbols (used by `tests/` but not production) may be
  deliberate public API. Treat those as *verify-then-decide*, not delete.

---

## 1. `core/brain.py`

| Item | Line | Verdict |
|---|---|---|
| `_RELAY_HINT_RE = None` | 2522 | Dead placeholder — assigned `None`, never read (a QA-round-5 tombstone comment). Delete both lines. |
| `import sys` | 1 | Unused (ruff F401). |
| `locate_ui_element`, `verify_action_result`, `contact_names` imports | 29, 30, 35 | Unused (F401). Code uses `locate_ui_element_ex`. |
| `_is_discord_message_request()` | 2561 | Production-dead. Only caller left is `tests/test_contact_routing.py:189`; the comment at 3637 notes `process_user_input` stopped calling it (it re-parsed / cost Laya passes). |
| `_APP_GUIDE_MAP` entries | 1694–1703 | 4 of 8 point at **missing files** (`firefox.md`, `spotify.md`, `explorer.md`, `notepad.md`); `ui_guides/` only has chrome/discord/vscode/whatsapp. Dead map entries (harmless, always return ""). |
| Stale comment in `_discord_hostility` | 3227–3229 | Says the lexicon "catches slurs and profanity aimed at Aster" — false after the imperative-only lexicon. |
| `_laya_relay_intent` docstring | 2527–2534 | Says "send-ish verb pre-filter"; code now requires a contact name or "discord". Minor drift. |

**Checked and clean:** all 71 `ADMIN_TOOLS` schemas have a matching
`_execute_tool_impl` branch (script-verified) — no orphaned tools, and no
dispatch branch without a schema.

## 2. Dead functions / constants (0 references anywhere)

| Symbol | Location | Note |
|---|---|---|
| `tools/gui.py` (whole module) | 30 lines | Never imported; `ui_type`/`ui_press_key` duplicate brain.py's direct pyautogui use. |
| `verify_action_result` | `tools/vision.py:916` | Never called (its own docstring says the dispatch path doesn't use it); its "LAYER 3 — VERIFICATION" header too. |
| `_hostility_margin` | `tools/conversations.py:145` | Never called — brain reads `config.DISCORD_HOSTILITY_MARGIN` directly. (Introduced in the conversation-ending work.) |
| `pronoun_for` | `tools/people.py:136` | Never called. |
| `reset_session` | `tools/awareness.py:318` | Never called; docstring says "call from aster_shutdown_protocol" — that wiring was never added. |
| `_ANIMATED_KEYS` | `tools/awareness.py:211` | Dead constant. |
| `_WEB_LOCK` | `tools/dom.py:369` | Dead constant. |
| `answer_margin` | `core/system1.py:521` | Never called; the architecture contract cites it as a Laya API. |
| `publish_telemetry` | `tools/realtime_stream.py:60` | Only referenced by the commented-out dashboard import in `main.py:27`. |

**Test-only (verify before deleting):** `conversations.register_mute`,
`instrumentation.read_log`, and `dom.active_page` / `snapshot_web` /
`motor_status` / `windows_shortlist`.

## 3. Unused imports

- `core/brain.py`: `sys`; `locate_ui_element`; `verify_action_result`; `contact_names`.
- `tools/vision.py`: `difflib`, `json`, `re`, `requests`, `Any`; local `config`
  at 296.
- `tools/awareness.py:23`: `timedelta`.
- `tools/sentry.py`: `threading`; `load_known_faces`.
- `tools/self_knowledge.py`: `os`; `datetime`.
- `tools/face_server.py`: `contextlib`; `_build_system_content`.
- `tools/media.py:78`: unused local `e`.
- `webrtc_bridge.py:11`: `WorkerOptions`.
- `main.py`: `asyncio`; `contextlib`.

## 4. Dead config flags (assigned, never read — silent drift hazard)

| Flag | Location | Reality |
|---|---|---|
| `MODEL_NAME` | `config.py:164` | Never used; brain hardcodes `"model": "local"` (brain.py:105). Comment admits the server ignores it. |
| `RAG_STALE_DAYS` | `config.py:514` | Never read; `tools/rag.py:193` hardcodes `90`. Changing the knob does nothing. |
| `MOOD_CHECKIN_COOLDOWN` | `config.py:561` | Never read (awareness uses `MOOD_CHECKIN_QUIET_SECONDS` + `MOOD_TOUCH_COOLDOWN`). `CLAUDE.md` documents it as live. |
| `LIVEKIT_ROOM_NAME` | `main.py:45` | Dead constant. |

## 5. Dead files / assets

**Tracked in the repo:**
- `index.html` (root, 17 KB) — a *"The Culinary Haven — Fine Dining"* restaurant
  template; referenced by nothing. Unrelated to Aster (old agent-test artifact).
- `conversation_testing.py` — unreferenced dev harness; docstring still describes
  "The XML tool manual" (pre-Qwen era).
- Root one-off scripts, all unreferenced: `system1-test.py`, `emotion-test.py`,
  `ambient-audio-test.py`, `face-emotion-test.py`, `dom-motor-test.py`,
  `kokoro_test.py`, `_inspect_mmproj.py`.
- `annoying-aster-prompt.txt` (16 KB), `WhatsApp Chat with George Da Homie.txt`
  (272 KB), `Bugs.txt`, `ideas-for-backend.md` — unreferenced.
- Empty dirs: `Aster_Models/`, `Chat-template/`.
- `yolov8n.pt` (6.5 MB) — unreferenced; the live Track-2 YOLO loads
  `microsoft/OmniParser-v2.0/icon_detect/model.pt` from the HF cache.
- `.roo/` (4 Roo-agent rule files) — dead if Roo is no longer used.

**Judgment calls (do NOT delete blindly):**
- `aster.pt` / `aster2.pt` — legacy Kokoro voices, still referenced as back-compat
  by `tools/audio.py`.
- `login_once.py` — live (documented CDP login path).

## 6. Local disk bloat (gitignored — not repo deadweight, ~4.8 GB)

| Path | Size | Note |
|---|---|---|
| `llama-server-turboquant/` | 1.4 GB | Unused local llama.cpp bundle. |
| `llama-server/` | 1.1 GB | Unused local bundle (active engine = `E:\Models\beellama-v0.4.7…`). |
| `whisper-models/models--Systran--faster-distil-whisper-large-v3` | 1.4 GB | Documented unused (`small.en` is the active model). |
| `wav2vec2_checkpoints/models--facebook--wav2vec2-base` | 725 MB | Unreferenced. |
| `piper_engine/` | 98 MB | Zero references. |
| `pretrained_models/` | ~0 | 0-byte husk files (from the earlier dead-code list). |

These are `.gitignore`d (`llama-server/`, `llama-server-turboquant/`,
`whisper-models/`, `piper_engine/`, `Aster_Models/`, `*.pt`) — safe to reclaim
disk, but confirm nothing external depends on them first.

## 7. Doc drift (found while reading)

- `CLAUDE.md:349` claims `tools/web.py` exists with dead `search_web()` and that
  the brain uses `deep_web_search()` — **the file does not exist and the function
  is `research()`** (only doc hits for `deep_web_search`).
- `AGENTS.md:24` claims `.github/instructions/summary-workflow.instructions.md`
  **does not exist** — it does (2.9 KB), plus `read-docs-before-code.instructions.md`.
- Stale line refs: Sentry `main.py ~359` → actual **703**; `webrtc_bridge.py:668`
  → actual **753**; the archaeology skill's `--n-cpu-moe 26` → actual **28**
  (`start.bat`).
- `main.py:51` still prints "Failed to load **llama-cpp-python** engine".
- `_discord_hostility` comment (brain.py:3227–3229) — see §1.

## 8. Suggested cleanup order

1. **Zero-risk:** `_RELAY_HINT_RE`, the unused imports (§3), `LIVEKIT_ROOM_NAME`,
   the `main.py:51` message, `MODEL_NAME` / `RAG_STALE_DAYS` /
   `MOOD_CHECKIN_COOLDOWN`, `index.html`, `yolov8n.pt`.
2. **Low-risk:** `tools/gui.py`, `verify_action_result`, `_hostility_margin`,
   `pronoun_for`, `reset_session`, `_ANIMATED_KEYS`, `_WEB_LOCK`, `answer_margin`,
   `publish_telemetry`, `_is_discord_message_request`, the missing `ui_guides` map
   entries.
3. **Verify-then-decide:** `register_mute`, `read_log`, the `dom.py` helpers,
   `publish_telemetry` (if the dashboard may return), `aster.pt` / `aster2.pt`.
4. **Docs + disk:** refresh `CLAUDE.md` / `AGENTS.md` drift (§7) and reclaim the
   ~4.8 GB of gitignored local bundles (§6).

---

*Re-run the checks after any cleanup:*
```powershell
python -m ruff check core tools main.py config.py webrtc_bridge.py --select F401,F811,F841,ERA001
```
