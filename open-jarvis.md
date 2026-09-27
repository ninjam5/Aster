# OpenJarvis — Stealables for Aster

Source: `E:\LLM testing\openjarvis\OpenJarvis` (Stanford Hazy Research / Scaling Intelligence Lab, Apache 2.0).
Frame: OpenJarvis is a **research framework** built around five primitives (Intelligence, Engine, Agents, Memory, Learning) with trace-driven feedback. Aster is a **production single-user assistant** with a fixed 45-tool admin brain and one llama-server. We don't want their framework — we want surgical lifts.

This document grades each idea by **how much it improves Aster** vs **how much rewiring it costs**. Items are ordered roughly by payoff.

---

## TIER 1 — Steal these. High payoff, low cost.

### 1.1 `LoopGuard` — degenerate-loop detection in the ReAct loop

**Their code:** `src/openjarvis/agents/loop_guard.py` (~243 lines, pure Python; optional Rust speedup).

**What it does:**
- SHA-256 hash of `(tool_name, arguments)` — blocks after N identical calls (default 3).
- Ping-pong detector: sliding window detects A-B-A-B and A-B-C-A-B-C cycles.
- Per-tool budget: tools tagged `spec.metadata["polling"] = True` get a relaxed budget.
- Warn-before-block: first cycle returns a warning that gets injected back into context; second cycle hard-blocks.
- 4-stage context-overflow compression: (1) truncate old tool results → (2) sliding window → (3) drop tool-call/result pairs from middle → (4) system + last 2 exchanges.

**Why Aster wants it:** Aster's 15-round admin loop in `core/brain.py` has *one* failsafe — the post-tool "apathy" nudge — but nothing detects "Gemma calls `smart_click` on the same word three times because the OCR keeps misreading it." We've definitely seen this. `LoopGuard.check_call(tool_name, json.dumps(args))` between `_extract_xml_tool_call()` and `execute_tool()` would catch it.

**Effort:** ~1 hour. Drop the file in as `core/loop_guard.py`, strip the Rust import, wire into the admin loop. Mark `watch_screen` as `polling=True` so the screen watcher isn't penalized.

---

### 1.2 Observation compression (tool-output truncation/summarization)

**Their code:** `src/openjarvis/agents/monitor_operative.py` — the `observation_compression` axis: `summarize` / `truncate` / `none`. Default summarises with a small LLM call when tool output > 2000 chars.

**Why Aster wants it:**
- Aster already does this *for images* (`purge_media_cache()` tombstones Base64 after the turn) but **does nothing for huge text tool outputs** — e.g. `list_directory_tree` of a deep folder, `deep_web_search` raw results, `read_local_file` on a 50k-line log.
- A 4-char-per-token heuristic with a hard truncate at e.g. 3000 chars + ellipsis tombstone (`[truncated — call re_examine_X to reload]`) would massively reduce KV-cache pressure inside long admin loops.
- For the heavyweights (`deep_web_search`), use the *summarize* mode: one extra Gemma call (`temp=0.2, n_predict=200`) condensing the result.

**Effort:** ~2 hours. Add a `_compress_tool_output(text, mode)` helper in `core/brain.py`; call it in `execute_tool()` before appending the observation. Per-tool override via a dict (`smart_click` is already short, `deep_web_search` should summarize).

---

### 1.3 Hybrid memory with Reciprocal Rank Fusion (RRF)

**Their code:** `src/openjarvis/tools/storage/hybrid.py` + `docs/architecture/memory.md`.

**What it does:**
- Combines a sparse retriever (BM25 / SQLite FTS5) and a dense retriever (FAISS / Chroma) via:
  `RRF(d) = Σ_i w_i / (k + rank_i(d))` with `k=60` default.
- Over-fetches `top_k × 3` from each sub-backend before fusion.
- Configurable per-backend weights (their default: dense 1.5×, sparse 1.0×).

