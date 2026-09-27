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
| Flags | `--n-gpu-layers 99 --n-cpu-moe 20 --flash-attn on --cache-type-k kvarn4 --cache-type-v kvarn2 --image-min-tokens 1024 --kv-tail-tokens 1024 --spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-p-min 0.75 --reasoning off --ctx-size 60000 --parallel 1 --threads 8 --batch-size 1024 --ubatch-size 512` |
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

_(append results here as tests are run — date, command, observed, verdict)_
