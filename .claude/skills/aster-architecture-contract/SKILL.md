---
name: aster-architecture-contract
description: Load this before designing or reviewing ANY change to Aster's core (core/brain.py, core/memory.py, config.py, main.py, webrtc_bridge.py) or when you need to know why the system is shaped the way it is. Triggers - adding a second LLM call path, touching the message list or system prompt, adding a model, changing tool-calling, modifying memory/trim behavior, adding a thread or daemon, questions like "why is there only one llama-server", "can I just call the API directly", "where does the persona live", "is it safe to keep images in history".
---

# Aster Architecture Contract

The load-bearing design decisions, the invariants that must hold, and the known weak
points. Violating an invariant here has historically cost days (see
`aster-failure-archaeology` for the full stories).

**When NOT to use this skill:** for running the system use `aster-run-and-operate`;
for symptom triage use `aster-debugging-playbook`; for local-LLM theory (GGUF,
templates, KV cache) use `local-llm-reference`.

## The system in one paragraph

One llama-server process (**BeeLlama v0.4.7 fork: Qwen 3.6 35B-A3B IQ4_XS MoE,
60k ctx, KVarN KV, MTP speculative decoding, mmproj-F16 vision on the GPU, port 8080**)
serves every LLM request. One Python process (`main.py`) hosts the brain: a 15-round
native OpenAI-format tool-calling loop over 69 admin tools, plus daemon threads for
Telegram, Discord, LiveKit voice, awareness, intervention, ambient audio, and a
FastAPI face server (port 8000) that backs two Tauri desktop apps. Memory is
dual-write (ChromaDB vector + `Aster_Vault/memory.md` append log). Everything runs
on one RTX 3080 12 GB; nothing leaves the machine.

## Invariants (verify before violating — most are hard "never")

### 1. Exactly one LLM engine
A single llama-server instance at `http://localhost:8080` serves all text, vision,
tool calls, chat, Discord, and sentry requests. No `llama-cpp-python`, no Ollama, no
second server, no per-purpose model loads.
**Why:** the 12 GB VRAM ceiling, and the 2026-05 "engine hell" saga — three failed
architectures came from fighting multiple template/handler layers.
**If violated:** VRAM OOM alongside the ~11.5-11.9 GB the server already holds, and you
reintroduce the template-mismatch class of bugs.

### 2. One adapter, one return shape
`_execute_llm_completion()` (`core/brain.py:79`) is the ONLY function that talks
to the LLM. It returns the **full message dict** (`role`/`content`/`tool_calls`) for
*every* caller. Callers that only want text must unwrap it themselves:
`(response_msg.get("content") or "")`.
Direct non-tool call sites that all obey this: `tools/vision.py`, `tools/sentry.py`,
`tools/awareness.py`, `tools/face_server.py` (one-shot descriptions, scene analysis,
persona generation).
**Why:** one calling convention instead of two; when the return type changed during
the native-tools migration, having a single choke point made it survivable.
**If violated:** a caller treating the dict as a string produces `{'role': ...`
garbage in user-facing replies.

### 3. `messages[0]` is the system prompt and is never trimmed
`trim_memory()` (`core/memory.py:417`) pops index 1 (oldest non-system message) in a
loop until under budget (`int(N_CTX * 0.9)`, token estimate = `len(content) // 4`
per message). Index 0 must always be the assembled system prompt.
**Why:** persona + tool laws live there; the 2026-05 root cause of "I am a large
language model trained by Google" was the system content silently not reaching the
model.

### 4. Persona is data, not code
Personality lives in `Aster_Vault/System_Prompts/<name>.md`, selected by
`config.SYSTEM_PROMPT` (from `self_config.yaml → persona.system_prompt`), assembled
by `_build_system_content()` (`core/brain.py:2186`, calls `_load_system_prompt()` at
`:2147`, strips `<!-- -->` comments, falls back to a minimal prompt on missing
file). There is **no code branching on which persona is active**, and no global
response-length flag — length rules are persona text.
**Why:** swapping the whole personality must stay a one-line config edit.
**Rule:** persona file *content* is owner-authored. Wire it; don't rewrite it.

### 5. Native tool calling — no forks
`tools=ADMIN_TOOLS` is sent on the POST; llama-server's chat template
(`E:\Models\qwen36_chat_template.jinja`, froggeric v22.5) renders the schemas and
llama-server parses the response back into a structured `tool_calls` array
(`_extract_native_tool_call`, `core/brain.py:249`). Tool results go back as
`role:"tool"` messages carrying `tool_call_id`.
The Gemma-era XML-in-text path **and** its `config.USE_NATIVE_TOOL_CALLS` rollback
flag were **deleted with owner sign-off in the Qwen swap (2026-09-27)** — there is
no runtime fork left. **Why:** the flag existed to make a bad LLM-behavior night a
one-line revert; the revert path is now the branch `gemma-4-e4b-lightweight`.
**Rule:** never re-introduce a parallel tool-calling parse path; LLM-behavior
changes ship behind a *new* flag (see `aster-change-control` rule 1).

### 6. Two brains, hermetically separate
Admin brain (`process_user_input`, `core/brain.py:2800`): full history, 69 tools,
15 rounds. Discord brain (`process_discord_chat`, `core/brain.py:2551`): per-friend histories,
its own system prompt, exactly 3 tools (`forward_to_owner`, `save_personal_fact`,
`get_current_track`), 4 rounds.
**Why:** friends must never reach system control, memory, or the owner's context.
**If violated:** a Discord contact can operate the owner's PC.

