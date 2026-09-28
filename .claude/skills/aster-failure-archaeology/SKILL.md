---
name: aster-failure-archaeology
description: Load this before re-investigating any Aster problem that smells familiar, before proposing an architectural change that may have been tried, or when a grep turns up something confusing (karaoke references, dead code, stale docs). The chronicle of every major investigation, dead end, rejected fix, and removal - symptom, root cause, evidence, status - so settled battles are never re-fought. This repo has NO git history; this file is the substitute.
---

# Aster Failure Archaeology

Format per entry: **Symptom → Root cause → Evidence → Status.**
IMPORTANT: this repo has zero git commits. The evidence trail is docs
(`summary.md` 2026-05-20 §4, root `CLAUDE.md` gotchas, `emotions-next-steps.md`,
`aster-opensource-plan.md`, `checklist_test.md`, `engine_testing/results/`) and
project memory. Dated logs are deliberately never rewritten — expect them to
describe old states.

**When NOT to use this skill:** live triage → `aster-debugging-playbook`; current
rules → `aster-change-control`.

## Quick index

| Battle | Verdict (one line) | Status |
|---|---|---|
| Engine hell — 3 failed tool-calling architectures | Stop fighting Gemma's template; custom Jinja + (later) llama-server | SETTLED — fenced |
| llama-cpp-python → llama-server migration | One REST server serves everything | SETTLED |
| XML ReAct → native OpenAI tool calling | Migrated 2026-06; rollback **removed** in the Qwen swap | SETTLED |
| Hallucinated tool execution | Detector + single retry | SETTLED (mitigation), model-level cause LIVE |
| Post-tool apathy | `[System Internal]` nudge + one completion | SETTLED (mitigation) |
| Degenerate tool loops | LoopGuard (call-hash budget + ping-pong detect) shipped + tests | SETTLED (watch live) |
| OCR clicked the word "Enter" | Keyboard ≠ screen text; `press_key` law | SETTLED |
| Per-crop OCR 60–90 s | Two-track rewrite → ~2–4 s | SETTLED |
| VRAM duplicate-model leaks | Singletons + ref-counting | SETTLED (pattern mandatory) |
| LiveKit devmode subprocess (+3–5 GB RAM) | Suspected worker fork | OPEN verify-item |
| SpeechBrain loading | `_prepare_speechbrain_imports()` workaround | SETTLED |
| hsemotion torch pkg broken on timm 1.x | Use `hsemotion-onnx` | SETTLED |
| IEMOCAP anger→happy | Arousal ≠ valence; text gates prosody | SETTLED (characterized) |
| Karaoke Mode | Fully removed 2026-07-03 | SETTLED — never restore |
| Doc drift (AGENTS.md et al.) | Multiple stale claims; refreshed 2026-09-27 for the Qwen swap | ONGOING hazard |
| Gemma 4 E4B → Qwen 3.6 35B-A3B engine swap | BeeLlama v0.4.7 + froggeric template; XML/audio/LoRA-era paths deleted | SETTLED (code) — live validation pending |

## The engine hell saga (2026-05) — the costliest battle. Fenced off.

**Symptom:** tool calling broken three different ways across three architectures;
at worst the model answered "I am a large language model trained by Google" (no
persona at all).

**The three dead ends (never retry these):**
1. **Native `tools=` + chatml-function-calling** (llama-cpp-python): Gemma clashed
   with ChatML grammar; leaked `functions.tool_name:` into chat output and tore its
   own output apart.
2. **`Llava15ChatHandler` for mmproj vision:** force-replaced the template with
   Vicuna-style `USER:/ASSISTANT:`; Gemma expects `<start_of_turn>user`; produced an
   infinite hallucination echo loop.
3. **XML ReAct + `chat_format="gemma"`:** llama-cpp-python's built-in gemma
   formatter **silently dropped `role:system` messages** — persona + tool manual
   never reached the model.

