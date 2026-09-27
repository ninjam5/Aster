import os
import threading
import telebot
import chromadb
import yaml

# ============================================================================
# SELF-CONFIG LOADER — self_config.yaml is the single source of truth for
# paths, identity, contacts, intended integrations, and tunable knobs. This
# file is per-install (gitignored); self_config.example.yaml is the public
# template. Credentials do NOT live here — see the SECRETS loader below.
# Exposed verbatim to the LLM via the get_my_config introspection tool, so
# nothing secret may ever be added to this file.
# ============================================================================
SELF_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "self_config.yaml")


def _load_self_config() -> dict:
    """Load self_config.yaml; fall back to {} on any error so import never fails."""
    try:
        with open(SELF_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            print(f"[Aster Config] WARNING: self_config.yaml root is not a mapping. Using defaults.")
            return {}
        return data
    except FileNotFoundError:
        print(f"[Aster Config] WARNING: self_config.yaml not found at {SELF_CONFIG_PATH}. Using defaults.")
        return {}
    except Exception as e:
        print(f"[Aster Config] WARNING: failed to read self_config.yaml ({e}). Using defaults.")
        return {}


SELF_CONFIG: dict = _load_self_config()


def _cfg(*path, default=None):
    """Walk SELF_CONFIG by dotted-path (e.g. _cfg('memory', 'fact_collection'))."""
    node = SELF_CONFIG
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# ============================================================================
# SECRETS LOADER — secrets.yaml holds every credential (Spotify/Telegram/
# Discord/LiveKit/Firecrawl). Per-install, gitignored; secrets.example.yaml
# is the public template. Never read by get_my_config — this loader is
# intentionally separate from SELF_CONFIG/_cfg so credentials can never leak
# through that introspection tool. Missing file or missing keys fall back to
# blank/zero, which every integration below treats as "not configured" and
# disables gracefully rather than crashing.
# ============================================================================
SECRETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets.yaml")


def _load_secrets() -> dict:
    """Load secrets.yaml; fall back to {} on any error so import never fails."""
    try:
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            print(f"[Aster Config] WARNING: secrets.yaml root is not a mapping. Using defaults.")
            return {}
        return data
    except FileNotFoundError:
        print(f"[Aster Config] NOTE: secrets.yaml not found. Copy secrets.example.yaml to secrets.yaml "
              f"(or run first_run_setup.py) to enable Spotify/Telegram/Discord/LiveKit/Firecrawl. "
              f"Continuing with those integrations disabled.")
        return {}
    except Exception as e:
        print(f"[Aster Config] WARNING: failed to read secrets.yaml ({e}). Using defaults.")
        return {}


SECRETS: dict = _load_secrets()


def _secret(*path, default=None):
    """Walk SECRETS by dotted-path (e.g. _secret('spotify', 'client_id'))."""
    node = SECRETS
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# Initiative level natural-language → integer mapping (used at boot).
_INITIATIVE_NAME_TO_INT = {"silent": 0, "low": 1, "medium": 2, "high": 3}

# ============================================================================
# SPOTIFY CLIENT
# ============================================================================
# Redirect URI defaults to :8081 (not :8080) to avoid colliding with
# llama-server's port during the local OAuth callback. If you've already
# registered a Spotify app on :8080, set redirect_uri explicitly in
# secrets.yaml to match — it must be an exact match on Spotify's dashboard.
SPOTIPY_CLIENT_ID = _secret("spotify", "client_id", default="")
SPOTIPY_CLIENT_SECRET = _secret("spotify", "client_secret", default="")
SPOTIPY_REDIRECT_URI = _secret("spotify", "redirect_uri", default="http://127.0.0.1:8081")

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    if SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET:
        sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=SPOTIPY_CLIENT_ID,
                client_secret=SPOTIPY_CLIENT_SECRET,
                redirect_uri=SPOTIPY_REDIRECT_URI,
                scope="user-read-playback-state,user-modify-playback-state,playlist-read-private,user-library-read",
            )
        )
        SPOTIFY_AVAILABLE = True
    else:
        sp = None
        SPOTIFY_AVAILABLE = False
except ImportError:
    sp = None
    SPOTIFY_AVAILABLE = False

# ============================================================================
# TELEGRAM BOT
# ============================================================================
TELEGRAM_BOT_TOKEN = _secret("telegram", "bot_token", default="")
AUTHORIZED_CHAT_ID = int(_secret("telegram", "authorized_chat_id", default=0) or 0)

try:
    if TELEGRAM_BOT_TOKEN:
        bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=True)
        TELEGRAM_AVAILABLE = True
    else:
        bot = None
        TELEGRAM_AVAILABLE = False