### 7. Media hygiene — no base64 survives the turn
Every image enters as an `image_url` content block only — the chat template
places the vision tokens itself; the old `<image>`/`<__media__>` marker injection was
a Gemma-template workaround, deleted 2026-09-27. After every
turn, `purge_media_cache()` strips base64 from history, archives the JPEG to
`Aster_Vault/images/`, and leaves a tombstone with the file path.
`re_examine_image(filepath, question)` is the only re-entry (stateless, temp 0.2).
**Why:** base64 in history bloats the KV cache/VRAM — this was a real leak class.

### 8. Shared model singletons (VRAM law)
One CUDA Faster-Whisper instance (`local_stt.get_whisper_model()`) serves LiveKit
calls; Telegram voice notes reuse it while a call is live and otherwise use a lazy
**CPU** instance (`local_stt.transcribe_file`/`_get_cpu_whisper`, zero VRAM). One ref-counted Kokoro pipeline (`tools/audio.py`
`acquire_kokoro_pipeline`/`release_kokoro_pipeline`) serves the voice-note tool AND
`local_tts.py`, offloading from VRAM at zero holders. The CUDA voice-emotion model
is likewise acquire/release ref-counted. OmniParser YOLO is released immediately
after each fallback use (`tools/vision.py:391 _release_omniparser`).
**Why + budgets:** see `aster-vram-discipline` (measured idle ~11.5-11.9 GB on the Qwen engine, 2026-09-27).
**Rule:** never instantiate a per-call model copy.

### 9. Config layering and fail-closed integrations
`config.py` ← `self_config.yaml` (identity/knobs; exposed to the LLM via
`get_my_config`) ← `secrets.yaml` (credentials; NEVER exposed, loaded by a
deliberately separate `_secret()` walker). Blank/missing credential ⇒ the
integration's `*_AVAILABLE` flag is False and the feature no-ops — nothing crashes.
**Rule:** a credential must never appear in `config.py`, `self_config.yaml`, or any
tracked file. Never read the real `secrets.yaml`/`self_config.yaml` in a session;
use the `.example.yaml` templates for structure.

### 10. Threading model
Main thread = CLI loop. Daemon threads started in `main.py:657-684`: Telegram
polling, Discord listener, WebRTC bridge, gesture, intervention, awareness, ambient
audio, face server. Sentry auto-start is commented out (`main.py:664`). A
brain-busy event makes the awareness daemon defer its own LLM calls while the main
brain is mid-turn (single engine, no concurrent slot contention);
`config.brain_lock` serializes brain entry. The tkinter assist overlay runs its
whole lifecycle on its own thread (tkinter is not thread-safe).

### 11. One pending-approval slot
Gmail replies and Calendar invites share a single pending-action slot
(`tools/google_auth.py` `set_pending_action`/`resolve_pending_action`, resolved at
the very top of `process_user_input`). One slot by design — "send it"/"discard" is
never ambiguous, and it works identically from every interface.

## Known weak points (open, stated plainly — candidates, not TODOs to freelance on)

| Weak point | Where | Status 2026-09-27 |
|---|---|---|
| Token counting is `len//4` heuristic, not a tokenizer | `core/memory.py:391` | Accepted; error matters near context limits |
| `--ctx-size 60000` (start.bat) == `config.N_CTX` 60000 | start.bat / config.py:168 | **Aligned** (swapped 2026-09-27); keep them in step; verify per-install |
| Discord honorific regex can mangle words ("Sirius") | `core/brain.py` `_discord_honorific` | Known, accepted |
| Vision/assist is primary-monitor only | `tools/vision.py` | Known limitation |
| Mood streaks are in-memory, reset on restart | `tools/emotion_recognition.py` | Accepted |
| Memory recall is dense-only (ChromaDB); keyword-exact queries miss | `core/memory.py:184` | Candidate fix: RRF hybrid (open-jarvis.md §1.3) |
| LoopGuard shipped (call-hash budget + A-B-A-B) | `core/loop_guard.py` | **Fixed**; fleet measurements pending (live soak) |
| `tools/face_server.py` on :8000 serves localhost without auth hardening | tools/face_server.py | UNVERIFIED posture — check before exposing beyond localhost |
| LiveKit devmode may fork a duplicate torch process (+3-5 GB RAM) | `webrtc_bridge.py:687` (`server.run(devmode=True)`) | Open verify-item (CLAUDE.md cites old line 668) |

## Provenance and maintenance

Authored 2026-07-05 against live code. Re-verify volatile facts:

- Line anchors: `Select-String -Path core\brain.py -Pattern "^def _execute_llm_completion|^ADMIN_TOOLS|^def execute_tool|^def process_user_input"`
- Tool count: `python -c "import core.brain as b; print(len(b.ADMIN_TOOLS))"` (68 on 2026-07-05; takes ~1 min)
- Tool path is native-only (no flag): `Select-String -Path core\brain.py -Pattern "_extract_native_tool_call"`
- ctx mismatch: `Select-String -Path start.bat -Pattern "ctx-size"` vs `Select-String -Path config.py -Pattern "N_CTX ="`
- devmode line: `Select-String -Path webrtc_bridge.py -Pattern "devmode"`
