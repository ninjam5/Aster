---
name: local-llm-reference
description: Load this for local-LLM domain knowledge as it applies to Aster - GGUF and quantization (Q4_K_M vs QAT vs KV-cache q4_0), context-window/VRAM math, the Gemma 4 chat template and why it has no system role, the custom Jinja template and <image>/<__media__> markers, llama-server API endpoints, sampling parameters, token-counting heuristics, and the supporting local model zoo (Whisper, Kokoro, ECAPA, emotion models). Load when a term like "mmproj", "KV cache", "min_p", "flash attention", or "quant" needs to be understood or explained.
---

# Local-LLM Reference (as applied in Aster)

Domain theory a mid-level engineer or Sonnet session typically lacks, grounded in
this repo's actual files. Not a textbook — every concept is tied to where it bites
here.

**When NOT to use this skill:** repo invariants → `aster-architecture-contract`;
operating commands → `aster-run-and-operate`; VRAM budgets/policy →
`aster-vram-discipline`.

## GGUF and quantization

- **GGUF** — llama.cpp's model container format (weights + metadata + chat
  template). Aster's primary documented model: `gemma-e4b-q4km.gguf` (5.41 GB).
- **Quantization** — storing weights in fewer bits. The names:
  - **Q4_K_M** — 4-bit "K-quant", medium variant; mixed-precision blocks that keep
    sensitive tensors at higher precision. The workhorse for 12 GB cards.
  - **QAT** (quantization-aware training) — the model was *trained* knowing it
    would be quantized; usually better quality at the same bit-width than post-hoc
    quantization. The repo swept `E2B-QAT`, `E4B-QAT`, and 12B builds in June 2026
    (`engine_testing/results/`), and the newer launchers serve
    `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf`.
  - **IQ4_XS** — importance-matrix 4-bit, extra-small; more compression, more
    quality risk (a 12B IQ4XS was swept once).
- **Gemma-4 "E4B"/"E2B"** — effective-4B / effective-2B parameter MatFormer
  variants; E4B is the daily model, E2B the low-VRAM tier (idle budget ~4 GB).
- Rule of thumb: 4-bit weights ≈ 0.55–0.65 GB per billion params, plus KV cache,
  plus the vision projector.

## KV cache and context math

- The **KV cache** stores every past token's key/value tensors per layer — it is
  why long chats consume VRAM beyond the weights, and grows linearly with context
  length.
- **`-ctk q4_0 -ctv q4_0`** quantizes the cache itself to 4-bit — the single trick
  that makes 128k context fit next to the weights on 12 GB (~1.5–2 GB for 128k per
  summary.md, vs ~4× that at fp16).
- Grounded data point (the only measured one): **E4B + 128k ctx + Q4_0 KV ≈ 5–6 GB
  total** on the 3080 (`packaging.md`, `checklist_test.md`). E2B/12B footprints
  were never recorded — don't invent them.
- **Flash attention** (`-fa on`) — faster/leaner attention kernels; required for
  the above numbers.
- **`--slot-save-path`** — persists server-side KV slots to disk
  (`Aster_Vault/kv_cache/`) so a restart can restore cached prefixes.
- **Context sizing**: server `--ctx-size 128000` must equal `config.N_CTX`
  (default 131072 — a live mismatch; the brain's trim budget is
  `int(N_CTX*0.9)` estimated tokens, so an optimistic N_CTX can overrun the
  server).

## The Gemma 4 chat template (the project's scar tissue)

- Gemma's native turn grammar is `<start_of_turn>user … <end_of_turn>` /
  `<start_of_turn>model …`. **It has no system role.** Layers that pretend
  otherwise fail in one of three documented ways (dropped system content, foreign
  `USER:/ASSISTANT:` grammar, ChatML clash) — see `aster-failure-archaeology`
  §engine-hell before touching anything template-adjacent.
- **The custom template** `Aster_Vault/gemma4-multimodal.jinja` (passed via
  `--chat-template-file`) does two load-bearing jobs:
  1. **Vision markers:** each image turn carries an `image_url` content block PLUS
     a literal `<image>` text block; the template converts that into the
     `<__media__>` token the mmproj pipeline expects, at the right position.
  2. **Native tool grammar:** renders the OpenAI-format `tools` array into Gemma's
     own `<|tool_call>call:name{...}<tool_call|>` syntax; llama-server parses the
     model's output back into a structured `tool_calls` field.
- llama-server boot logs `detected an outdated gemma4 chat template, applying
  compatibility workarounds` — **expected and harmless**.
- Legacy INST tokens (`<start_of_turn>user\nINST`) in trailing assistant messages
  conflict with the template and are stripped before sending.

