---
name: aster-config-and-flags
description: Load this when reading, changing, or adding any Aster configuration - flags in config.py, knobs in self_config.yaml, credentials in secrets.yaml, environment variables, runtime toggles, or when asked "where is X configured", "what does flag Y default to", "how do I disable Z", "add a new setting". Contains the full flag catalog with defaults and the add-a-flag checklist.
---

# Aster Config and Flags

**SAFETY RULE (absolute):** never open the real `self_config.yaml` or `secrets.yaml`
— they hold personal data and credentials. Read `self_config.example.yaml` /
`secrets.example.yaml` for structure, and `config.py` for defaults. Defaults below
are `config.py`'s fallbacks; the real per-install yaml may override them.

**When NOT to use this skill:** VRAM policy → `aster-vram-discipline`; shipping the
change → `aster-change-control`; what a subsystem does → root `CLAUDE.md`.

## The three layers

1. **`config.py`** — code constants. Loads the two yamls at import via `_cfg(*path,
   default=)` and `_secret(*path, default=)`. Import never fails: missing files →
   `{}` → defaults.
2. **`self_config.yaml`** (gitignored) — identity, paths, integrations-intent,
   tunable knobs. Exposed **verbatim to the LLM** via the `get_my_config` tool, so
   nothing secret may ever be added to it.
3. **`secrets.yaml`** (gitignored) — every credential (spotify, telegram, discord,
   livekit, firecrawl, google). Deliberately separate loader so introspection can
   never leak it. Blank/missing ⇒ integration disabled gracefully.

`python first_run_setup.py` writes both interactively (safe to re-run; existing
values become defaults). It is NOT auto-invoked by main.py.

**Fail-closed gates** (verified in config.py): `SPOTIFY_AVAILABLE`,
`TELEGRAM_AVAILABLE`, `GOOGLE_AVAILABLE`, `MEMORY_AVAILABLE`; LiveKit/Discord have
their own empty-credential checks in their modules.

## Flag catalog (defaults verified in config.py; runtime rows updated for the Qwen swap 2026-09-27)

Status: **P** = production/always-on path · **O** = opt-in (default OFF) ·
**L** = legacy/no-op.

### Runtime / LLM
| Flag | Yaml path | Default | Status | Notes |
|---|---|---|---|---|
| `MODEL_NAME` | — (constant) | `Qwen3.6-35B-A3B-UD-IQ4_XS` | P | Sent as `model=`; server ignores it |
| `N_CTX` | `runtime.context_window` | 60000 | P | **Must match start.bat `--ctx-size 60000`** |
| `LLM_TEMPERATURE` | `settings.llm_temperature` | 1.0 | P | Sub-task calls override temp only (vision 0.2) |
| `LLM_TOP_P` / `LLM_TOP_K` | `settings.llm_top_p/top_k` | 0.95 / 64 | P | |
| `llm` | — | `None` | **L** | Back-compat placeholder |

### Wake word / voice
| Flag | Yaml path | Default | Status |
|---|---|---|---|
| `WAKE_WORD_ENABLED` | `runtime.wake_word_enabled` | True | P |
| `WAKE_WORD_MODEL` | `runtime.wake_word_model` | `hey_jarvis` | P (built-ins: alexa, hey_mycroft, hey_rhasspy, or custom .onnx path) |
| `WAKE_WORD_THRESHOLD` | `runtime.wake_word_threshold` | 0.5 | P |
| `WAKE_INACTIVITY_TIMEOUT` | `runtime.wake_inactivity_timeout` | 300 s | P |
| `VOICE_SPEED` | `settings.voice_speed` | 1.0 | P |
| `VOICE_NAME` | `settings.voice_name` | `af_bella` (example yaml ships `af_nova`) | P — runtime-switchable via `POST /api/voice/select` |
| `UTTERANCE_DEBOUNCE` | `settings.utterance_debounce` | 0.6 s | P |

### Persona / identity / contacts
| Flag | Yaml path | Default | Notes |
|---|---|---|---|
| `SYSTEM_PROMPT` | `persona.system_prompt` | `jarvis` | Filename stem in `Aster_Vault/System_Prompts/`; THE persona switch |
| `OWNER_NAME` | `identity.owner` | `User` | Gates ambient face/voice mood trust |
| `DISCORD_FEMALE_NAMES` | `contacts.female_names` | `[]` | Honorific rewrite list — per-install, never hardcode names |

