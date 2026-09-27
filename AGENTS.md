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
- **llama-server is the single engine** at `http://localhost:8080` — no `llama-cpp-python`, no Ollama. One server serves text, vision, tool calls, Discord, and sentry.
- **XML-based ReAct tool calling** — NOT OpenAI function-calling. The LLM outputs `<tool_call>{"name": "...", "arguments": {...}}</tool_call>`. `_polyfill_tool_calls()` in [`core/brain.py`](core/brain.py) handles Gemma-specific `<|tool_call|>` variants.
- **Adding a tool requires 3 edits**: (1) schema dict in `ADMIN_TOOLS` list (~line 241), (2) `elif` branch in `execute_tool()`, (3) import at top of [`core/brain.py`](core/brain.py).
- **Two separate brain contexts**: Admin (69 tools, 15-round loop) vs Discord (3 tools, 4-round loop), fully independent histories.
- **`<image>` marker injection** — every `image_url` content block must have a `<image>` text marker injected for the custom Gemma Jinja template (`Aster_Vault/gemma4-multimodal.jinja`).
- **Base64 purge** — `purge_media_cache()` strips base64 from history after each turn to prevent VRAM bloat; images are archived to `Aster_Vault/images/` with tombstones.
- **`is_fact_already_known()` path casing bug** — reads `Aster_vault/memory.md` (lowercase `v`) while everything else uses `Aster_Vault`. Beware on case-sensitive filesystems.
- **Hardcoded credentials** in [`config.py`](config.py) — Spotify, Telegram, Discord, LiveKit tokens are plaintext. Do NOT commit new ones or relocate without asking.
- **`summary.md` is source of truth** — must be updated after every code change. See `.github/instructions/summary-workflow.instructions.md`.
- **Disabled/archived code**: FastAPI dashboard (commented out in `main.py`), Sentry daemon auto-start (commented out), `tools/gui.py` (not imported), old YOLO pipeline (string block at bottom of `tools/vision.py`), native audio track (`if False:` bypassed). The Florence-2 captioner reservation was removed (never wired); `tools/web.py` does not exist.
- **GUI automation**: `press_key` for keyboard keys (Enter/Tab/Esc), NOT `smart_click`. Clicking the word "Enter" found by OCR is a known past bug.
- **Automation flags are default-OFF** — `USE_DOM_MOTOR` (DOM-first motor: `tools/dom.py`), `USE_LAYA_KERNEL` (System-1/Laya kernel: `core/system1.py`), `USE_PIXEL_FALLBACK` (OmniParser/YOLO Track 2). `smart_click` gained a `confirm_send` param for the web send gate (BLOCKED until owner confirms). Stage plan + QA gates: `engine_testing/qa/`. **`browse_web`** (live web reading via an Aster-owned real Chrome/Edge) is a separate tool, always on unless `automation.web_browse: false`; with `automation.human_search: true` (set on this install) site+query lands on the site's home page and the model drives its own search bar (the mapped search URLs are the rollback path). **The owner's real browser profile can NEVER be driven** (settled 2026-09-26: Chrome ≥136 port block on the default dir + v20 app-bound cookies; copies/junctions get the cookies PURGED — never attempt); `use_real_profile: true` fails loudly with the `login_once.py` alternative (one-time login in the dedicated profile = the only logged-in CDP path).