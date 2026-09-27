# Qwen 3.6 Swap — Live Validation Checklist (Phase 6)

**Status: DEFERRED — not yet executed.** The offline phases (1–4) are committed on
branch `qwen3.6-35b`. Everything below requires a running `llama-server`, so it is
deliberately deferred until the owner authorizes starting it.

Run these in order after starting the server with `start.bat` (or the daily
`E:\Models\run_qwen3.6_35b_v2.bat`). Record actual numbers in this file as each
step is done; promote to `summary.md` when green.

---

## Environment under test

| Item | Value |
|---|---|
| Engine binary | `E:\Models\beellama-v0.4.7-bin-win-cuda-12.4-x64\llama-server.exe` (BeeLlama = llama.cpp fork with KVarN) |
| Model | `E:\Models\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf` (MoE, 3B active) |
| Vision | `E:\Models\Qwen3.6-35B-A3B-mmproj-F16.gguf`, served with `--no-mmproj-offload` (runs from RAM) |
| Template | `E:\Models\qwen36_chat_template.jinja` (froggeric v22.5 fix) |
| Flags | `--n-gpu-layers 99 --n-cpu-moe 26 --flash-attn on --cache-type-k kvarn4 --cache-type-v kvarn2 --image-min-tokens 1024 --kv-tail-tokens 1024 --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-p-min 0.75 --reasoning off --ctx-size 60000 --parallel 1 --threads 8 --batch-size 1024 --ubatch-size 512` |
| Brain config | `config.N_CTX = 60000` (must match `--ctx-size`), `MODEL_NAME = Qwen3.6-35B-A3B-UD-IQ4_XS` |
| Reference bench (BeeLlama v0.4.4, 172 tok/s pp / 31.9 tok/s decode) | superseded by v0.4.7 measurements below |

---

## T1 — Boot + server props

```powershell
.\start.bat
Invoke-RestMethod http://localhost:8080/props | ConvertTo-Json -Depth 3   # model path, n_ctx
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
```

Expected:
- health 200 within ~60 s; `n_ctx` = **60160** (60000 + vision reserve)
- VRAM ≈ **11.5–11.9 GiB / 12.3 GiB**; RAM working set ≈ 7 GB + mmproj
- boot log shows `KVarN is target-context-only…` for the MTP draft context, and a
  `draft acceptance` line well above 0.5 during use (v0.4.7 measured ~0.87–0.92)

Fail conditions: CUDA OOM at load → lower `--n-cpu-moe` is NOT possible (VRAM-bound);
instead close desktop VRAM users. `bad allocation` during prefill → RAM/commit exhausted;
close apps or lower `--ubatch-size` to 256.

## T2 — Vision round-trip (**highest priority: the `<image>` marker injection was removed**)

```powershell
python - <<'PY'
import base64, io, requests
from PIL import Image
img = Image.open(r"engine_testing\assets\sample_screen.png"); img.thumbnail((1024,1024))
buf = io.BytesIO(); img.save(buf, format="JPEG", quality=85)
b64 = base64.b64encode(buf.getvalue()).decode()
r = requests.post("http://localhost:8080/v1/chat/completions", json={"model":"local","messages":[{"role":"user","content":[{"type":"text","text":"Describe this screen in 3-4 sentences, naming apps."},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}]}],"max_tokens":400}, timeout=300)
print(r.status_code); print(r.json()["choices"][0]["message"]["content"][:600])
PY
```

Expected: HTTP 200 and an **accurate** description (Phase 0 got GothamChess/Chess.com/OpenCode
correct on the downscaled frame). Also re-test **full-res 1920×1080** (worked at 60k/ncmoe 20
before the marker removal).

Gates:
- If the server returns `mtmd_batch_encode: bad allocation` → RAM pressure, downscale or free RAM.
- If the description is empty/garbled or the image is ignored → **the `<image>` marker removal
  is implicated** → re-inspect `core/brain.py::_execute_llm_completion` and the froggeric
  template's image handling (the template must place vision tokens itself).
- Also send one image inside history (assistant reply + follow-up question) to confirm
  `purge_media_cache` still tombstones correctly with no `<image>` marker present.