### Emotion pipeline
| Flag | Yaml path | Default | Status |
|---|---|---|---|
| `EMOTION_ENABLED` | `emotion.enabled` | True | P (master switch) |
| `EMOTION_TEXT_MODEL` | `emotion.text_model` | j-hartmann/emotion-english-distilroberta-base | P |
| `EMOTION_VOICE_MODEL` | `emotion.voice_model` | speechbrain/emotion-recognition-wav2vec2-IEMOCAP | P (lazy CUDA) |
| `EMOTION_MIN_CONFIDENCE` | `emotion.min_confidence` | 0.5 | P — THE false-positive lever (raise to 0.6 if energetic speech mis-tags) |
| `FACE_EMOTION_ENABLED` + `_MODEL`/`_MIN_CONFIDENCE`/`_EMA_ALPHA` | `emotion.face.*` | False / enet_b2_8 / 0.5 / 0.5 | **O** — toggle_face_emotion, `/faceemotion` |
| `AMBIENT_AUDIO_ENABLED` + device/vad/min-utterance/ema/gate_owner/feed-cooldown | `emotion.ambient_audio.*` | False / None / 2 / 2.0 s / 0.5 / True / 60 s | **O** — toggle_ambient_audio, `/ambientaudio` |

### Mood features (emotion roadmap)
| Flag | Yaml path | Default | Status |
|---|---|---|---|
| `MOOD_TREND_ENABLED` | `emotion.mood_trend.enabled` | True | P |
| `MOOD_SUSTAIN_TURNS` | `emotion.mood_trend.sustain_turns` | 3 | P — shared debounce for Ideas 1 & 2 (deliberately no per-system knob) |
| `MOOD_LOG_RETENTION_DAYS` / `MOOD_FLUSH_INTERVAL_HOURS` / `MOOD_FLUSH_MIN_TURNS` / `MOOD_FLUSH_NONNEUTRAL_FRAC` | `emotion.mood_trend.*` | 30 / 24 / 6 / 0.4 | P |
| `MOOD_ACTIONS_ENABLED` / `MOOD_ACTIONS_COOLDOWN` | `emotion.mood_actions.*` | False / 1800 s | **O** — toggle_mood_actions, `/moodactions` |
| `MOOD_CHECKIN_ENABLED` / `_QUIET_SECONDS` / `_COOLDOWN` | `emotion.proactive_checkin.*` | False / 90 s / 3600 s | **O** — toggle_mood_checkins, `/checkins`; needs initiative ≥ medium + Awareness daemon |
| `MOOD_TOUCH_COOLDOWN` | `emotion.mood_shared.touch_cooldown_seconds` | 120 s | P — min gap between ANY two mood nudges |