**Root cause across all three:** fighting Gemma's chat template with layers that
rewrote or dropped parts of the conversation.
**Fix (2026-05-16):** respect the template. Later completed by migrating to
llama-server with the custom `Aster_Vault/gemma4-multimodal.jinja` (handles
`<__media__>` placement and, post-migration, native tool grammar).
**Evidence:** project memory (fix dated 2026-05-16); `checklist_test.md`
(2026-05-15 migration record); summary.md §1.4.
**Status: SETTLED.** Rule: no template/adapter change without harness A/B
(`aster-change-control` rule 2).

## Engine migration: llama-cpp-python → llama-server (2026-05)

**Why:** single server binary, OpenAI-compatible endpoint, internal multimodal
routing — deleted the whole handler-layer problem class. `build_cuda.py` is a
leftover from the llama-cpp-python era (LEGACY, do not run).
**Status: SETTLED.** One engine is invariant #1 in `aster-architecture-contract`.

## Tool calling: XML ReAct → native OpenAI format (2026-06)

**What changed:** `tools=ADMIN_TOOLS` on the POST; structured `tool_calls` read off
the response; results as `role:"tool"` + `tool_call_id`. Legacy XML regex parsing
(`_legacy_xml_parse*`, which normalized Gemma's mangled variants like
`<|tool_call>call>`) retained behind `USE_NATIVE_TOOL_CALLS` (default True).
**Evidence:** the plan and its rollback requirement are written in
`aster-opensource-plan.md` Step 2 [Done]; latest engine report (2026-06-24) shows
0% XML tag-mangle / 0% JSON-decode failure on 84 calls.
**Caveat still true today:** `engine_testing/harness.py` exercises ONLY the legacy
path — the native path has no automated harness yet (first obligation of
`aster-gemma-reliability-campaign`).
**Status: SETTLED**, rollback flag must stay.

## Model behavior mitigations (both live in the 15-round loop)

- **Hallucinated execution** ("Playing it now" with no tool call): regex
  cross-reference of user-intent × response-claims + leaked-syntax check; one
  retry, `hallucination_retried` flag prevents loops (`core/brain.py:176`, `:3214`).
- **Post-tool apathy** (empty string after successful tools): `tools_executed`
  bool; neutral `[System Internal]` nudge; one final completion; nudge removed from
  history.

**Status:** mitigations SETTLED; the underlying model unreliability is the LIVE
campaign problem, alongside **degenerate loops** (no guard yet; candidate:
LoopGuard port graded in `open-jarvis.md` §1.1).

## GUI automation battles

- **Driving the owner's real Chrome profile via CDP — IMPOSSIBLE, do not re-fight
  (2026-09-26)**. Symptom chain: `browse_web` with `use_real_profile: true` opened
  a motionless Profile-3 window, then a logged-out guest. Root cause, settled by
  direct experiment on Chrome 153: (1) Chrome ≥136 ignores
  `--remote-debugging-port` whenever the effective user-data-dir IS the default
  one — even passed explicitly (window opens, port never binds → dead window →
  old code silently fell back to a guest); (2) v20 app-bound cookie encryption
  refuses to decrypt through ANY copy OR directory-junction of that dir, and
  Chrome then PURGES the undecryptable cookies — the junction test wiped the
  owner's `Default` + `Profile 3` sessions (re-login only; `Local State` key
  survived, other profiles intact); (3) Edge is identical (v20). All three are
  anti-cookie-theft security features, not bugs. Consequences in code:
  `use_real_profile: true` raises `RealProfileUnavailable` (no silent guest),
  `browser_user_data_dir`/`browser_profile_directory` config keys were removed,
  and logged-in browsing happens via `login_once.py` (one-time login in the
  dedicated persistent profile). NEVER launch Chrome with a copy/junction of the
  owner's User Data dir. SETTLED.
