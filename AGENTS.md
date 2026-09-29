# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Commands

```powershell
python main.py                          # Start Aster (headless CLI)
pytest tests/test_migration.py -v       # Run all tests (mocked LLM, no llama-server needed)
pytest tests/test_migration.py -v -k "test_name"  # Run single test
```

## Critical Non-Obvious Patterns

- **No virtual environments** — all packages installed globally via `python -m pip install <pkg>`. Never create venvs.
- **llama-server is the single engine** at `http://localhost:8080` (BeeLlama v0.4.7 fork: Qwen 3.6 35B-A3B MoE, 60k ctx, KVarN KV, MTP spec-decode, thinking off, mmproj vision on the GPU, `--n-cpu-moe 28` (vision-first); Faster-Whisper stays OFF the GPU — voice notes use the CPU instance, only a LiveKit call loads CUDA Whisper) — no `llama-cpp-python`, no Ollama. One server serves text, vision, tool calls, Discord, and sentry. Live validation of the swapped engine is tracked in `engine_testing/qa/qwen-swap-live-validation.md`.
- **Native OpenAI tool calling** — `ADMIN_TOOLS` (full JSON-Schema dicts) is sent as the `tools` param on every `/v1/chat/completions` POST; llama-server returns a structured `tool_calls` array, and tool results go back as `role:"tool"` messages with `tool_call_id`. The legacy XML-in-text path (`_polyfill_tool_calls`, `_legacy_xml_parse`) was **removed in the Qwen 3.6 swap (2026-09-27)**.
- **Adding a tool requires 3 edits**: (1) schema dict in `ADMIN_TOOLS` list (~line 324), (2) `elif` branch in `execute_tool()`, (3) import at top of [`core/brain.py`](core/brain.py).
- **Two separate brain contexts**: Admin (71 tools, 15-round loop) vs Discord (3 tools, 4-round loop), fully independent histories.
- **No `<image>` marker injection** — the chat template (`E:\Models\qwen36_chat_template.jinja`, froggeric v22.5) places vision tokens itself. The Gemma-era marker injection was removed in the Qwen swap; do not re-add it.
- **Base64 purge** — `purge_media_cache()` strips base64 from history after each turn to prevent VRAM bloat; images are archived to `Aster_Vault/images/` with tombstones.
- **`is_fact_already_known()` path casing bug** — reads `Aster_vault/memory.md` (lowercase `v`) while everything else uses `Aster_Vault`. Beware on case-sensitive filesystems.
- **Credentials live in `secrets.yaml`** (gitignored, read via `config._secret()`) — never in `config.py` or `self_config.yaml` (the latter is exposed verbatim to the LLM via `get_my_config`).
- **Docs duty** — update root [`CLAUDE.md`](CLAUDE.md) after behavior changes; `summary.md` is a dated baseline updated on architectural shifts. (`.github/instructions/summary-workflow.instructions.md` does not exist — only `python-global-packages.instructions.md` does.)
- **Disabled/archived code**: FastAPI dashboard (commented out in `main.py`), Sentry daemon auto-start (commented out), `tools/gui.py` (not imported). The archived YOLO blocks in `tools/vision.py` and the Gemma native-audio path (`input_audio`) were **deleted in the Qwen swap**; the Florence-2 captioner reservation was removed (never wired); `tools/web.py` does not exist.
- **GUI automation**: `press_key` for keyboard keys (Enter/Tab/Esc), NOT `smart_click`. Clicking the word "Enter" found by OCR is a known past bug.
- **Automation flags are default-OFF** — `USE_DOM_MOTOR` (DOM-first motor: `tools/dom.py`), `USE_LAYA_KERNEL` (System-1/Laya kernel: `core/system1.py`), `USE_PIXEL_FALLBACK` (OmniParser/YOLO Track 2). `smart_click` gained a `confirm_send` param for the web send gate (BLOCKED until owner confirms). Stage plan + QA gates: `engine_testing/qa/`. **`browse_web`** (live web reading via an Aster-owned real Chrome/Edge) is a separate tool, always on unless `automation.web_browse: false`; with `automation.human_search: true` (set on this install) site+query lands on the site's home page and the model drives its own search bar (the mapped search URLs are the rollback path). **The owner's real browser profile can NEVER be driven** (settled 2026-09-26: Chrome ≥136 port block on the default dir + v20 app-bound cookies; copies/junctions get the cookies PURGED — never attempt); `use_real_profile: true` fails loudly with the `login_once.py` alternative (one-time login in the dedicated profile = the only logged-in CDP path).