### Awareness / initiative
| Flag | Yaml path | Default | Notes |
|---|---|---|---|
| `AWARENESS_ACTIVE` | `daemons.awareness_mode` | True | **Now honored** — `tools/awareness.py:29` reads it from config. (Root CLAUDE.md's gotcha "yaml flag ignored / hardcoded True" and the example-yaml comment are STALE as of 2026-07-05.) |
| `AWARENESS_INTERVAL` | `awareness.interval` | 300 s | Poll cadence (also freshness bound for face-mood fusion) |
| `AWARENESS_ABSENCE_THRESHOLD` | `awareness.absence_threshold` | 900 s | Return-compliment gate |
| `AWARENESS_ENV_COOLDOWN` | `awareness.env_cooldown` | 7200 s | |
| `INITIATIVE_LEVEL` | `settings.initiative_level` | `medium` (=2) | 0 silent … 3 high; runtime `/initiative`, set_initiative tool |
| `INITIATIVE_LEVELS` | — (code matrix) | per-level caps | Also gates `mood_checkin` & `email_checkin` capabilities (≥2) |
| `INITIATIVE_QUIET_HOURS` | `awareness.initiative_quiet_hours` | None | `[start,end]` hours |

### Intervention
| Flag | Yaml path | Default | Notes |
|---|---|---|---|
| `INTERVENTION_CHECK_INTERVAL` | `intervention.check_interval` | 30 s | |
| `INTERVENTION_THRESHOLD` | `intervention.threshold` | **60 s** | summary.md's "default 1800s" is stale |
| `INTERVENTION_COOLDOWN` | `intervention.cooldown` | 900 s | |
| `INTERVENTION_QUIET_HOURS` | `intervention.quiet_hours` | None | |
| `DISTRACTION_KEYWORDS` | `intervention.distraction_keywords` | dict incl. youtube/reddit/…/Stremio | Title-substring → label map |

### Google (Gmail + Calendar)
| Flag | Yaml path | Default | Notes |
|---|---|---|---|
| `GOOGLE_ACCESS_TIER` | `integrations.google.access_tier` | `limited` | limited / partial / autonomous; persisted via `tools/config_writer.set_config_values()` (a standing choice, not daemon state) |
| `AUTONOMOUS_SEND_DAILY_CAP` | `integrations.google.autonomous_daily_cap` | 10 | |
| `GOOGLE_REAUTH_REMINDER_ENABLED` | `integrations.google.reauth_reminder` | True | ~7-day Testing-status token lapse |
| `EMAIL_CHECKIN_ENABLED` / `_THRESHOLD` / `_COOLDOWN` | `integrations.google.email_checkin.*` | False / 3 / 3600 s | **O** |

### Paths / memory (all under `paths.*` / `memory.*`, defaults inside `Aster_Vault/`)
`VAULT_DIR`, `MD_FILE` (memory.md), `CHROMADB_PATH` (chroma_db),
`FACT_COLLECTION` (`aster_long_term_memory`), `RAG_VAULT_DIR` (database),
`RAG_STALE_DAYS` (90), `COLD_STORAGE_DIR` (images), `VOICES_DIR`,
`DISCORD_CONTACTS_FILE`, `SYSTEM_PROMPTS_DIR`, `CUSTOM_VOICES_DIR` (TTS-Voices),
`CONVERSATIONS_DIR`, `MOOD_LOG_PATH` (emotion_log.jsonl), `MOOD_TRENDS_FILE`,
`MOOD_FLUSH_STATE_FILE`, `GOOGLE_TOKEN_PATH` (google_token.json).

### Secrets (structure only — see secrets.example.yaml)
`spotify.client_id/client_secret/redirect_uri` (default redirect
`http://127.0.0.1:8081` — deliberately NOT 8080, which is llama-server's port),
`telegram.bot_token/authorized_chat_id`, `discord.bot_token`,
`livekit.url/api_key/api_secret`, `firecrawl.api_key`,
`google.client_id/client_secret`.

### Environment variables
`TESSERACT_CMD` — overrides the Tesseract executable path (default
`C:\Program Files\Tesseract-OCR\tesseract.exe`).

## Runtime toggles vs standing config

- **Runtime (in-memory, lost on restart):** all `toggle_*` tools and their Telegram
  twins (`/sentry`, `/gesture`, `/intervention`, `/moodactions`, `/checkins`,
  `/faceemotion`, `/ambientaudio`, `/initiative`, `/diagnostics`), plus
  `config.DIAGNOSTICS_MODE`, `STOP_REQUESTED`, `SHUTDOWN_REQUESTED`. Live state is
  read from module globals by `get_my_status` — never from yaml.
- **Standing (persisted to self_config.yaml):** persona, voice, Google tier — via
  `tools/config_writer.set_config_values()` (used by the dashboard endpoints).

## Add-a-flag checklist

1. Add the key with a comment to **`self_config.example.yaml`** (and
   `secrets.example.yaml` instead if it's a credential).
2. Read it in `config.py` with an explicit default:
   `X = bool(_cfg("section", "key", default=False))` — experimental features
   default to the safe/off value.
3. Consume `config.X` in the owning module (import config, don't re-read yaml).
4. If runtime-togglable: add the toggle tool (three-edit rule →
   `aster-change-control` §A) and the Telegram command in `main.py`.
5. Tests + per-feature checklist; update root CLAUDE.md.
6. Never touch the real yamls in the repo working tree.

## Provenance and maintenance

Authored 2026-07-05; every default read from config.py the same day. Runtime LLM
rows (`MODEL_NAME`, `N_CTX`) updated 2026-09-27 for the Qwen 3.6 35B-A3B swap.
Flags drift — re-verify with:

- Full constant dump: `Select-String -Path config.py -Pattern "^[A-Z_]+\s*="`
- A specific default: `Select-String -Path config.py -Pattern "<FLAG_NAME>"`
- Yaml structure: `Get-Content self_config.example.yaml` / `Get-Content secrets.example.yaml`
- Awareness-flag honor status: `Select-String -Path tools\awareness.py -Pattern "AWARENESS_ACTIVE ="`