except Exception:
    bot = None
    TELEGRAM_AVAILABLE = False

# ============================================================================
# FIRECRAWL
# ============================================================================
FIRECRAWL_API_KEY = _secret("firecrawl", "api_key", default="")

# ============================================================================
# THREADING & GLOBAL STATE
# ============================================================================
brain_lock = threading.Lock()
SHUTDOWN_REQUESTED = False
OS_SHUTDOWN_DELAY = 0
STOP_REQUESTED = False       # Set by /stop — checked between tool rounds to abort the current run
DIAGNOSTICS_MODE = False     # Set by /diagnostics on|off — forwards stdout to Telegram

# ============================================================================
# MODEL CONFIGURATION — llama-server OpenAI-compatible client
# ============================================================================

# Model name sent as the `model=` parameter to llama-server (server ignores it
# when only one model is loaded, but the field is required by the OpenAI schema).
MODEL_NAME = "gemma-e4b-q4km"

# Audio routing: "native" = OpenAI input_audio block, "whisper" = Faster-Whisper STT
# NOTE: Native audio is bypassed in main.py (`if False:`) to prevent GPU inference stalls.
# AUDIO_MODE is kept for config compatibility but has no runtime effect.
AUDIO_MODE = "whisper"

# Context window ceiling — must match llama-server's --ctx-size.
# Kept at 128k to leave VRAM headroom for the multimodal image pipeline.
N_CTX = int(_cfg("runtime", "context_window", default=131072))

# Utterance coalescing debounce — how long to wait after a VAD segment ends
# before sending to the brain. Rapid mid-sentence pauses within this window
# are merged into one turn. Raise if fragmentation persists; lower for faster response.
UTTERANCE_DEBOUNCE = float(_cfg("settings", "utterance_debounce", default=0.6))

# Kokoro TTS playback speed multiplier (1.0 = natural). Applied at both synthesis
# call sites (tools/audio.py, local_tts.py).
VOICE_SPEED = float(_cfg("settings", "voice_speed", default=1.0))

# Kokoro TTS voice identifier. Can be a built-in voice name (e.g. "af_bella",
# "am_michael") or an absolute path to a custom .pt tensor (e.g. "aster.pt").
# Overridden at runtime by POST /api/voice/select. The custom aster.pt / aster2.pt
# clones take priority in local_tts.py only when this is set to the default.
VOICE_NAME = str(_cfg("settings", "voice_name", default="af_bella"))

# LLM sampling parameters — defaults applied to all main-conversation completions.
# Sub-task calls (screen description, webcam, compaction) override temperature only.
LLM_TEMPERATURE = float(_cfg("settings", "llm_temperature", default=1.0))
LLM_TOP_P       = float(_cfg("settings", "llm_top_p",       default=0.95))
LLM_TOP_K       = int(_cfg("settings",   "llm_top_k",       default=64))

# Sampling for the agentic tool-loop rounds specifically (the hallucinate-vs-call
# decision) — separate from the main knobs above so persona-facing final replies
# are unaffected by any future cooling of tool-decision sampling. Default =
# the main knobs, which is a byte-identical no-op until deliberately changed.
# NOTE: a loop round that happens to produce the final text (e.g. round 0 of a
# pure-chat turn) is sampled with THESE settings, not the main ones — see
# aster-gemma-reliability-campaign skill before changing away from the default.
LLM_TOOL_TEMPERATURE = float(_cfg("settings", "llm_tool_temperature", default=LLM_TEMPERATURE))
LLM_TOOL_TOP_P       = float(_cfg("settings", "llm_tool_top_p",       default=LLM_TOP_P))
LLM_TOOL_TOP_K       = int(_cfg("settings",   "llm_tool_top_k",       default=LLM_TOP_K))

# ============================================================================
# RELIABILITY CAMPAIGN — instrumentation + mitigations for agent failure
# modes (hallucinated execution, post-tool apathy, degenerate tool loops,
# long-context degradation). See .claude/skills/aster-gemma-reliability-campaign.
# ============================================================================
# Log-only counters for the existing single-retry mitigations plus round/loop
# stats, written to Aster_Vault/reliability_log.jsonl. Changes NO behavior.
RELIABILITY_LOG_ENABLED = bool(_cfg("runtime", "reliability_log_enabled", default=True))

# LoopGuard — blocks/warns on degenerate repeated tool calls within one turn
# (identical-call budget + A-B-A-B ping-pong detection). See core/loop_guard.py.
LOOP_GUARD_ENABLED = bool(_cfg("runtime", "loop_guard_enabled", default=True))

