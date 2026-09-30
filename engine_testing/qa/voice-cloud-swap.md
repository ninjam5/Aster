# Voice cloud swap — Deepgram Flux (STT + TTS) behind a flag

**Status:** implemented 2026-09-30, offline-tested, **live call validation PENDING**.
**Owner intent:** the local `Faster-Whisper + Kokoro` call stack needs more hardware
than is currently available; route the **LiveKit call** through Deepgram's cloud so
Aster stays functional, while keeping the local engine as the default and one flag
away. Scope is the LiveKit call path only — Telegram voice notes and the CLI stay
local.

## What changed

| Piece | Local (default) | Cloud (`deepgram`) |
|---|---|---|
| STT | `LocalWhisperSTT` (medium.en) + Silero VAD, wrapped in `stt.StreamAdapter` | `deepgram.STTv2` — **Flux** (`flux-general-en`, `/v2/listen`), native streaming, no VAD/StreamAdapter |
| TTS | `LocalKokoroTTS` (24 kHz mono PCM) | `DeepgramFluxTTS` (in-repo) — **Flux TTS** `flux-colin-en` (British "Colin"), `POST /v2/speak`, 24 kHz mono linear16 |

- **config.py** — `voice.stt_provider` / `voice.tts_provider` (`local` | `deepgram`),
  plus `deepgram_stt_model`, `deepgram_stt_keyterms`, `deepgram_tts_model`.
  Resolved to `config.STT_PROVIDER` / `config.TTS_PROVIDER` **fail-closed**: a
  `deepgram` request with no `deepgram.api_key` in `secrets.yaml` resolves back to
  `local` (with a boot warning). `config.DEEPGRAM_AVAILABLE` mirrors the other
  `*_AVAILABLE` flags.
- **deepgram_tts.py** — hand-rolled `DeepgramFluxTTS` / `FluxChunkedStream`.
  The installed `livekit-plugins-deepgram` **1.5.7** (pinned to `livekit-agents`
  1.5.7) only ships Aura voices on `/v1/speak`; Flux TTS is on `/v2/speak` and only
  arrived in plugin ≥1.6, which forces `livekit-agents` ≥1.6 and risks the
  `AgentServer`/`rtc_session` API the bridge is built on. So the adapter speaks the
  small REST surface directly (we always have the full reply text at `_speak()`).
- **webrtc_bridge.py** — `_build_stt()` / `_build_tts()` factories pick the engine;
  `aclose()` only calls `release_whisper_model()` on the local path and closes the
  STT once (on the cloud path `_streaming_stt` *is* `_base_stt`).

## Deliberately NOT done

- **No `livekit-agents` / `livekit-plugins-deepgram` upgrade.** The pinned 1.5.7
  stack stays; that is the whole reason the TTS adapter is hand-rolled. Do not
  `pip install -U livekit-plugins-deepgram`.
- **Telegram voice notes stay local** (CPU Whisper, zero VRAM) — not the bottleneck.

## What is lost on the cloud path (by design)

- On-call **`[Speaker: X]` voice ID** and voice-prosody **`[Mood: X]`** (the brain
  still adds a Tier-0 **text** mood via `_maybe_tag_text_mood`, so mood degrades
  rather than vanishing). Voice ID also means no unknown-speaker Telegram alert.
- Aster's **cloned Kokoro voice** — Flux uses the fixed Aura/Flux catalog ("Colin").
- Audio **leaves the machine**; needs internet; costs per minute.

## Tests (offline — no network, no models)

`tests/test_voice_providers.py` (28 tests) covers: the real fail-closed resolver
(`config._resolve_voice_provider` — local/deepgram, blank, whitespace, None,
mixed-case) and keyterm normalizer (`config._normalize_keyterms` — None, scalar,
list, blank, non-list), the shipped example's `local` defaults, `_build_stt`
local vs deepgram (Flux device, no VAD, direct streaming, credential forwarded),
`_build_tts` local vs deepgram, `DeepgramFluxTTS` requires a key, request URL
(`model=flux-colin-en`, `linear16`, `container=none`, 24000), an end-to-end
streaming `synthesize()` (real `ChunkedStream`, fake HTTP), the error path
(reported via `diagnostics.send_error`, then **raised** — not swallowed), a
real-stream failure that must propagate **without** retrying into a duplicate-POST
storm, the session create/close lifecycle, and `ManualWebRTCBridge.aclose`
(cloud path closes the shared STT once + skips the Whisper release; local path
closes both + releases Whisper). `tests/test_config_cleanup.py` now scans
`deepgram_tts.py` and the `DEEPGRAM_API_KEY` value for hardcoded-literal leaks.