## llama-server API surface (what the brain actually uses)

| Endpoint | Use here |
|---|---|
| `POST /v1/chat/completions` | THE endpoint — all chat/vision/tool traffic (OpenAI format) |
| `GET /health` | Readiness probe |
| `GET /props` | Model metadata — includes which GGUF is loaded (use to resolve launcher ambiguity) |
| `POST /completion` | Raw endpoint — used only by `config.load_engine()`'s reachability check |

Native tool calling: request carries `tools=[{"type":"function","function":{...}}]`;
response message carries `tool_calls=[{id, function:{name, arguments}}]`; the
follow-up turn feeds results back as `{"role":"tool", "tool_call_id": …, "content": …}`.

## Sampling parameters (and where each is set here)

| Param | Meaning | Aster's values |
|---|---|---|
| `temperature` | Randomness of token choice | Main conversation: `LLM_TEMPERATURE` default **1.0** (config.py). Vision description / re-examine / compaction: **0.2** (deterministic). `engine_testing/harness.py` uses 0.7 (its "must match brain.py" comment has drifted). `conversation_testing.py` sweeps presets. |
| `top_p` | Nucleus cutoff — sample only from the smallest set of tokens whose probability sums to p | 0.95 default |
| `top_k` | Only the k most likely tokens are candidates | 64 default |
| `min_p` | Drop tokens below a fraction of the top token's probability — a quality floor that adapts to confidence | Not set by default (server default applies); swept in conversation_testing presets |
| `repeat_penalty` | Penalizes recently-emitted tokens | Not set by default; a candidate lever against degenerate loops (see `aster-gemma-reliability-campaign`) |

Persona *style* is tuned by sweeping these against a fixed prompt battery
(`conversation_testing.py`) — never by vibes on one reply.

## Token counting

No local tokenizer exists (REST-only), so everything uses the **~4 characters per
token heuristic** (`core/memory.py:180`, `len(content)//4` per message). Used by
`trim_memory` (budget 90% of N_CTX) and `/status`. Error bars: fine mid-session,
meaningful near the context ceiling; code and JSON tokenize denser than 4 chars/tok,
so the estimate typically UNDER-counts them.

## Supporting local model zoo (device + residency policy per `aster-vram-discipline`)

| Model | Job | Device / residency |
|---|---|---|
| Faster-Whisper `medium.en` int8 (CTranslate2) | STT (Telegram + call) | CUDA, resident shared singleton |
| Kokoro 82M (`KPipeline`) | TTS, 24 kHz | CUDA w/ CPU fallback; ref-counted, offloads idle |
| Silero VAD | Speech/silence gating on the call | via livekit-plugins-silero |
| openWakeWord (`hey_jarvis`) | Wake phrase, agent sleeps otherwise | CPU, tiny |
| SpeechBrain ECAPA-TDNN | Speaker ID (`[Speaker:]`), owner-gating | CUDA, eager (grandfathered) |
| j-hartmann distilroberta (7→5 labels) | Text emotion (Tier 0) | CPU, eager |
| SpeechBrain wav2vec2 IEMOCAP (4→4 labels) | Voice emotion (Tier 1); separate CPU copy for ambient (1b) | CUDA ref-counted / CPU resident |
| hsemotion-onnx AffectNet-8 (EfficientNet-B2) | Face emotion (Tier 2, opt-in) | CPU (onnxruntime) |
| OmniParser v2 icon_detect (YOLOv8) | UI element fallback detector | CUDA, loaded per use, released after |
| face_recognition (dlib) | Face ID, crops | CPU/eager at import |

## Glossary quick-fire

**mmproj** — multimodal projector GGUF that maps image embeddings into the LLM's
token space; loaded alongside the model (`--mmproj`); without it image turns fail.
**-ngl 99** — offload (up to) 99 layers to GPU = everything. **int8 (CTranslate2)**
— 8-bit inference quantization for Whisper, ≈half the VRAM of fp16.
**Barge-in** — user speech interrupts TTS playback mid-response (epoch-cancelled).
**ReAct loop** — reason→act→observe cycling; Aster's is capped at 15 rounds.

## Provenance and maintenance

Authored 2026-07-05.

- Which model/ctx is live: `Invoke-RestMethod http://localhost:8080/props`
- Sampling defaults: `Select-String -Path config.py -Pattern "LLM_TEMPERATURE|LLM_TOP"`
- Template flags: `Select-String -Path start.bat,start_new.bat -Pattern "chat-template|ctk|ctv|fa"`
- Heuristic unchanged: `Select-String -Path core\memory.py -Pattern "// 4"`
