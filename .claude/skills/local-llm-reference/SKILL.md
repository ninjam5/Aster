---
name: local-llm-reference
description: Load this for local-LLM domain knowledge as it applies to Aster - GGUF and quantization (Q4_K_M vs QAT vs IQ4_XS vs KVarN KV), context-window/VRAM math, the Qwen 3.6 froggeric chat template, MTP speculative decoding, llama-server API endpoints, sampling parameters, token-counting heuristics, and the supporting local model zoo (Whisper, Kokoro, ECAPA, emotion models). Load when a term like "mmproj", "KV cache", "KVarN", "MTP", "min_p", "flash attention", or "quant" needs to be understood or explained.
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
  template). Aster's engine model: `E:\Models\Qwen3.6-35B-A3B-UD-IQ4_XS.gguf`
  (17.0 GB, MoE).
- **Quantization** — storing weights in fewer bits. The names:
  - **Q4_K_M** — 4-bit "K-quant", medium variant; mixed-precision blocks that keep
    sensitive tensors at higher precision. The workhorse for 12 GB cards.
  - **QAT** (quantization-aware training) — the model was *trained* knowing it
    would be quantized; usually better quality at the same bit-width than post-hoc
    quantization. The repo swept `E2B-QAT`, `E4B-QAT`, and 12B builds in June 2026
    (`engine_testing/results/`) on the Gemma-era engine.
  - **IQ4_XS** — importance-matrix 4-bit, extra-small; more compression, more
    quality risk. **This is the Qwen 3.6 swap's daily quant** — viable on the 35B
    MoE because only ~3B params are active per token.
- **Qwen 3.6 35B-A3B** — a Mixture-of-Experts model (36B total, ~3B active
  params/token). The MoE split lets 26 expert layers sit on CPU (`--n-cpu-moe 26`)
  while attention/dense stay on GPU.
- Rule of thumb: 4-bit weights ≈ 0.55–0.65 GB per billion params, plus KV cache,
  plus the vision projector.

## KV cache, KVarN, MTP, and context math

- The **KV cache** stores every past token's key/value tensors per layer — it is
  why long chats consume VRAM beyond the weights, and grows linearly with context
  length.
- **KVarN** (BeeLlama's KV quantization; `--cache-type-k kvarn4 --cache-type-v
  kvarn2 --kv-tail-tokens 1024`) — quantizes the cache into a very-low-precision
  budget (the `kvarn2`–`kvarn8` range; higher number = more bits) while keeping a
  **precision tail** (`--kv-tail-tokens 1024`) at full precision for the most recent
  tokens. ~0.9 GB at 60k context. Grounded: **Qwen 3.6 35B-A3B + 60k + KVarN ≈
  9.4 GB model + ~1.2 GB resident Faster-Whisper / 12.3 GB** on the 3080 (measured).
- **MTP speculative decoding** (`--spec-type draft-mtp --spec-draft-n-max 3
  --spec-draft-p-min 0.75`) — the GGUF carries multi-token-prediction draft heads,
  so no separate draft model is loaded; measured draft acceptance ≈0.90.
- **Flash attention** (`--flash-attn on`) — faster/leaner attention kernels; required
  for the above numbers.
- **`--no-mmproj-offload`** — the vision projector is served from RAM, not VRAM.
- **Context sizing**: server `--ctx-size 60000` must equal `config.N_CTX` (60000);
  the brain's trim budget is `int(N_CTX*0.9)` estimated tokens.

## The Qwen 3.6 chat template (froggeric v22.5)

- Aster passes `E:\Models\qwen36_chat_template.jinja` via `--chat-template-file`
  (with `--jinja`). This is the **froggeric v22.5** fixed template
  (`froggeric/Qwen-Fixed-Chat-Templates`), not Qwen's official template.
- What it fixes:
  - the **`|items` tool-argument bug** — Qwen's official template used a Jinja
    `|items` filter that breaks tool-argument rendering in C++ (llama.cpp's) Jinja
    runtime, mangling the `arguments` JSON.
  - **empty `<think>` blocks** — the official template emits empty thinking blocks
    that fill context for no benefit.
- Thinking is disabled server-side (`--reasoning off`), so responses arrive as plain
  `content` with no `reasoning_content`.
- **No vision markers needed.** The template places the vision tokens itself from
  the `image_url` content blocks; Aster no longer injects an `<image>`/`<__media__>`
  marker (that Gemma-era mechanism was removed in the swap).
- **No system-role limitation.** Unlike Gemma, Qwen supports a system role natively,
  so the persona system prompt is a normal `role:"system"` message.
- See `aster-failure-archaeology` §engine-hell before touching anything
  template-adjacent.

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
token heuristic** (`core/memory.py:391`, `len(content)//4` per message). Used by
`trim_memory` (budget 90% of N_CTX) and `/status`. Error bars: fine mid-session,
meaningful near the context ceiling; code and JSON tokenize denser than 4 chars/tok,
so the estimate typically UNDER-counts them.

## Supporting local model zoo (device + residency policy per `aster-vram-discipline`)

| Model | Job | Device / residency |
|---|---|---|
| Faster-Whisper `medium.en` int8 (CTranslate2) | STT call + Telegram voice notes (one CUDA singleton via `local_stt.transcribe_file`) | ~1.2 GB VRAM, resident; CPU instance is the fallback only |
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

**mmproj** — multimodal projector GGUF (`E:\Models\Qwen3.6-35B-A3B-mmproj-F16.gguf`)
that maps image embeddings into the LLM's token space; loaded via `--mmproj` with
`--no-mmproj-offload` (served from RAM). Vision-only — without it image turns fail.
**-ngl 99** — offload (up to) 99 layers to GPU = everything. **int8 (CTranslate2)**
— 8-bit inference quantization for Whisper, ≈half the VRAM of fp16.
**KVarN** — BeeLlama's KV-cache quantization (`kvarn2`–`kvarn8`); `--kv-tail-tokens`
keeps a full-precision tail for recent tokens. **MTP** — multi-token-prediction draft
heads inside the GGUF (`--spec-type draft-mtp`), used for speculative decoding with
no separate draft model.
**Barge-in** — user speech interrupts TTS playback mid-response (epoch-cancelled).
**ReAct loop** — reason→act→observe cycling; Aster's is capped at 15 rounds.

## Provenance and maintenance

Authored 2026-07-05.

- Which model/ctx is live: `Invoke-RestMethod http://localhost:8080/props`
- Sampling defaults: `Select-String -Path config.py -Pattern "LLM_TEMPERATURE|LLM_TOP"`
- Template/KV flags: `Select-String -Path start.bat -Pattern "chat-template|cache-type|spec-type|reasoning"`
- Heuristic unchanged: `Select-String -Path core\memory.py -Pattern "// 4"`