Full suite: **853 passed** (2026-09-30; was 825 before this change).

## QA review (3 read-only reviewers, 2026-09-30) + follow-ups

- **Invariant/regression reviewer** — confirmed the local path is behaviorally
  identical (same objects, same order), `aclose()` semantics preserved. Fixed:
  `deepgram_stt_keyterms` could be `None`/scalar and crash `import config`
  (now `_normalize_keyterms`); a whitespace-only key now counts as unconfigured
  (stripped); `aclose` gates on a snapshotted `_stt_is_local`. Also fixed a
  pre-existing `.gitignore` bug where `secrets.*.yaml` wrongly ignored the tracked
  `secrets.example.yaml` (added `!secrets.example.yaml`; the template is now
  staged).
- **Adapter/protocol reviewer** — confirmed the LiveKit 1.5.7 contract, URL/headers,
  sample-rate match, and Flux STT event handling. Fixed: the adapter no longer
  blanket-swallows errors (which silently truncated partial audio and turned a
  hard failure into a 3× retry + 3× diagnostic storm) — it reports once and
  **propagates**; and the HTTP request now has a bounded **total** timeout (was
  connect-only, so a stalled server could hang the response pipeline).
- **Test-quality reviewer** — the resolution tests were a self-referential copy;
  replaced with direct calls to the real `config` helpers, plus added the
  `aclose`/keyterms/session tests above and tightened the propagation assertion to
  `RuntimeError, match="HTTP 500"`.

**Accepted / deferred (not fixed):**
- *Idle Flux STT WebSocket while asleep.* With the wake word enabled, the bridge
  opens the STT stream (and, on the Deepgram path, a billed `/v2/listen` WebSocket)
  at track-subscribe even while Aster is asleep and frames are routed to
  openWakeWord. Local Whisper does no compute in that state; Deepgram bills an idle
  connection. Fix = open the STT stream lazily on the first awake frame (deferred to
  avoid restructuring the `_forward_audio`/`_consume_stt_events` gather).
- `first_run_setup.py` has no `voice.*` / `deepgram` prompts yet, so the feature is
  configured by editing yaml, not the setup wizard.


## Manual live checklist (run on a real call)

- [ ] Flag OFF (or no key): a call behaves exactly as before — Whisper transcript,
      Kokoro voice, barge-in works. (Local regression.)
- [ ] Flag ON, key present: boot log shows `[Aster Ears] STT: Deepgram Flux` and
      `[Aster Voice] Using Deepgram Flux TTS voice: flux-colin-en`.
- [ ] Speech is transcribed (Flux), the reply is spoken in Colin (British), and
      **barge-in** interrupts Aster mid-reply.
- [ ] Wake word still gates audio while asleep; the `_addressed_to_aster` gate still
      drops side-conversation/TV.
- [ ] A forced TTS/STT failure (e.g. pull the key) logs a diagnostic and does **not**
      crash the call/process.
- [ ] Telegram voice note still transcribes locally (unchanged).
- [ ] VRAM after a cloud call is *lower* than after a local call (no CUDA Whisper
      held): `nvidia-smi --query-gpu=memory.used --format=csv`.

## Reverting

Set `voice.stt_provider: local` and `voice.tts_provider: local` (or blank the
`deepgram.api_key`). No code change needed — `local` is the default and the original
code path.

## Provenance

- Flux STT: `https://developers.deepgram.com/docs/flux/quickstart` (`/v2/listen`,
  `flux-general-en`).
- Flux TTS: `https://developers.deepgram.com/docs/flux-tts/quickstart` (`/v2/speak`,
  model `flux-{voice}-{language}`).
- Plugin Flux support landed after 1.5.7 (verified by inspecting the 1.5.18 wheel —
  still Aura-only — vs the 1.8.3 wheel, which adds `tts_v2.py`/`TTSv2`).
