---
name: aster-diagnostics-and-tooling
description: Load this to MEASURE the Aster system instead of eyeballing it - checking service health, reading VRAM/RAM, inspecting the live conversation, estimating context usage, dumping the tool registry, or interpreting /status /peek /gpu /diagnostics output. Ships ready-to-run scripts in scripts/. Triggers - "is the server up", "how much VRAM", "what's in the context", "how many tokens", "dump the tools", "measure before/after".
---

# Aster Diagnostics and Tooling

House rule: numbers, not vibes. Every claim about performance, memory, or behavior
gets a measurement (`aster-research-methodology` holds the discipline; this skill
holds the instruments).

**When NOT to use this skill:** interpreting a *failure* → `aster-debugging-playbook`;
running test suites → `aster-validation-and-qa`; VRAM policy →
`aster-vram-discipline`.

## Shipped scripts (`scripts/` next to this file — all tested 2026-07-05)

Run from the repo root.

### 1. `health_check.py` — stack probe (offline-safe, always exits 0)
```powershell
python .claude/skills/aster-diagnostics-and-tooling/scripts/health_check.py
```
Checks llama-server `/health` + `/props` (reports which model file is loaded —
resolves the three-launchers ambiguity), face_server :8000, Tesseract, the two
repo model files, ChromaDB dir. Example output with Aster **not** running:
```
CHECK                                   RESULT  DETAIL
llama-server /health (:8080)            FAIL    DOWN — No connection could be made...
face_server (:8000)                     FAIL    DOWN — ...
Tesseract executable                    PASS    C:\Program Files\Tesseract-OCR\tesseract.exe
Aster_Vault\Models\gemma-e4b-q4km.gguf  PASS    5.41 GB
...
4/7 checks passed (services down are reported above — may be expected if Aster is not running)
```
FAILs on the two servers with Aster stopped are EXPECTED; FAILs on
Tesseract/models/Chroma are environment problems (`aster-build-and-env`).

### 2. `vram_report.ps1` — VRAM + devmode duplicate check
```powershell
powershell -File .claude/skills/aster-diagnostics-and-tooling/scripts/vram_report.ps1
```
Board totals, per-process VRAM, python.exe RAM working sets; warns if ≥2 python
processes exceed 2 GB RAM (the LiveKit devmode fork signature). Reference desktop
baseline measured 2026-07-05 with Aster stopped: **1.9 GB / 12.3 GB** used. With
Aster idle on E4B expect ≈ 6 GB (the budget). Note: `used_gpu_memory` shows `[N/A]`
for processes not using CUDA compute — normal.
(The script is ASCII-only on purpose: PowerShell 5.1 misparses UTF-8 punctuation in
BOM-less scripts — em-dash bytes decode as a CP1252 smart quote and break string
parsing. Keep it ASCII when editing.)

### 3. `context_estimate.py` — token math the way trim_memory does it
```powershell
python .claude/skills/aster-diagnostics-and-tooling/scripts/context_estimate.py somefile.md
"text" | python .claude/skills/aster-diagnostics-and-tooling/scripts/context_estimate.py
```
Mirrors `core/memory.py` exactly (`len//4`, budget = 90% of
`runtime.context_window`; reads only that single yaml key). Verified output shows
N_CTX 131072 → budget 117,964 est. tokens — note this exceeds the server's
`--ctx-size 128000`… in *estimated* tokens, and the heuristic under-counts dense
text, so treat >80% of budget as the danger zone.

### 4. `dump_tool_registry.py` — live tool list (slow: ~1 min, loads face models)
```powershell
python .claude/skills/aster-diagnostics-and-tooling/scripts/dump_tool_registry.py
```
Prints `68 admin tools registered:` + numbered name/description table. Use after
any brain.py edit to confirm registration.

## Telegram diagnostic commands (live system)

| Command | Reads | Interpretation |
|---|---|---|
| `/status` | Context health | Token figure is the len//4 estimate — see script 3's caveats |
| `/peek [N]` | Last N (≤40) brain messages, base64 as `[IMG]` | THE tool for "what did the model actually see/say"; check `tool_calls` presence, nudge artifacts, mood/speaker tags |
| `/gpu` | nvidia-smi board + per-process | Uses nvidia-smi deliberately — `torch.cuda` only sees the PyTorch slice and misses llama-server & Whisper |
| `/diagnostics on\|off` | ALL stdout forwarded to Telegram (2 s batches, `tools/diagnostics.py` stdout shim) | Fastest remote tailing; turn OFF after — it's chatty |
| `/screenshot` | Primary monitor | Ground truth for vision/GUI investigations |

## Self-knowledge tools (ask Aster, or call the functions)

| Tool | Returns |
|---|---|
| `get_my_status` | Uptime, LIVE daemon flags (read from module globals, never yaml), VRAM/RAM, initiative, persona |
| `get_my_memory_stats` | ChromaDB fact count, memory.md lines/KB, RAG vault doc count |
| `get_my_config(section)` | self_config.yaml content (never secrets — separate loader by design) |
| `describe_my_tool(name)` | Full JSON schema of any admin tool |
| `check_context_health` | Same estimate as /status |

## llama-server endpoints

```powershell
Invoke-RestMethod http://localhost:8080/health    # readiness
Invoke-RestMethod http://localhost:8080/props     # model path, n_ctx, generation settings
```
`/props` is the authority on which GGUF and context size are ACTUALLY loaded —
always check it before reasoning about model behavior (the launchers disagree about
the model; see `aster-run-and-operate`).

## Log streams

- `WS ws://localhost:8000/api/logs` — the realtime event stream (terminal lines +
  `wake`/`sentiment` events) that feeds both desktop UIs
  (`tools/realtime_stream.py`, queue max 500, drops oldest).
- Engine-side: llama-server prints per-request timings in its own console window.

## Measurement recipes

**Before/after VRAM for a model change** (the `aster-change-control` gate):
run script 2 at idle → perform the load/action → run again → trigger release →
run a third time; the third must match the first.

**Tool-loop inspection:** reproduce the turn, then `/peek 20`; count rounds,
look for repeated identical `tool_calls` (loop signature), check the final
assistant message isn't empty.

**Latency:** the engine harness reports tok/s and per-turn latency per scenario
(90.2 tok/s gen, 4.98 s avg/turn on 2026-06-24 — `engine_testing/results/`);
for one-off checks read llama-server's console timings rather than stopwatching.

## Provenance and maintenance

Authored 2026-07-05; all four scripts executed successfully that day (servers-down
path verified for script 1; live-GPU path verified for script 2).

- Scripts still run: re-execute the four commands above
- /props shape drift: `Invoke-RestMethod http://localhost:8080/props | ConvertTo-Json -Depth 3`
- Telegram handler set: `Select-String -Path main.py -Pattern "commands=\['"`
- Heuristic mirror still exact: `Select-String -Path core\memory.py -Pattern "// 4"`