# Tool-output compression — truncates/summarizes oversized tool results before
# they enter conversation history (long-context degradation mitigation).
# See core/output_compression.py.
TOOL_OUTPUT_COMPRESSION = bool(_cfg("runtime", "tool_output_compression", default=True))

# Strip <think>/<thinking> reasoning-scaffold blocks from every completion's
# content before it re-enters history or reaches the user/TTS.
STRIP_THINK_TAGS = bool(_cfg("runtime", "strip_think_tags", default=True))

# Use llama-server's /tokenize endpoint to calibrate trim_memory()'s chars-per-
# token ratio instead of the fixed //4 heuristic. Calibration is cached and
# rate-limited (see core/memory.py) — this does NOT add a network call per turn.
EXACT_TOKEN_COUNT = bool(_cfg("runtime", "exact_token_count", default=True))

# Streaming TTS (Level 1) — push Kokoro audio chunks into the LiveKit call as
# each segment is synthesized instead of concatenating the whole reply first.
# Speech starts after the first sentence's audio is ready. See local_tts.py.
TTS_CHUNK_STREAMING = bool(_cfg("runtime", "tts_chunk_streaming", default=True))

# Structured tool results — tools migrated to return a ToolResult(ok, text)
# envelope (core/tool_result.py) get EXACT failure detection in the
# failure-blind-claim guard instead of the FAILED/Error prefix regex. False =
# always use the legacy regex heuristic even for envelope returns (rollback).
STRUCTURED_TOOL_RESULTS = bool(_cfg("runtime", "structured_tool_results", default=True))

# Auto-compact — when the estimated context reaches AUTO_COMPACT_THRESHOLD of
# N_CTX at the start of a turn, run the same summarize-and-replace compaction
# as the manual /compact command (trimming drops facts silently; compaction
# preserves them). The triggering turn pays a one-time summarization delay.
AUTO_COMPACT_ENABLED   = bool(_cfg("runtime", "auto_compact", default=True))
AUTO_COMPACT_THRESHOLD = float(_cfg("runtime", "auto_compact_threshold", default=0.75))

# Icon captioner for the GUI locator's Track-2 fallback (YOLO icon boxes with
# no readable text). "llm" (default) captions crops with the resident main
# LLM via llama-server — zero extra VRAM. "off" disables captioning (Track 2
# then matches OCR-readable boxes only). The Florence-2 icon_caption experiment
# was dropped (never wired).
ICON_CAPTIONER = str(_cfg("vision", "icon_captioner", default="llm")).lower()

# ============================================================================
# AUTOMATION MOTOR (DOM-first perception; see tools/dom.py, core/system1.py)
# ============================================================================
# USE_DOM_MOTOR gates the DOM-first perception motor: web tasks go through the
# attached browser's accessibility tree (Playwright over CDP), Windows tasks
# through a richer UIA shortlist, before the OCR/YOLO tracks are consulted.
# Default OFF — promoted only after the stage QA gates pass.
USE_DOM_MOTOR = bool(_cfg("automation", "dom_motor", default=False))

# Port of the Chromium/Edge instance the motor attaches to via
# --remote-debugging-port. Loopback only; opened on demand (open_application
# does it when the motor is enabled). While open, any local process can drive
# the browser, so the owner closes it by closing that browser instance.
# Chrome >=136 ignores the debugging switch on the default profile unless an
# explicit --user-data-dir is passed too — open_application passes the real
# profile, so the browser must be FULLY closed first (profile lock).
BROWSER_CDP_PORT = int(_cfg("automation", "browser_cdp_port", default=9222))

# Send/submit/destructive clicks on web pages: "confirm" refuses the click and
# tells the model to get owner confirmation, then re-call with confirm_send=true;
# "allow" disables the gate. Mirrors the gated-outward-actions rule.
DOM_MOTOR_SEND_POLICY = str(
    _cfg("automation", "dom_motor_send_policy", default="confirm")
).lower()

# Shortlist ceiling — System-1 choice accuracy degrades past ~20 options, so the
# motor never hands a bigger list to the decision step.
DOM_MOTOR_SHORTLIST_K = int(_cfg("automation", "dom_motor_shortlist_k", default=18))

# Minimum score for the motor to auto-act without the System-1 kernel.
DOM_MOTOR_MIN_SCORE = float(_cfg("automation", "dom_motor_min_score", default=0.55))

# Stage-2 rollback flag for the OmniParser/YOLO Track-2 pixel fallback. Kept
# TRUE until the locator eval shows DOM coverage; flipping it off demotes the
# pixel track (OCR Track 1 still covers text-only surfaces).
USE_PIXEL_FALLBACK = bool(_cfg("automation", "pixel_fallback", default=True))