## T3 — Native tool calling + the known failure family

```powershell
python engine_testing/run_engine_test.py --model Qwen3.6-35B-A3B-UD-IQ4_XS --preset baseline
```

Then manually re-test the six Phase-0 "narrate instead of act" prompts, which were the
residual defect (temp 1.0 fixed 5 of 10 auto-fails; these six remained):

| Prompt | Phase-0 behavior (temp 1.0) | Desired |
|---|---|---|
| What time is it? | answered "$2:37 AM, Boss" (fabricated) | calls `get_current_time` |
| Pause Spotify. | "Consider it done, Boss." | calls `pause_spotify` |
| Skip this song. | "Consider it done, Sir." | calls `skip_spotify_track` |
| Take a screenshot. | "I cannot take screenshots…" | calls `look_at_screen` |
| Take a screenshot and describe what you see. | capability denial | calls `look_at_screen` |
| Set a 10 minute timer right now. | "The timer is set, Sir." | calls `set_timer` |

If the family persists → the Phase-2 lever is **tool-law wording** in
`Aster_Vault/System_Prompts/_shared_tool_laws.md` (e.g. "when a tool exists for the action,
call it; never narrate an action you did not execute"), A/B'd on the harness battery.
Note the production loop's `_claims_tool_execution` single-retry already catches the
explicit claims ("The timer is set").

## T4 — Voice note → Faster-Whisper route (Phase 3)

```powershell
ffmpeg -y -i <any .ogg voice note> -ar 16000 -ac 1 -f wav sample_voice.wav
python -c "from local_stt import transcribe_file, format_transcript; t=transcribe_file(r'sample_voice.wav'); print(format_transcript(t, speaker='Mohamed', mood='calm'))"
```

Expected: transcript text with "Esther"→"Aster" correction; first call logs
`[Aster Ears] Booting CPU Faster-Whisper (medium.en, 8-bit)…` and costs **zero VRAM**
(~1.5 GB RAM, stays resident); subsequent calls skip the cold load.
Then send a real Telegram voice note → expect `[Speaker: X] [Mood: Y] text` reaching the
brain, no `input_audio` anywhere in the server log, and no `[NATIVE_AUDIO_PAYLOAD]` on the
inbound side (that marker is outbound-TTS only now).

Edge cases: silent clip → reply "I could not make out any speech…", no empty turn;
unknown speaker → temp WAV stashed + `/enroll` hint.

## T5 — Harness regression gate (compare against Phase 0)

| Run | Phase-0 baseline |
|---|---|
| `--preset baseline` (temp 1.0) | **31/37 auto (84%)**, 0.0% text leaks, 0.0% JSON errors, loop discipline 5/5 |
| default (temp 0.7) | 27/37 auto (73%) |

Report path: `engine_testing/results/engine_report_<model>_<ts>.txt`.
Gate: **no regression worse than 2 points** on either run, plus a green
`python -m pytest tests/ -q` (408 expected) with the server up (tests are mocked, but
this catches any import-time coupling).

## T6 — Full `main.py` boot smoke (last; needs Telegram)

```powershell
python main.py
```

Expected boot lines: Tesseract executable, facial-recognition boot, `Kokoro TTS 82M …
registered (lazy)`, **69 tools registered**, `llama-server reachable`, warmup success.
Then via Telegram: `/status` (context estimate against 60000), `/peek 5`, one typed turn,
one tool turn (e.g. "what time is it"), one vision turn, one voice note.
Safety: never start a second `main.py` (daemons + face-server port 8000 collide).

---

## Results log

### 2026-09-27 — T1 PASS
BeeLlama v0.4.7 + Qwen 3.6 35B-A3B + mmproj-F16 + froggeric template, `--ctx-size 60000`.
Health in **8 s**; `/props` `n_ctx = 60160` (60k + vision reserve) ✓. VRAM **11.6 GiB / 12.3**
(PID 40076). RAM free 9.1 GB, commit 34.4/38.7 GB. Boot log: `creating MTP draft context…`,
`loaded multimodal model '…mmproj-F16.gguf'`, `model loaded`, `listening on http://0.0.0.0:8080` ✓.

### 2026-09-27 — T2 PASS (vision round-trip after `<image>` marker removal)
- downscaled 1024×576 JPEG: HTTP 200, 27 s, accurate (VS Code, `scenarios.py`, the
  `Aster-localization` workspace, terminal running `run_engine_test.py`) ✓
- **full-res 1920×1080 PNG: HTTP 200, 62 s, accurate** ✓ (no `mtmd_batch_encode` errors)
- image-in-history + follow-up: HTTP 200, 30 s, and it correctly read the VS Code tab bar
  (`Personality-systems.md`, `summary.md`, `scenarios.py`) ✓
→ **The removed marker injection is NOT a regression.** Template places vision tokens itself.

### 2026-09-27 — T3 FAIL (known family confirmed live; the one open defect)
Live system prompt (14,566 chars) + 69 tools, temp 1.0:

| Prompt | Result |
|---|---|
| What time is it? | ✗ no call — "The time is 2:29 AM, Boss." (fabricated) |
| Pause Spotify. | ✗ no call — "Consider it done, Sir." |
| Skip this song. | ✗ no call — "Consider it done, Sir." |
| Take a screenshot. | ✗ no call — capability denial |
| Take a screenshot and describe what you see. | ✓ `look_at_screen()` |
| Set a 10 minute timer right now. | ✗ no call — "The timer is set, Sir." |
| Round-trip (`get_current_time` → `role:"tool"` → final) | ✓ "Seven fifty-five in the evening, Sir." |

→ Native tool calling works and the `role:"tool"` feedback path is correct; the defect is
**tool avoidance on terse imperative prompts** (answered in persona). Lever: tool-law
wording in `Aster_Vault/System_Prompts/_shared_tool_laws.md`, A/B'd on this battery.

### 2026-09-27 — T4 PASS (Whisper voice-note route)
`H:\Voice-Testing\Farah.wav` → ffmpeg 16 kHz mono → `local_stt.transcribe_file`:
CPU model booted (`[Aster Ears] Booting CPU Faster-Whisper (medium.en, 8-bit)…`),
transcribe **11.0 s cold / 5.6 s warm**, identical runs, transcript accurate.
`format_transcript` → `[Speaker: Farah] [Mood: calm] <text>` ✓
**VRAM unchanged (+3 MiB)**; ~1.5 GB RAM stays resident after first use.

### 2026-09-27 — T5 harness regression: NO REGRESSION (within single-sample noise)
| Run | Phase-0 (pre-cleanup) | Post-cleanup (this build) |
|---|---|---|
| default (temp 0.7) | 27/37 (73%) | **33/37 (89%)** |
| `--preset baseline` (temp 1.0) | 31/37 (84%) | **29/37 (78%)** |
| mean | 29/37 (78%) | **31/37 (84%)** |

Reports: `engine_testing/results/engine_report_Qwen3.6-35B-A3B-UD-IQ4_XS_20260927_201345.txt`
and `…_20260927_202815.txt`. Generation **42.2 / 39.2 tok/s** (vs 36.4 in Phase 0 — the
v0.4.7 + threads-8 + ub-512 tuning shows). Each scenario is **one sample**, so ±3 scenarios
is noise; the paired mean improved by 2 and the temp-0.7 run improved by 6. If a tighter
number is wanted, average 3 seeds per temp. Complementary: `pytest tests/ -q` = **408 passed**.

### Not run
- **T6** (`main.py` boot smoke + Telegram turns) — left to the owner; it brings the live
  daemons (Telegram polling, mic, face server) online and needs interactive judgement.

### Session notes
- RAM headroom is the tight resource, not VRAM: with the CPU Whisper fallback resident,
  free RAM fell to ~2.3 GB. After the `--n-cpu-moe 26` rebalance (2026-09-27) voice notes
  use the CUDA singleton instead (~1.2 GB VRAM, verified resident with 1.4 GB headroom).
- The server was stopped after this run (owner instruction).

### 2026-09-27 (later) — T3 FIX A/B: tool-law wording, and the Whisper VRAM rebalance

**Rebalance (task 1):** `--n-cpu-moe 20 → 26` frees VRAM for a *resident* Faster-Whisper.

| n-cpu-moe | llama-server VRAM | + Whisper CUDA | free after both |
|---|---|---|---|
| 20 | 11,878 MiB | (would OOM) | ~0 |
| 24 | 10,233 MiB | 11,489 MiB | 799 MiB |
| **26 (shipped)** | **9,635 MiB** | **10,867 MiB (transcribe peak)** | **1,421 MiB** |

Whisper measured at **1,182 MiB**; `local_stt.transcribe_file` now prefers the CUDA
singleton (CPU is the fallback), so calls and voice notes share one resident instance.

**A/B (task 2):** added the `INSTANT ACTIONS — CALL, NEVER NARRATE, NEVER DENY` law to
`Aster_Vault/System_Prompts/_shared_tool_laws.md` (terse-command → exact-tool table, banned
narrations, "never write a tool result yourself", capability-denial ban, "now" is part of
the command).

Sharp probe (the 6 stubborn prompts), same server/config:

| | Phase-6 (no law) | law v1 | law v2 (shipped) |
|---|---|---|---|
| tool calls emitted | 1/6 | 4/6 | **6/6** |

Harness battery:

| Run | Phase 0 (pre-cleanup) | post-cleanup, no law | **+ tool law (final)** |
|---|---|---|---|
| temp 0.7 (default) | 27/37 (73%) | 33/37 (89%) | **34/37 (92%)** |
| temp 1.0 (`--preset baseline`) | 31/37 (84%) | 29/37 (78%) | **35/37 (95%)** |

Reports: `…_20260927_210200.txt` (temp 1.0), `…_20260927_211553.txt` (temp 0.7).
→ **T3 defect materially fixed** (both temps now above every prior baseline); the residual
two auto-fails are in other categories. Loop tendency still rises at low temp (C6-01: 8
`browse_web` calls in 9 rounds at 0.7 vs 5 in 6 at 1.0) — temp 1.0 remains the keeper.

### 2026-09-27 — T6 (application smoke) RUN: vision + voice-note green; time handling had two distinct causes

Boot (main.py, server at `--n-cpu-moe 26`): every expected line present — Tesseract, learned
face, Kokoro lazy, **69 tools**, ECAPA voice learned, `llama-server reachable`, warmup via
native REST, Telegram + LiveKit bridges, awareness daemon, face server :8000, watchdog,
dashboard, `All systems online. Entering CLI mode.` ✅

Telegram turns:
| Turn | Result |
|---|---|
| text / image (photo) | ✅ **vision perfect through the full brain path** ("a selfie… clean-shaven man with blue eyes, grey t-shirt, wooden door, warm lighting") |
| voice note | ✅ **Phase-3 route perfect**: ffmpeg → Whisper → `[Mood: neutral]` tag → brain; transcript exact; unknown speaker correctly prompted `/enroll` (enrolled voice is "Mohamed", clip was another speaker) |
| "What time is it?" (first) | ✅ called `get_current_time` → tool returned `2026-09-27 21:41:16` → "Nine forty-one in the evening, Sir." |
| "Wait what time is it?" (repeat) | ❌ **no new call** — reused/garbled the previous turn's result into "Nine fifty-two, Sir." |

**Cause 1 (not a bug):** `AWARENESS_ACTIVE = False` on this install, so no `CURRENT CONTEXT`
time block is injected — the tool is the only time source. (Verified: `Local time:` absent
from every logged payload; the earlier "CURRENT CONTEXT" grep hit was the *tool-laws text*
mentioning the tag, not an injected block.)

**Cause 2 (fixed):** the model treated the previous turn's tool result as current. Added a
staleness clause to `_shared_tool_laws.md`: *"Results from earlier turns are STALE … asking
'what time is it?' twice must call `get_current_time` both times"*. Repeat-question probe
(fresh brain, temp 1.0): 2/3 trials re-called correctly (trial 1 showed the known
first-call flakiness — no call on the *first* question).

**Residual (open):** first-call flakiness on terse prompts at some rate (~1/3 in this small
sample; matches harness `C1-06` flakiness). Levers if it matters: more sampling/retries, or
constrained decoding on the first tool call.