- **Stale guest browsers squatting the CDP port** (2026-09-25): old Aster-spawned
  guests survived window close (background mode) and held `9222`, so attach-first
  grabbed a logged-out stranger instead of launching fresh. Fixed at the root:
  every Aster launch passes `--disable-background-mode`, launches on a free port
  ≥ CDP+1, and detach kills the browser by `--user-data-dir` match
  (`_kill_browser_tree` — the Popen pid can hand off to a child, observed live).
  SETTLED.
- **"Clicked the word Enter"**: user asked to press Enter; OCR found the literal
  string "Enter" on screen and smart_click clicked it. Law: keyboard keys →
  `press_key`, never `smart_click`. SETTLED.
- **Per-crop OCR latency**: the old pipeline OCR'd ~109 detected elements
  individually (60–90 s). Rewritten to two-track: full-screen
  `pytesseract.image_to_data` ×2 (normal+inverted, 2× upscale) with fuzzy line
  scoring, YOLO+crop-OCR only as fallback → ~2–4 s. SETTLED (`summary.md` §4.13).
- **DPI offsets**: coordinates need `/2` (upscale) × logical/physical scaling.
  SETTLED in code; re-check on monitor/DPI changes.
- **`smart_click` "Florence-2" description drift**: the ADMIN_TOOLS description
  once referenced Florence-2 while the implementation was OmniParser+pytesseract.
  **FIXED as of 2026-07-05** (no "Florence" match in core/brain.py) — root
  CLAUDE.md's gotcha entry is now STALE.
- **Old YOLO coordinate pipeline** (`parse_screen`, `UI_ELEMENT_COORDS`): archived
  inside a `'''...'''` string at the bottom of `tools/vision.py`. Never resurrect;
  `locate_ui_element` is the locator.

## VRAM / resource battles

- **Duplicate model loads**: separate Whisper/Kokoro instances per consumer during
  calls. Fixed with the shared Whisper singleton + ref-counted Kokoro (and
  ref-counted voice-emotion). Pattern is now mandatory
  (`aster-vram-discipline`). SETTLED.
- **Base64 bloat**: images persisted in message history inflating KV/VRAM. Fixed by
  `purge_media_cache()` tombstones + cold storage + `re_examine_image`. SETTLED.
- **LiveKit devmode subprocess**: `server.run(devmode=True)`
  (`webrtc_bridge.py:687`; CLAUDE.md cites old line 668) may fork a worker
  duplicating Python+torch (+3–5 GB **RAM**). Check:
  `Get-Process python | Format-Table Id, WorkingSet64` after a call connects.
  Candidate fix `devmode=False` — untested, changes worker lifecycle. **OPEN.**

## Model-loading landmines (all SETTLED — workarounds in code)

- **SpeechBrain**: import requires `_prepare_speechbrain_imports()`
  (`tools/emotion_recognition.py`) — a lazy-module guard plus a patch for the
  CVE-2025-32434-era `torch.load` restriction. Don't downgrade torch to "fix" it.
- **hsemotion (torch)**: pickled models break on installed timm 1.x (`conv_s2d`
  attribute error) → project uses `hsemotion-onnx` on onnxruntime; loader
  pre-fetches weights around the package's broken downloader.
- **CTranslate2 CUDA DLLs**: `main.py:14-19`/`local_stt.py` prepend
  `site-packages/nvidia/{cublas,cuda_nvrtc}/bin` to PATH.

## Emotion-model characterization (SETTLED as known limits, not bugs)

- IEMOCAP wav2vec2 confuses high arousal with valence: fast/loud neutral speech →
  `frustrated`/`happy`; **anger can read as happy**. Mitigation: fusion lets
  confident TEXT content gate prosody; lever `EMOTION_MIN_CONFIDENCE` 0.5→0.6.
- j-hartmann text model scores clearly-angry content as `sad` sometimes — don't
  treat sad-vs-frustrated as precise.
- Face FER tops out ~60–67% on AffectNet — hence EMA smoothing, confidence gate,
  ambient-hint-only role. (`emotions-next-steps.md` "Known limitations".)

## Removals and dead code (do not "helpfully" restore)

