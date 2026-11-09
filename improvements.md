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
| `_RELAY_HINT_RE = None` | 2577 | Dead placeholder — assigned `None`, never read (a QA-round-5 tombstone comment). Delete both lines. |
| `import sys` | 1 | Unused (ruff F401). |
| `locate_ui_element`, `verify_action_result`, `contact_names` imports | 29, 30, 35 | Unused (F401). Code uses `locate_ui_element_ex`. |
| `resolve_typo, send_discord_message` imports | 35, 36 | Unused in active production (outbound bot relay archived). |
| `_is_discord_message_request()` | 2616 | Production-dead. Only caller left is `tests/test_contact_routing.py`; `process_user_input` stopped calling it. |
| `_APP_GUIDE_MAP` entries | 1733–1742 | 4 of 8 point at missing files; now largely superseded by the Two-Tier UI Playbook engine (`tools/playbooks.py`). |
| Outbound Discord Bot Relay Dead Code | 1882–1888, 3601, 3754–3781, 3826–3834 | Commits `78b0a98` & `1c6048f` archived `send_discord_message` from `ADMIN_TOOLS` and hardcoded `discord_message_request = False`. The disabled stub, dead prompt injection block, and unreachable blocked-tools check can be deleted. |
| Stale comment in `_discord_hostility` | 3273–3280 | Says the lexicon "catches slurs and profanity aimed at Aster" — false after the imperative-only lexicon. |
| `_laya_relay_intent` docstring | 2582–2590 | Says "send-ish verb pre-filter"; code now requires a contact name or "discord". Minor drift. |

**Tool Registry & Dispatch Parity:**
- All 67 `ADMIN_TOOLS` schemas (lines 446–1740) have a matching `_execute_tool_impl` branch (script-verified).
- `_execute_tool_impl` contains **70 branches**: the 67 admin tools, plus 1 archived fallback (`send_discord_message`) and 2 Discord-specific tools (`forward_to_owner`, `save_personal_fact`).
- `DISCORD_TOOLS` contains **4 tools** (`forward_to_owner`, `save_personal_fact`, `remember_pronoun`, `get_current_track`).

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
| `MODEL_NAME` | `config.py:168` | Never used; brain hardcodes `"model": "local"` (brain.py:115). Comment admits the server ignores it. |
| `RAG_STALE_DAYS` | `config.py:533` | Never read; `tools/rag.py:193` hardcodes `90`. Changing the knob does nothing. |
| `LIVEKIT_ROOM_NAME` | `main.py:49` | Dead constant. |
| `paths.voice_ref_dir` | `self_config.example.yaml:171` | Unreferenced in Python code (`config.py` uses `paths.voices_dir` and `paths.custom_voices_dir`). |
| `paths.notes_file` | `self_config.example.yaml:166` | Unused; `tools/notes.py` hardcodes `Aster_Vault/notes.md`. |
| `paths.models_dir` | `self_config.example.yaml:164` | Unused in code. |
| `paths.chat_template` | `self_config.example.yaml:168` | Unused in code (passed as CLI parameter in `start.bat`). |

## 5. Dead files / assets

**Tracked in the repo:**
- `index.html` (root, 17 KB) — a *"The Culinary Haven — Fine Dining"* restaurant
  template; referenced by nothing. Unrelated to Aster (old agent-test artifact).
- `conversation_testing.py` — unreferenced dev harness; docstring still describes
  "The XML tool manual" (pre-Qwen era).
- Root one-off scripts, all unreferenced: `system1-test.py`, `dom-motor-test.py`,
  `kokoro_test.py`, `_inspect_mmproj.py`.
- `annoying-aster-prompt.txt` (16 KB), `WhatsApp Chat with George Da Homie.txt`
  (272 KB), `Bugs.txt`, `ideas-for-backend.md` — unreferenced.
- Empty dirs: `Aster_Models/`, `Chat-template/`.
- `yolov8n.pt` (6.5 MB) — **[RESOLVED / REMOVED]** deleted alongside OmniParser v2 in the Direct Qwen 3.6 Vision Grounding migration.
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
| `wav2vec2_checkpoints/models--facebook--wav2vec2-base` | 725 MB | **[SAFE TO PURGE]** Unreferenced; emotion recognition subsystem was excised in commit `f65d3b1`. |
| `piper_engine/` | 98 MB | Zero references. |
| `pretrained_models/` | ~0 | 0-byte husk files (from the earlier dead-code list). |

These are `.gitignore`d (`llama-server/`, `llama-server-turboquant/`,
`whisper-models/`, `piper_engine/`, `Aster_Models/`, `*.pt`) — safe to reclaim
disk, but confirm nothing external depends on them first.

## 7. Doc drift (audited & resolved)

- **[RESOLVED]** `README.md` claimed 69 tools, emotion models, Tesseract OCR, OmniParser, and `medium.en` Faster-Whisper. Updated to 67 tools, Qwen 3.6 vision grounding, and `small.en`.
- **[RESOLVED]** `CLAUDE.md` and `AGENTS.md` claimed 3 Discord tools. Updated to 4 tools (`forward_to_owner`, `save_personal_fact`, `remember_pronoun`, `get_current_track`).
- **[RESOLVED]** `CLAUDE.md` described playbooks as `.yaml`. Updated to `.json` across all sections, noting the 18 pre-seeded playbooks.
- **[RESOLVED]** `AGENTS.md:24` false claim that `is_fact_already_known()` has an `Aster_vault` casing bug removed (uses `os.path.join("Aster_Vault", "memory.md")`).
- **[RESOLVED]** `AGENTS.md:26` false claim that `.github/instructions/summary-workflow.instructions.md` does not exist corrected.
- **[RESOLVED]** `summary.md` Appendix A tool registry updated from 54 stale tools to the full 67 `ADMIN_TOOLS`.
- `main.py:55` still prints "Failed to load **llama-cpp-python** engine" on failure (legacy engine name).
- `self_config.yaml:36` `context_window: 131072` drift against `start.bat` (`--ctx-size 60000`) and `config.py` (60000).

## Resolved Cleanups (Direct Qwen 3.6 Vision Grounding Migration)

The GUI locator was overhauled from the legacy multi-track (UIA/OCR/YOLO+caption) architecture to a streamlined Track 0 UIA → Track 1 Direct Qwen 3.6 Vision Grounding architecture:
- **`yolov8n.pt`** (6.5 MB) — deleted from repo root.
- **PyTesseract OCR** — all `pytesseract` calls, 2× upscale, OCR line clustering, and Tesseract binary prerequisite completely stripped.
- **OmniParser v2 / YOLOv8** — `_yolo_track`, `_caption_crop`, and HuggingFace download dependencies stripped from `tools/vision.py`.
- **Dead config flags removed** — `USE_PIXEL_FALLBACK` and `ICON_CAPTIONER` removed from `config.py` and `self_config.yaml`.
- **Test suite updates** — `tests/test_automation_flags.py` removed; `tests/test_locator.py` overhauled to test UIA + Direct Qwen 3.6 Vision Grounding.

## 8. Suggested cleanup order

1. **Zero-risk:** `_RELAY_HINT_RE`, the unused imports (§3), `LIVEKIT_ROOM_NAME`,
   the `main.py:51` message, `MODEL_NAME` / `RAG_STALE_DAYS`,
   `index.html` (`yolov8n.pt` resolved).
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