**Why Aster wants it:**
- Aster's memory pipeline is **dense-only** (ChromaDB) plus an append-only `memory.md` that nobody searches. Semantic recall misses keyword-exact stuff ("what's the API key for X?") and vice-versa.
- `Aster_Vault/memory.md` is already there as the BM25 corpus. Just index it.

**Effort:** ~3 hours. Add `rank_bm25` (pure Python, no deps), build an index over `memory.md` lines on startup, modify `recall_memory()` in `tools/memory_manager.py` (or wherever it lives) to fuse Chroma + BM25 results via RRF. No new GPU load.

**Bonus:** Their `inject_context()` adds source attribution tags (`[Source: docs/api.md]`). Aster's `recall_memory` returns naked strings — adding `[Recalled: 2026-03-14]` tags would let Gemma cite when it's using long-term memory vs. session memory.

---

### 1.4 Persona externalised to a markdown file

**Their code:** `configs/openjarvis/prompts/personas/jarvis.md` — 35-line persona file (warm British, dry-witted, never describes actions, no markdown when spoken). Loaded at runtime, not hardcoded.

**Why Aster wants it:** Aster's persona lives inside `core/brain.py` as a Python string constant (system prompt). Pulling it into `Aster_Vault/persona.md` means:
- Edit personality without touching Python.
- Different personas for different contexts (Discord brain already has its own `DISCORD_CHAT_SYSTEM_PROMPT` — same idea, deserves a file).
- Easy A/B testing.

**Effort:** ~20 minutes. Read the file at startup in `config.py`; fall back to a hardcoded default if the file is missing.

---

### 1.5 `<think>` tag stripping

**Their code:** `BaseAgent._strip_think_tags(text)` — static method that removes `<think>...</think>` blocks from model output before returning.

**Why Aster wants it:** Gemma 4 sometimes emits chain-of-thought blocks (or hallucinates `<thinking>` from RLHF traces). Aster doesn't filter them — they leak into voice replies and Telegram. A 3-line regex in `_execute_gemma_completion()` is free defense.

**Effort:** 5 minutes.

---

### 1.6 URL pre-fetching in user input

**Their code:** `NativeOpenHandsAgent` auto-detects URLs in the prompt, fetches them, inlines the content directly so the LLM doesn't have to call a tool.

**Why Aster wants it:** When the user pastes a URL into CLI/Telegram ("summarize this: https://..."), Aster currently has to call `deep_web_search` or fail. A regex-detect + `requests.get` + readability extract in `process_user_input()` lets Aster answer in one round instead of three.

**Effort:** ~1 hour. Use `trafilatura` (which is already a sane dep for clean extraction); guard with a domain blocklist for the obvious garbage.

---

## TIER 2 — Steal these, but only if you actually want the feature.

### 2.1 Morning Digest as a scheduled task

**Their code:** `src/openjarvis/agents/morning_digest.py` + `configs/openjarvis/examples/morning-digest-mac.toml`.

**What it does:** Scheduled daily briefing that pulls from email, calendar, messages, weather/news, runs them through a persona-conditioned LLM, and **synthesises spoken audio** via TTS. Their `jarvis.md` persona is specifically tuned for this — "no markdown, no bullet points, this is spoken aloud."