# If the CDP attach fails because the owner's browser is already running without
# the debug port, the motor can launch its OWN Playwright Chromium instead. This
# is what makes autonomous browsing (browse_web) work regardless of the state of
# the owner's Chrome. Headed by default: headless is more likely to be bot-blocked.
DOM_MOTOR_OWN_BROWSER = bool(_cfg("automation", "own_browser", default=True))
BROWSER_HEADLESS = bool(_cfg("automation", "browser_headless", default=False))
BROWSER_PROFILE_DIR = str(_cfg("automation", "browser_profile_dir", default=os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Aster_Vault", "browser_profile")))
# Drive the owner's REAL Chrome profile. IMPOSSIBLE since Chrome >=136 (settled
# experimentally 2026-09-26): the debugging port is ignored on the default
# user-data-dir, and any copy/junction of that dir loses the app-bound
# encrypted logins (Chrome purges the cookies — never attempt it). With this
# flag ON, browse_web and the motor fail LOUDLY with the supported alternative
# (one-time login in the dedicated profile, login_once.py) instead of silently
# browsing logged-out. Default OFF.
BROWSER_USE_REAL_PROFILE = bool(_cfg("automation", "use_real_profile", default=False))
# browse_web tool: open a URL (or site+query) and return the page text. Default
# ON (Playwright + bundled Chromium are already installed); set false to hide it.
WEB_BROWSE_ENABLED = bool(_cfg("automation", "web_browse", default=True))

# Human-style site search (the Astra pattern): when on, site+query opens the
# site's HOME page and the model drives the site's own search bar interactively
# (open site -> find the search box -> type -> submit -> read), instead of the
# pre-mapped search URLs in tools/dom.py _SITE_SEARCH. The map stays as the
# rollback path (flag off). Default OFF.
BROWSER_HUMAN_SEARCH = bool(_cfg("automation", "human_search", default=False))

# Stage-3 System-1 decision kernel (core/system1.py). Default OFF: when on, the
# DOM motor asks Laya to pick among ambiguous shortlist candidates and to answer
# neutral-key yes/no state gates; low-margin answers escalate back to the main LLM.
USE_LAYA_KERNEL = bool(_cfg("automation", "laya_kernel", default=False))
LAYA_MODEL_ID = str(_cfg("automation", "laya_model",
                         default="convaiinnovations/laya"))
LAYA_DEVICE = str(_cfg("automation", "laya_device", default="cpu")).lower()
# Top-1 minus top-2 probability margin required to act without escalation.
LAYA_MARGIN_THRESHOLD = float(_cfg("automation", "laya_margin_threshold", default=0.25))
# False (default) unloads the checkpoint when the last caller releases, keeping
# RAM free; true keeps it hot for latency (costs ~1-2 GB RAM, zero VRAM on CPU).
LAYA_KEEP_RESIDENT = bool(_cfg("automation", "laya_keep_resident", default=False))
# Decision log (calibration dataset for Stage 4). JSONL, one verdict per line.
LAYA_LOG_PATH = str(_cfg("automation", "laya_log_path", default=os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Aster_Vault", "system1_log.jsonl")))

# ============================================================================
# EMOTION DETECTION
# ============================================================================
# Two-tier pipeline: text (CPU, eager) + voice (CUDA, lazy/ref-counted).
# Embeds [Mood: <state>] tags into every user message before the brain sees it.
# Labels: happy | sad | frustrated | anxious | neutral
# Disable the whole system with emotion.enabled: false in self_config.yaml.
EMOTION_ENABLED         = bool(_cfg("emotion", "enabled",       default=True))
EMOTION_TEXT_MODEL      = str(_cfg("emotion",  "text_model",    default="j-hartmann/emotion-english-distilroberta-base"))
EMOTION_VOICE_MODEL     = str(_cfg("emotion",  "voice_model",   default="speechbrain/emotion-recognition-wav2vec2-IEMOCAP"))
EMOTION_MIN_CONFIDENCE  = float(_cfg("emotion","min_confidence", default=0.5))

# ── Tier 2 — face (CPU, eager, HSEmotion) ────────────────────────────────────
# Reads facial affect from the webcam frame the Awareness daemon already grabs
# (no new camera open). 8-class AffectNet output → the 5-label vocab above.
# Ambient (surfaced as "Face read:" in the awareness block) AND fused into the
# per-turn [Mood:] tag. Opt-in / default OFF for privacy and because the
# hsemotion dep + weights aren't present until installed — flip true after
# `pip install hsemotion`. Toggle at runtime via toggle_face_emotion / /faceemotion.
FACE_EMOTION_ENABLED        = bool(_cfg("emotion", "face", "enabled",        default=False))
FACE_EMOTION_MODEL          = str(_cfg("emotion",  "face", "model",          default="enet_b2_8"))
FACE_EMOTION_MIN_CONFIDENCE = float(_cfg("emotion","face", "min_confidence", default=0.5))
FACE_EMOTION_EMA_ALPHA      = float(_cfg("emotion","face", "ema_alpha",      default=0.5))

# Owner identity — the enrolled face/voice name that ambient mood reads are
# trusted from (rejects a visiting friend's expression/tone, Aster's own TTS
# echo, and background media from poisoning Mohamed's mood). Reused by the
# Tier-2 face-emotion guard and the ambient-audio owner gate.
OWNER_NAME = str(_cfg("identity", "owner", default="User"))

# Discord friends whose honorific should be "Ma'am"/"Ms." instead of the
# default "Sir"/"Mr." (see core.brain._discord_honorific). Per-install list —
# lives in self_config.yaml under contacts.female_names, empty by default.
DISCORD_FEMALE_NAMES = set(_cfg("contacts", "female_names", default=[]) or [])

# ── Tier 1b — ambient room audio (CPU, opt-in/default OFF) ────────────────────
# A dedicated local-mic daemon (tools/ambient_audio.py) that passively listens
# to the room so Aster reads Mohamed's vocal tone even when he isn't addressing
# it (e.g. upset on the phone). VAD-gated, owner-gated via ECAPA speaker ID,
# paused during LiveKit calls. The voice-emotion model runs on a CPU copy here
# (zero VRAM) — distinct from the CUDA path used by LiveKit/Telegram. Toggle at
# runtime via toggle_ambient_audio / /ambientaudio. Default OFF (privacy + the
# sounddevice/webrtcvad deps load on demand).
AMBIENT_AUDIO_ENABLED       = bool(_cfg("emotion", "ambient_audio", "enabled",            default=False))
AMBIENT_AUDIO_DEVICE        = _cfg("emotion",       "ambient_audio", "device",             default=None)  # None = system default mic
AMBIENT_VAD_AGGRESSIVENESS  = int(_cfg("emotion",   "ambient_audio", "vad_aggressiveness", default=2))    # 0..3 (webrtcvad)
AMBIENT_MIN_UTTERANCE_SECONDS = float(_cfg("emotion","ambient_audio", "min_utterance_seconds", default=2.0))
AMBIENT_VOICE_EMA_ALPHA     = float(_cfg("emotion", "ambient_audio", "ema_alpha",          default=0.5))
AMBIENT_VOICE_GATE_OWNER    = bool(_cfg("emotion",  "ambient_audio", "gate_owner",         default=True))
AMBIENT_WINDOW_FEED_COOLDOWN = float(_cfg("emotion","ambient_audio", "window_feed_cooldown", default=60.0))

# ============================================================================
# WAKE-WORD (openWakeWord)
# ============================================================================
# The voice agent starts asleep and ignores all speech until the wake word is
# detected by openWakeWord — a tiny CPU model, so the GPU/Whisper STT stays
# idle while asleep. Fully offline and free (models auto-download once on the
# first run, then need no internet). After WAKE_INACTIVITY_TIMEOUT seconds of
# silence it re-mutes. Set WAKE_WORD_ENABLED = False to start awake forever.
WAKE_WORD_ENABLED = bool(_cfg("runtime", "wake_word_enabled", default=True))
WAKE_INACTIVITY_TIMEOUT = int(_cfg("runtime", "wake_inactivity_timeout", default=300))

# Wake-word model — either a built-in pretrained name ("hey_jarvis", "alexa",
# "hey_mycroft", "hey_rhasspy") or a path to a custom-trained .onnx file.
# A custom "Hey Aster" model can be trained offline — see ideas.md idea #7.
WAKE_WORD_MODEL = str(_cfg("runtime", "wake_word_model", default="hey_jarvis"))

# Detection threshold 0..1 — higher = fewer false accepts, more misses.
WAKE_WORD_THRESHOLD = float(_cfg("runtime", "wake_word_threshold", default=0.5))

# ============================================================================
# INTERVENTION MODE
# ============================================================================
# A background daemon polls the foreground window; once Mohamed has spent
# INTERVENTION_THRESHOLD seconds continuously on a distracting app, Aster breaks
# in (in persona) to offer to close it. Toggled via /intervention on|off or the
# toggle_intervention_mode tool — starts inactive. Not mutually exclusive with
# Sentry/Gesture (no webcam/GPU use).
INTERVENTION_CHECK_INTERVAL = int(_cfg("intervention", "check_interval", default=30))
INTERVENTION_THRESHOLD      = int(_cfg("intervention", "threshold", default=60))
INTERVENTION_COOLDOWN       = int(_cfg("intervention", "cooldown", default=900))
_qh = _cfg("intervention", "quiet_hours", default=None)
INTERVENTION_QUIET_HOURS    = tuple(_qh) if isinstance(_qh, (list, tuple)) and len(_qh) == 2 else None
_kw = _cfg("intervention", "distraction_keywords", default=None)
DISTRACTION_KEYWORDS = dict(_kw) if isinstance(_kw, dict) else {
    "youtube": "YouTube", "reddit": "Reddit", "twitter": "Twitter",
    "instagram": "Instagram", "tiktok": "TikTok", "twitch": "Twitch",
    "netflix": "Netflix", "facebook": "Facebook", "discord": "Discord", "Stremio": "Stremio - Freedom to Stream",
}

# ============================================================================
# AWARENESS MODE — ambient context daemon + initiative dial (Phase A)
# ============================================================================
# A background daemon polls the screen + webcam every AWARENESS_INTERVAL
# seconds, building a small "current_context" snapshot that is injected into
# the brain at the top of every turn. It also fires occasional proactive
# nudges (specific compliments, environmental observations, context-anchored
# questions) gated by INITIATIVE_LEVEL. See tools/awareness.py.
AWARENESS_INTERVAL          = int(_cfg("awareness", "interval", default=300))
AWARENESS_ACTIVE            = bool(_cfg("daemons", "awareness_mode", default=True))

# llama-server health watchdog — daemon pings localhost:8080/health every 30s;
# 3 consecutive failures → Telegram alert, and (when autorestart is on)
# relaunches start.bat in a new console, capped at 2 restarts per rolling hour.
# See tools/health_watchdog.py.
HEALTH_WATCHDOG_ENABLED     = bool(_cfg("daemons", "health_watchdog", default=True))
HEALTH_WATCHDOG_AUTORESTART = bool(_cfg("daemons", "health_watchdog_autorestart", default=True))
AWARENESS_ABSENCE_THRESHOLD = int(_cfg("awareness", "absence_threshold", default=15 * 60))
AWARENESS_ENV_COOLDOWN      = int(_cfg("awareness", "env_cooldown", default=2 * 3600))

# Initiative dial — controls how often Aster speaks unprompted.
# 0=silent, 1=low, 2=medium (default), 3=high.
_init_name = str(_cfg("settings", "initiative_level", default="medium")).strip().lower()
INITIATIVE_LEVEL = _INITIATIVE_NAME_TO_INT.get(_init_name, 2)
# Per-level capability matrix — code shape, not user-tunable; lives here.
INITIATIVE_LEVELS = {
    0: {"unsolicited_max_per_hour": 0, "compliment": False, "env_nudges": False, "context_questions": False, "mood_checkin": False, "email_checkin": False},
    1: {"unsolicited_max_per_hour": 1, "compliment": False, "env_nudges": False, "context_questions": True,  "mood_checkin": False, "email_checkin": False},
    2: {"unsolicited_max_per_hour": 3, "compliment": True,  "env_nudges": True,  "context_questions": True,  "mood_checkin": True,  "email_checkin": True},
    3: {"unsolicited_max_per_hour": 6, "compliment": True,  "env_nudges": True,  "context_questions": True,  "mood_checkin": True,  "email_checkin": True},
}
_iqh = _cfg("awareness", "initiative_quiet_hours", default=None)
INITIATIVE_QUIET_HOURS = tuple(_iqh) if isinstance(_iqh, (list, tuple)) and len(_iqh) == 2 else None

# No longer using OpenAI client — using native REST endpoint at localhost:8080/completion
# This variable is kept for backward compatibility but is no longer used.
llm = None


def load_engine():
    """Verifies the llama-server instance is reachable at localhost:8080."""
    try:
        import requests
        resp = requests.post(
            "http://localhost:8080/completion",
            json={"prompt": ".", "n_predict": 1, "temperature": 0.1},
            timeout=10,
        )
        if resp.status_code == 200:
            print("[Aster Engine] llama-server reachable at http://localhost:8080.")
        else:
            print(f"[Aster Engine] WARNING: Server returned status {resp.status_code}")
    except Exception as e:
        print(f"[Aster Engine] WARNING: Server unreachable — {e}")

# ============================================================================
# HYBRID MEMORY SYSTEM: ChromaDB + Markdown Vault
# ============================================================================
VAULT_DIR        = str(_cfg("paths", "vault_dir", default="Aster_Vault"))
MD_FILE          = str(_cfg("memory", "memory_file", default=os.path.join(VAULT_DIR, "memory.md")))
CHROMADB_PATH    = str(_cfg("memory", "chromadb_path", default=os.path.join(VAULT_DIR, "chroma_db")))
FACT_COLLECTION  = str(_cfg("memory", "fact_collection", default="aster_long_term_memory"))
RAG_VAULT_DIR    = str(_cfg("memory", "rag_vault_dir", default=os.path.join(VAULT_DIR, "database")))

# RRF hybrid recall — fuses BM25 (keyword) over memory.md with ChromaDB dense
# retrieval via Reciprocal Rank Fusion. Byte-identical return format to the
# dense-only path; only selection/ordering of recalled facts changes.
HYBRID_RECALL    = bool(_cfg("memory", "hybrid_recall", default=True))
RAG_STALE_DAYS   = int(_cfg("memory", "rag_stale_days", default=90))
COLD_STORAGE_DIR = str(_cfg("paths", "cold_storage_images", default=os.path.join(VAULT_DIR, "images")))
VOICES_DIR       = str(_cfg("paths", "voices_dir", default=os.path.join(VAULT_DIR, "Voices")))
# Known-faces directory (face recognition + sentry enrolment). Capital-V/F to
# match the on-disk Aster_Vault/Faces — the old hardcoded "Aster_vault/faces"
# literals only worked because Windows paths are case-insensitive.
FACES_DIR        = str(_cfg("paths", "faces_dir", default=os.path.join(VAULT_DIR, "Faces")))
DISCORD_CONTACTS_FILE = str(_cfg("paths", "discord_contacts", default=os.path.join(VAULT_DIR, "discord_contacts.json")))
SYSTEM_PROMPTS_DIR = str(_cfg("paths", "system_prompts_dir", default=os.path.join(VAULT_DIR, "System_Prompts")))
CUSTOM_VOICES_DIR  = str(_cfg("paths", "custom_voices_dir",  default=os.path.join(VAULT_DIR, "TTS-Voices")))
CONVERSATIONS_DIR  = str(_cfg("paths", "conversations_dir",  default=os.path.join(VAULT_DIR, "Conversations")))

# ============================================================================
# MOOD-TREND MEMORY (emotion roadmap — Idea 3)
# ============================================================================
# Logs each real user turn's (timestamp, mood, context) to a rolling JSONL log,
# maintains an in-memory "sustained mood" window (the shared foundation Ideas 1
# & 2 will consume), and periodically folds a rolled-up summary into long-term
# memory. The raw log is always emotion_log.jsonl; the daily summary goes to its
# own mood_trends.md + ChromaDB so memory.md stays reserved for personal facts.
# All knobs live under emotion.mood_trend in self_config.yaml.
MOOD_TREND_ENABLED         = bool(_cfg("emotion", "mood_trend", "enabled",                default=True))
MOOD_SUSTAIN_TURNS         = int(_cfg("emotion",  "mood_trend", "sustain_turns",          default=3))
MOOD_LOG_RETENTION_DAYS    = int(_cfg("emotion",  "mood_trend", "retention_days",         default=30))
MOOD_FLUSH_INTERVAL_HOURS  = float(_cfg("emotion","mood_trend", "flush_interval_hours",   default=24))
MOOD_FLUSH_MIN_TURNS       = int(_cfg("emotion",  "mood_trend", "flush_min_turns",        default=6))
MOOD_FLUSH_NONNEUTRAL_FRAC = float(_cfg("emotion","mood_trend", "flush_min_nonneutral_frac", default=0.4))
MOOD_LOG_PATH              = os.path.join(VAULT_DIR, "emotion_log.jsonl")

# Reliability-campaign instrumentation log (RELIABILITY_LOG_ENABLED above).
RELIABILITY_LOG_PATH       = os.path.join(VAULT_DIR, "reliability_log.jsonl")
MOOD_TRENDS_FILE           = os.path.join(VAULT_DIR, "mood_trends.md")
MOOD_FLUSH_STATE_FILE      = os.path.join(VAULT_DIR, "mood_flush_state.json")

# ── Mood-triggered behaviors (emotion roadmap — Ideas 1 & 2) ─────────────────
# Both default OFF (opt-in). Idea 2 (ambient-action offers) fires inline while
# chatting; Idea 1 (proactive check-ins) fires from the Awareness daemon once
# Mohamed has gone quiet. Both consume get_sustained_mood() / get_mood_streak()
# and coordinate via the shared streak-guard so they never double-nudge.
# Idea 2 — inline ambient-action OFFERS (never auto-executes):
# (Both systems share the single MOOD_SUSTAIN_TURNS debounce window above so the
# streak-guard stays coherent — there is intentionally no per-system turn knob.)
MOOD_ACTIONS_ENABLED       = bool(_cfg("emotion", "mood_actions", "enabled",        default=False))
MOOD_ACTIONS_COOLDOWN      = float(_cfg("emotion","mood_actions", "cooldown_seconds", default=1800))
# Idea 1 — proactive mood check-ins (via the Awareness daemon):
MOOD_CHECKIN_ENABLED       = bool(_cfg("emotion", "proactive_checkin", "enabled",         default=False))
MOOD_CHECKIN_QUIET_SECONDS = float(_cfg("emotion","proactive_checkin", "quiet_seconds",   default=90))
MOOD_CHECKIN_COOLDOWN      = float(_cfg("emotion","proactive_checkin", "cooldown_seconds", default=3600))
# Shared — minimum gap between ANY two mood-driven nudges (cross-system):
MOOD_TOUCH_COOLDOWN        = float(_cfg("emotion","mood_shared", "touch_cooldown_seconds", default=120))

# ============================================================================
# GOOGLE (GMAIL + CALENDAR) — tiered account access
# ============================================================================
# OAuth client credentials for the combined Gmail + Calendar integration (one
# consent grant covers both APIs' scopes since access_tier is a single shared
# setting). The actual OAuth flow, token caching/refresh, and per-tier scope
# selection live in tools/google_auth.py — this block only reads credentials
# and the tier/feature knobs. GOOGLE_AVAILABLE means "configured", not
# "currently authenticated" (matching SPOTIFY_AVAILABLE's meaning) — token
# validity is checked lazily by tools/google_auth.py at call time.
GOOGLE_CLIENT_ID     = _secret("google", "client_id", default="")
GOOGLE_CLIENT_SECRET = _secret("google", "client_secret", default="")
GOOGLE_AVAILABLE = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
GOOGLE_TOKEN_PATH = os.path.join(VAULT_DIR, "google_token.json")

# Access tier: limited (read-only) | partial (modify+send, ask permission
# first) | autonomous (modify+send immediately). Persisted like persona/voice
# via config_writer.set_config_values() — a deliberate standing choice about
# account access, not ephemeral daemon state.
GOOGLE_ACCESS_TIER = str(_cfg("integrations", "google", "access_tier", default="limited")).strip().lower()
if GOOGLE_ACCESS_TIER not in ("limited", "partial", "autonomous"):
    GOOGLE_ACCESS_TIER = "limited"

# Autonomous-tier safety valve: never auto-send to a sender with no prior
# correspondence on record, and cap how many autonomous sends happen per day.
AUTONOMOUS_SEND_DAILY_CAP = int(_cfg("integrations", "google", "autonomous_daily_cap", default=10))

# Proactively nudge the owner to re-authenticate before Google's ~7-day
# Testing-mode refresh token silently lapses (see tools/google_auth.py).
GOOGLE_REAUTH_REMINDER_ENABLED = bool(_cfg("integrations", "google", "reauth_reminder", default=True))

# Ambient "you have N unread emails" check-in (Awareness daemon) — mirrors
# the proactive_checkin (mood check-in) shape/knobs exactly.
EMAIL_CHECKIN_ENABLED   = bool(_cfg("integrations", "google", "email_checkin", "enabled",          default=False))
EMAIL_CHECKIN_THRESHOLD = int(_cfg("integrations",  "google", "email_checkin", "unread_threshold", default=3))
EMAIL_CHECKIN_COOLDOWN  = float(_cfg("integrations","google", "email_checkin", "cooldown_seconds", default=3600))

# ============================================================================
# ACTIVE PERSONA — which system prompt brain.py loads at startup.
# Value is a filename stem (no .md) inside SYSTEM_PROMPTS_DIR.
# Switch personas by changing this one knob (or persona.system_prompt in
# self_config.yaml). Shipped options: "jarvis" (butler), "gogi" (best friend).
# ============================================================================
SYSTEM_PROMPT = str(_cfg("persona", "system_prompt", default="jarvis"))

if not os.path.exists(VAULT_DIR):
    os.makedirs(VAULT_DIR)
os.makedirs(VOICES_DIR, exist_ok=True)
os.makedirs(SYSTEM_PROMPTS_DIR, exist_ok=True)
os.makedirs(CUSTOM_VOICES_DIR, exist_ok=True)
os.makedirs(CONVERSATIONS_DIR, exist_ok=True)

try:
    chroma_client = chromadb.PersistentClient(path=CHROMADB_PATH)
    memory_collection = chroma_client.get_or_create_collection(name=FACT_COLLECTION)
    MEMORY_AVAILABLE = True
except Exception as e:
    print(f"Memory DB Error: {e}")
    memory_collection = None
    MEMORY_AVAILABLE = False
