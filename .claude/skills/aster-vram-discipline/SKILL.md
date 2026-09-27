---
name: aster-vram-discipline
description: Load this whenever a change touches ML models, GPU memory, or performance on the Aster project - adding/removing/moving a model, choosing CPU vs CUDA, lazy vs eager loading, investigating VRAM creep or OOM, or verifying the idle-memory budget. Triggers - "out of memory", "CUDA OOM", "VRAM", "should this model be preloaded", "llama-server crashed while X was running", "PC got slow during a call".
---

# Aster VRAM Discipline

The owner's #1 standing rule (stated 2026-07-05): **Aster must be as optimized as
possible. 12 GB (RTX 3080) is a hard ceiling. On the Qwen 3.6 35B-A3B engine
llama-server alone measures ~9.4 GB model + ~1.2 GB resident Whisper / 12.3 GB at `--n-cpu-moe 26` + 60k ctx —
the vision mmproj is served from RAM, not VRAM, so headroom is tight.** New ML models
are **lazy-loaded and offloaded when idle** via ref-counting — not eagerly preloaded.
(This *reverses* an older 2026-05 eager-preload preference; existing eager loads are
grandfathered — don't churn them without asking the owner.)

**When NOT to use this skill:** general failure triage → `aster-debugging-playbook`;
quantization/KV-cache theory → `local-llm-reference`; shipping the change →
`aster-change-control` (VRAM measurement is one of its gates).

## Budget table

The llama-server figure below is **measured** (2026-09-27, `summary.md` §3.3); the
other components are estimates. Treat estimates as planning numbers; always confirm
with `nvidia-smi`.

| Component | VRAM | Residency |
|---|---|---|
| llama-server: Qwen 3.6 35B-A3B IQ4_XS (MoE, `--n-cpu-moe 26`), 60k ctx, KVarN KV + mmproj in RAM | **~11.5–11.9 GB / 12.3 GB (measured 2026-09-27)** | Permanent while server runs |
| Faster-Whisper `medium.en` int8 (shared singleton) — call path | ~1.0–1.5 GB (est.) | Loaded at startup, resident for calls |
| Faster-Whisper CUDA singleton — calls + Telegram voice notes (`local_stt.transcribe_file`) | ~1.2 GB VRAM (measured) | Resident; VRAM reserved by `--n-cpu-moe 26` (CPU instance is the fallback) |
| SpeechBrain ECAPA speaker-ID (eager CUDA, grandfathered) | ~80–200 MB (est.) | Resident |
| Silero VAD | ~50 MB (est.) | Resident (bridge) |
| openWakeWord | ~0 (CPU) | — |
| Kokoro TTS 82M (ref-counted) | ~0.3–0.6 GB (est.) | **Only while held**; offloads at 0 users |
| Voice-emotion IEMOCAP CUDA copy (ref-counted) | ~200–300 MB (est.) | Only while held (call / voice note) |
| OmniParser v2 YOLO | ~200 MB (est.) | **Released immediately after each fallback use** (`tools/vision.py:388 _release_omniparser`, called at :593 and :719) |
| Text/face/ambient emotion models | 0 VRAM (CPU by design) | Resident (CPU) |

The measured Qwen 3.6 35B-A3B stack above is the grounded number on the 3080. The
old Gemma E4B ~5–6 GB figure is archived with the Gemma branch
(`gemma-4-e4b-lightweight`). Do not invent footprints for untested models.

## The ref-count pattern (canonical implementation: `tools/audio.py`)

Shape to copy for ANY new model:

```python
_model = None
_users = 0
_lock = threading.RLock()

def acquire_x():
    global _model, _users
    with _lock:
        if _model is None:
            # lazy import + load; try CUDA, fall back to CPU rather than dying
            _model = load(...)
        if _model is not None:
            _users += 1
        return _model

def release_x():
    global _model, _users
    with _lock:
        if _users > 0: _users -= 1
        if _users <= 0 and _model is not None:
            _model = None; _users = 0
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available(): torch.cuda.empty_cache()
            except Exception: pass
```

Live examples to study:
- `tools/audio.py` `acquire_kokoro_pipeline`/`release_kokoro_pipeline` — includes
  CUDA→CPU fallback so voice is never silently lost; one-shot users
  (`generate_kokoro_voice`, `synthesize_preview`) release in `finally`.
- `tools/emotion_recognition.py` `acquire_voice_emotion_model`/`release_...` —
  LiveKit's `LocalWhisperSTT.__init__` takes an outer hold for the whole call;
  `aclose()` releases; Telegram voice notes load→classify→offload per note.
- `tools/vision.py` `_release_omniparser` — ephemeral: freed right after each use.

Rules that make ref-counting actually work:
1. Every `acquire` has a guaranteed `release` (use `try/finally`).
2. Long-lived consumers (a live call) hold ONE outer reference for their lifetime;
   don't acquire per-utterance.
3. `release` must `gc.collect()` + `torch.cuda.empty_cache()` or the VRAM stays
   allocated to the process.
4. Never instantiate a private copy of a model that already has a shared singleton
   (Whisper: `local_stt.get_whisper_model()` serves BOTH Telegram and LiveKit).

## Adding a new model — checklist

1. Default `device="cuda"` only if latency demands it AND the budget holds;
   CPU is correct for ambient/background classifiers (precedent: ambient-audio
   IEMOCAP runs a separate CPU copy specifically to spare VRAM).
2. Implement acquire/release as above; lazy import the heavy package inside the
   loader so `import` of the module stays cheap.
3. Measure **before** (idle), **after load** (active), and **after release** —
   the third number must return to baseline:
   ```powershell
   nvidia-smi --query-gpu=memory.used,memory.total --format=csv
   nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
   ```
4. Check idle total against the budget: llama-server alone is ~11.5–11.9 GB of the
   12.3 GB board on the Qwen engine, so anything eager will not fit.
5. Record the measured numbers in your change notes (gate in
   `aster-change-control` rule 3).

## Leak trap catalog

| Trap | Symptom | Fix / defense | Story |
|---|---|---|---|
| Per-call model instantiation | VRAM stair-steps up with each call/voice note | Shared singleton or ref-count | Duplicate Whisper+Kokoro copies during calls were a real past leak; fixed by the shared instances |
| Base64 images retained in message history | VRAM/KV grows every image turn; slow prompts | `purge_media_cache()` runs after every turn — never bypass it; images re-enter only via `re_examine_image` | Cold-storage tombstone design exists because of this |
| LiveKit devmode subprocess | TWO python.exe processes with multi-GB **RAM** (not VRAM) footprints after a call connects | Check: `Get-Process python \| Format-Table Id, WorkingSet64`. Candidate fix: `devmode=True → False` at `webrtc_bridge.py:687` — OPEN item, test carefully (changes worker lifecycle) | Documented in CLAUDE.md (which cites old line 668) |
| Missing release on an error path | Model never offloads after an exception | `try/finally` around acquire/release | Kokoro's one-shot users demonstrate the pattern |
| Eager import chains | A "CPU-only" module import drags in torch-CUDA weights at boot | Lazy imports inside loader functions | Kokoro deliberately prints "lazy — loads on first use" at registration |
| KV cache growth | First-token latency grows with long history | trim_memory budget (90% of N_CTX); `/compact`; ctx-size vs N_CTX check (start.bat 60000 vs `config.N_CTX` 60000) | See `aster-architecture-contract` weak points |

## Measurement quick reference

```powershell
# Board totals + per-process:
nvidia-smi
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
# From Telegram: /gpu   (uses nvidia-smi; torch.cuda would miss llama-server & Whisper)
# RAM-side duplicate-process check (devmode):
Get-Process python | Format-Table Id, WorkingSet64
```

Healthy idle (Qwen engine, no call): board usage ≈ 11.5–11.9 GB dominated by
llama-server; exactly one python.exe with a large working set; Kokoro/voice-emotion
NOT resident.

## Provenance and maintenance

Authored 2026-07-05. Budgets are owner-stated (2026-07-05 Q&A); component estimates
from summary.md; Qwen 3.6 35B-A3B llama-server footprint measured 2026-09-27.

- Ref-count implementation drift: `Select-String -Path tools\audio.py -Pattern "_kokoro_users"`
- OmniParser release still in place: `Select-String -Path tools\vision.py -Pattern "_release_omniparser"`
- devmode still True: `Select-String -Path webrtc_bridge.py -Pattern "devmode"`
- Live idle measurement: `nvidia-smi --query-gpu=memory.used --format=csv` with Aster idle