- **Karaoke Mode — fully removed 2026-07-03** (module, tools, config, face_server
  endpoints, UI section, tests). Grep hits remain in dated logs
  (`engine_testing/results/*.txt`, integration logs) BY DESIGN. Never re-add
  yt-dlp/demucs.
- `tools/web.py` (`search_web`) — dead code; brain uses `research()` in
  `tools/rag.py`.
- `tools/gui.py` — present, not imported (press_key/smart_type dispatch directly
  via pyautogui in brain.py).
- `whisper-models/models--Systran--faster-distil-whisper-large-v3/` — never
  loaded; only `medium.en` is used.
- `AUDIO_MODE` + the native-audio (`input_audio`) voice-note route — **removed 2026-09-27**;
  voice notes now go through Faster-Whisper (`local_stt.transcribe_file`).
- Archived YOLO `'''...'''` blocks in `tools/vision.py` (`parse_screen`, `UI_ELEMENT_COORDS`,
  `get_ui_element_coords`, `_extract_detector_elements`…) — **deleted 2026-09-27**.
- Old in-`main.py` FastAPI dashboard block — commented out; the ACTIVE server is
  `tools/face_server.py`.
- Sentry daemon auto-start — commented out (`main.py:664`); toggle still works.

## Small settled oddities

| Item | Truth as of 2026-07-05 |
|---|---|
| `is_fact_already_known()` reads `Aster_vault/memory.md` (lowercase v) | Still true; harmless on Windows, real on case-sensitive FS |
| Spotify redirect URI once collided with llama-server's :8080 | Fixed — default is now `http://127.0.0.1:8081` (config.py:104) |
| `awareness_mode: false` yaml "ignored / hardcoded True" | **FIXED** — `tools/awareness.py:29` now reads config; CLAUDE.md gotcha + example-yaml comment are STALE |
| Intervention threshold "default 1800 s" (summary.md) | STALE — config default is 60 s |
| Docs claim model is `gemma-e4b-q4km` | **RESOLVED 2026-09-27** — engine + launchers now Qwen 3.6 35B-A3B; verify live via `/props` |
| `AGENTS.md` | Was badly stale (XML ReAct, 39 tools, hardcoded creds). **Rewritten 2026-09-27** — now native tool calling, 69 tools, `secrets.yaml`, docs-duty line. Re-verify before trusting |
| harness.py `TEMPERATURE = 0.7` labeled "must match brain.py" | config default `LLM_TEMPERATURE` is 1.0 — drifted |

## Model-sweep record (June 2026, `engine_testing/results/`)

Variants swept: Gemma-4 E2B-QAT, E4B-QAT, E4B q4km, 12B (IQ4XS and QAT builds) —
timestamped reports 2026-06-18 → 2026-06-24. Latest report (2026-06-24, 128k ctx):
auto-score **32/32** (single-tool 8/8, args 6/6, multi-step 5/5, restraint 5/5,
hallucination 5/5, cutoff-routing 3/3), 90.2 tok/s generation, 4.98 s avg
latency/turn, 0% XML mangle. Manual-review categories (persona, two-face, Discord
relay, vision) are graded by hand in the report files. These are the frozen
baselines for any model swap.

## Qwen 3.6 35B-A3B engine swap (2026-09-27, branch `qwen3.6-35b`)

**Symptom (why it happened):** the quantized Gemma-4-E4B agent kept misbehaving in
the same four ways the reliability campaign documented (hallucinated execution,
post-tool apathy, degenerate loops, long-context degradation), and the owner was
already at the model's quality ceiling after an extensive build-up of mitigations.

**Root cause:** model capability, not the harness. Phase-0 baseline on the swapped
engine resolved the harness question: native path, 0.0% text-tool-syntax leaks,
0.0% JSON decode failures, loop-discipline 5/5, clean role handling.