**Why Aster wants it:** Aster has every ingredient:
- Spotify ✓, Kokoro TTS ✓ (and it's the *same* shared pipeline used by the call), `[NATIVE_AUDIO_PAYLOAD]` ✓, Telegram voice-note dispatch ✓.
- Missing: Gmail/Calendar integration, scheduler.
- A cron-style daemon thread that fires at 07:30 → builds a "what's on today" prompt from any data sources you wire up → emits Kokoro voice note via Telegram is ~150 lines.

**Effort:** Medium. The hard part is data sources, not the orchestration. Skip if you don't actually want a morning briefing.

**Not worth stealing:** Their TOML config layering — Aster has hardcoded `config.py` and that's fine for a single-user system.

---

### 2.2 Trace recording (lightweight, not the full learning system)

**Their code:** `src/openjarvis/traces/` — `TraceStore` (SQLite), `TraceCollector` (wraps an agent, subscribes to events, builds a `Trace` per `run()`), `TraceAnalyzer` (read-only aggregate stats).

**What it does:** Every turn produces a row with `(query, agent, model, steps, latency, tokens, outcome, feedback)`. `TraceStep` rows record each `GENERATE` / `TOOL_CALL` / `RETRIEVE`. SQLite, append-only.

**Why Aster wants it (the modest version):**
- Aster has *no idea* which of its 45 tools actually get used, how often, with what latency, how often they fail. Right now diagnostics is a `sys.stdout` shim → Telegram forwarder. That's logs, not metrics.
- A single `aster_traces.db` SQLite table written by `execute_tool()` (just `tool_name`, `args_hash`, `latency_ms`, `success`, `timestamp`) unlocks: "show me my 10 slowest tools," "which tools fail most," "what does my actual usage look like."
- This is **observability**, not "learning." Don't take the `TraceDrivenPolicy` / `update_from_traces()` / DSPy optimizer stuff — that's framework overkill for a single-LLM setup.

**Effort:** ~2 hours for the minimal version. Add to `tools/diagnostics.py`.

**Skip:** The full `learning/` directory. Aster has one model. Routing policies are pointless.

---

### 2.3 EventBus pub/sub for inter-daemon coordination

**Their code:** `src/openjarvis/core/events.py` — synchronous pub/sub. Event types: `INFERENCE_START`, `TOOL_CALL_END`, `AGENT_TURN_END`, `MEMORY_RETRIEVE`, `LOOP_GUARD_TRIGGERED`, `SCHEDULER_TASK_START`, etc.

**Why Aster might want it:** Aster's daemons (Telegram, Discord, WebRTC, Gesture, Intervention, Face server) communicate through:
- Module-level globals (`_active_bridge`, `_active_loop`, `WAITING_FOR_ID`).
- Direct cross-imports (`webrtc_bridge.speak_intervention()` called from `intervention.py`).
- Telegram as a shared notification channel.

That's tangled. A small pub/sub (`tools/event_bus.py`, ~60 lines) where the brain publishes `TURN_END` and the desktop UI / sentiment classifier / diagnostics all subscribe would replace the existing ad-hoc `publish_terminal` / `publish_wake` / `publish_sentiment` calls in `tools/realtime_stream.py` with one mechanism.

**Effort:** Medium-high (~half a day). Touches many daemons. **Honest verdict:** Aster is small enough that the current ad-hoc wiring is fine. Only do this if you find yourself adding a third "publish_X" helper.

---

### 2.4 Channel ABC + additional bridges (Signal, WhatsApp, Matrix, Slack, …)

**Their code:** `src/openjarvis/channels/` — `BaseChannel` ABC + 25+ implementations including Signal, WhatsApp (Baileys), Matrix, Slack, Teams, iMessage, Telegram, Discord, etc.

**Why Aster might want it:** Aster currently supports Telegram + Discord, both hardcoded. If you ever want Signal or WhatsApp bridging, their `whatsapp_baileys.py` (with bundled Node.js Baileys bridge) is the most production-grade open-source WhatsApp-via-personal-account integration available.

**Effort:** High — and only worth it once you actually want a second bridge. The abstraction-for-its-own-sake of refactoring Telegram+Discord into `BaseChannel` would not pay off.

**Note:** Aster's Telegram bridge has bespoke features (`/find`, `/screenshot`, `/peek`, `[NATIVE_AUDIO_PAYLOAD]` interception) that don't fit a generic Channel interface. Don't lose those to genericization.

---

## TIER 3 — Interesting but probably skip.

### 3.1 CodeAct (`NativeOpenHandsAgent`) — generate-and-execute Python

The LLM writes ```python ... ``` blocks, the agent extracts and `exec()`s them. Massive flexibility (the model can write any computation it needs) but:
- **Security disaster** without a sandbox. Their answer is their `SandboxedAgent` + Docker `--network none`. Aster has no Docker, runs on the user's actual Windows machine, and has `set_system_state` (lock/sleep/restart) in its toolbelt. Adding `exec()` is a sharp knife at a kid's birthday party.
- Aster's 45-tool registry already covers what users actually ask. CodeAct is for open-ended research agents, not "play Spotify."

**Verdict:** Don't.

### 3.2 RLM (Recursive Language Model with persistent REPL)

For very long contexts, store the document as a Python variable in a REPL, let the "Root LM" write Python to inspect chunks via `llm_query()` and `llm_batch()`.

**Verdict:** Aster has 64k context already and the use case (summarize a 200k-token document) is not Mohamed's workflow. Skip.

### 3.3 LLM-guided spec search / DSPy / GEPA skill optimization

A frontier "teacher" model rewrites the local "student's" prompts/configs/tools based on trace analysis. Risk-tiered auto-apply (`auto` / `review` / `manual`). Genuinely cool research.

**Verdict:** Way too heavy. Aster is one Gemma instance with hand-tuned prompts. Don't.

### 3.4 HeuristicRouter + multiple-model routing

Aster runs **one** Gemma 4 E4B. Any routing policy is a no-op. Skip.

### 3.5 Skills system (`agentskills.io` SKILL.md format)

A discoverable, hot-loadable, optimisable skill registry where each skill is a `SKILL.md` + dependency graph + capability declarations + DSPy-optimised few-shot overlay. ~13,700 community skills via OpenClaw.

**Verdict:** Beautiful design, wrong scale. Aster's tools are a *fixed* hand-curated list of 45. Adding a SKILL.md loader would add complexity for a feature you've explicitly avoided ("Adding a new tool requires three edits" in CLAUDE.md is a deliberate choice, not a problem). Skip unless Aster grows a plugin ecosystem.

### 3.6 Container sandbox

Same reasoning as CodeAct — Aster isn't running untrusted input, and the user *wants* root on their own machine.

### 3.7 Energy/cost reward function

`HeuristicRewardFunction` (latency 0.4 + cost 0.3 + efficiency 0.3). Cost is always $0 locally; efficiency (completion/total tokens) is interesting but not actionable without multi-model routing. Skip.

---

## Concrete recommendation — order to do them in

1. **`<think>` strip** (5 min, free win).
2. **LoopGuard** (1 hr, fixes a class of bugs you've definitely hit).
3. **Persona to markdown file** (20 min, makes future tuning easier).
4. **Tool-output compression** (2 hr, KV-cache relief on long admin loops).
5. **URL pre-fetch** (1 hr, kills a class of "summarize this link" failures).
6. **Hybrid memory (RRF over ChromaDB + BM25 on memory.md)** (3 hr, materially better recall).
7. **Trace SQLite** (2 hr, finally know what your tools are actually doing).
8. *(Optional)* Morning Digest if you want it as a feature, not as a steal.

Total: ~10 hours of work for a sizeable Aster upgrade. Everything else in OpenJarvis is either framework scaffolding Aster doesn't need or research-y stuff that doesn't fit a single-user single-model assistant.

---

## What's worth reading even if you don't steal it

- `docs/architecture/overview.md` — the five-primitive framing is a clean mental model.
- `docs/architecture/agents.md` — the agent taxonomy (Simple / Orchestrator / ReAct / CodeAct / RLM / Operative / MonitorOperative) is useful vocabulary even if you stay with Aster's monolithic admin brain.
- `docs/architecture/learning.md` — even just the **trace data model** (Trace + TraceStep + StepType enum) is a good template if/when you add observability.
- `configs/openjarvis/prompts/personas/jarvis.md` — the prompt is *good*. The constraints section ("ONLY report facts present", "NEVER describe actions you are taking", "no markdown for spoken output") would tighten Aster's persona prompt if you imported the discipline.