**What shipped (code):** all Gemma/XML machinery deleted from the engine path —
`_legacy_xml_parse`/`_get_xml_tool_prompt`/`_TOOL_CALL_REMINDER`, the
`USE_NATIVE_TOOL_CALLS` rollback flag and every dual-parse branch, the `<image>`
marker injection + INST-token stripping, the Gemma/QAT leak-scrub strip list, the
Gemma audio-head (`input_audio`) voice-note route, and the archived YOLO string
blocks. Adapter renamed `_execute_gemma_completion` → `_execute_llm_completion`.
`N_CTX` 131072 → 60000. See commits `0454616`, `c59c409`, `c4929eb`, `2cdaebd`.

**Numbers (measured, this install):**
- decode **~40 tok/s** hot (BeeLlama v0.4.7, `--threads 8` P-cores, MTP draft
  acceptance ~0.90, `ubatch 512`); ~32 tok/s on v0.4.4 and ~24-31 with the earlier
  configs. Prefill ~1.5-1.8k tok/s at ub 512, versus ~178 at ub 256 (the
  RAM-crash mitigation was the single biggest prefill cost).
- VRAM **~10.4 GB (model + GPU mmproj) / 12.3** at `--n-cpu-moe 26 --ctx-size 60000`; the MoE is
  bandwidth-bound on DDR4-3200 dual channel, not GPU-bound.
- Harness battery (52 scenarios): **31/37 auto (84%) at temp 1.0**, **27/37 (73%)
  at temp 0.7** — temp 1.0 is the keeper default (config already 1.0).

**Residual failure family (LIVE — not settled):** "narrate instead of act" —
on trivial instant actions the model answers in persona without calling the tool
("The timer is set, Sir."). Six prompts: time / pause Spotify / skip song /
screenshot / screenshot+describe / 10-min timer. Production's
`_claims_tool_execution` single-retry catches the explicit claims; the fix lever is
tool-law wording in `_shared_tool_laws.md`, to be A/B'd on the battery. Tracked in
`engine_testing/qa/qwen-swap-live-validation.md` T3.

**Fences produced (do not re-enter):**
1. **Never re-add `<image>` marker injection or INST-token stripping** — the
   froggeric template places vision tokens itself; those were Gemma-template
   workarounds.
2. **Never re-add `input_audio`** — the Qwen mmproj is vision-only; voice notes
   are text via Whisper.
3. **Do not chase BeeLlama as a slow fork** — A/B against mainline b9568 was a
   dead heat on decode; the fork's KVarN + v0.4.7 GQA decode kernels are the win.
4. **`--ubatch-size 256` was a RAM-crash mitigation, not a tuning choice** — it
   costs ~8× prefill; only lower it if commit charge is near the limit.
5. **The Gemma engine is preserved on branch `gemma-4-e4b-lightweight`** — roll
   back by switching branches, not by restoring deleted code.
6. **Never keep a second CUDA model resident beside the engine.** A resident CUDA
   Whisper (~1.2 GB) starves llama-server's compute buffers: the same image prompt
   prefilled at **248 tok/s without it vs 17 tok/s with it** (2026-09-27). Voice
   notes use the CPU instance (~5.6 s/note); only a LiveKit call loads CUDA Whisper.
7. **The mmproj must be served on the GPU.** Offloading the projector to RAM
   (`--no-mmproj-offload`) costs **15x on image prefill** (248 → 17 tok/s); it also
   slowed text prefill. It is not a VRAM-saving freebie.

**Status:** SETTLED in code; **live validation OPEN** (boot, vision round-trip,
tool-calling behavior, Whisper route, harness regression) — checklist in
`engine_testing/qa/qwen-swap-live-validation.md`.

## Provenance and maintenance

Authored 2026-07-05 from docs + live-code verification (no git history exists).

- Re-check any "FIXED/STALE" claim: the one-liners in each entry, e.g.
  `Select-String -Path tools\awareness.py -Pattern "AWARENESS_ACTIVE ="`,
  `Select-String -Path core\brain.py -Pattern "Florence"` (expect no hits),
  `Select-String -Path webrtc_bridge.py -Pattern "devmode"`
- New battles: append entries in the same four-field format; never delete old ones.
