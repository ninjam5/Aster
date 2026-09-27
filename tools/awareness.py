"""Awareness Mode — ambient context daemon + initiative dial (Phase A).

Polls the screen + webcam every AWARENESS_INTERVAL seconds and maintains a
``current_context`` snapshot that is injected into the brain at the top of
every turn (via ``render_context_block()``). Also fires occasional proactive
nudges through the brain — specific compliments after an absence (#5),
environmental observations (#11), and context-anchored questions on return
from idle (#2 is consumed *by* the brain via the injected block).

Mood inference (#6) is keyword-only (no extra LLM call), mirroring
``tools/sentiment.py``.

The initiative dial (#15) gates how often Aster speaks unprompted; the
``set_initiative`` admin tool and ``/initiative`` Telegram command both
route through ``set_initiative()`` here.
"""

from __future__ import annotations

import re
import time
import threading
from datetime import datetime, timedelta
from collections import deque

import config

# ── module-level state ────────────────────────────────────────────────────────
AWARENESS_ACTIVE = bool(getattr(config, "AWARENESS_ACTIVE", True))
_lock = threading.Lock()

# Brain-busy semaphore — the daemon defers its Gemma call when the main brain
# is mid-turn (prevents racing on the single llama-server instance).
_brain_busy = threading.Event()

current_context: dict = {
    "screen": None,                 # one-line description of the foreground app/window
    "webcam": None,                 # one-line description of the person + posture, or "no one visible"
    "lighting": None,               # "dim" | "normal" | "bright" | None
    "presence": "unknown",          # "present" | "away" | "unknown"
    "absence_since": None,          # datetime — when presence first flipped to "away"
    "mood": "neutral",              # focused | tired | animated | terse | neutral
    "face_mood": "neutral",         # facial emotion (Tier 2): happy|sad|frustrated|anxious|neutral
    "last_capture": None,           # datetime of most recent successful poll
    "new_observations": [],         # list[str] of unsurfaced changes (consumed by brain)
}

_previous_snapshot: dict = {        # last fully-formed snapshot, for diffing
    "screen": None,
    "webcam": None,
    "lighting": None,
}
_pre_absence_webcam: str | None = None   # snapshot taken just before user left
_last_env_nudge: float = 0.0
_unsolicited_events = deque(maxlen=20)   # timestamps of unsolicited speech, for per-hour cap

# Session tracking (A0)
_session_start: datetime = datetime.now()
_late_night_remark_made: bool = False

# Recent user-message tracking for mood inference (A4)
_recent_user_msgs: deque = deque(maxlen=10)   # tuples of (timestamp, length, has_emoji)


# ── helpers ───────────────────────────────────────────────────────────────────
def _in_quiet_hours() -> bool:
    """Mirror of intervention._in_quiet_hours; uses INITIATIVE_QUIET_HOURS."""
    qh = config.INITIATIVE_QUIET_HOURS
    if not qh:
        return False
    start, end = qh
    hour = datetime.now().hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _initiative_settings() -> dict:
    """Return the per-level settings dict for the current INITIATIVE_LEVEL."""
    level = max(0, min(3, int(getattr(config, "INITIATIVE_LEVEL", 2))))
    return config.INITIATIVE_LEVELS.get(level, config.INITIATIVE_LEVELS[2])


def _budget_remaining() -> bool:
    """True if Aster has not yet hit the per-hour unsolicited-speech cap."""
    if _in_quiet_hours():
        return False
    settings = _initiative_settings()
    cap = settings.get("unsolicited_max_per_hour", 0)
    if cap <= 0:
        return False
    now = time.time()
    one_hour_ago = now - 3600
    recent = [t for t in _unsolicited_events if t >= one_hour_ago]
    return len(recent) < cap


def _record_unsolicited() -> None:
    _unsolicited_events.append(time.time())


def _format_session_duration() -> str:
    delta = datetime.now() - _session_start
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 1:
        return "just started"
    hours, mins = divmod(total_minutes, 60)
    if hours == 0:
        return f"{mins}m"
    return f"{hours}h {mins}m"


# ── low-token vision call ─────────────────────────────────────────────────────
_SCENE_PROMPT = (
    "Two short observations, one per line, no preamble:\n"
    "SCREEN: <one phrase: foreground app or window content>\n"
    "WEBCAM: <one phrase: person + posture, OR 'no one visible'. Also include lighting as 'dim', 'normal', or 'bright'>"
)


def _brief_scene_describe(screen_b64: str | None, webcam_b64: str | None) -> dict:
    """One Gemma call that captures screen + webcam at low token budget.

    Returns a dict with keys ``screen``, ``webcam``, ``lighting``. Missing
    images are tolerated (returns None for that field). Bypasses tools and
    never appends to ``messages``.
    """
    out = {"screen": None, "webcam": None, "lighting": None}
    if not screen_b64 and not webcam_b64:
        return out

    content: list[dict] = []
    if screen_b64:
        clean = screen_b64.split(",", 1)[-1].replace("\n", "").replace("\r", "").strip()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean}"}})
    if webcam_b64:
        clean = webcam_b64.split(",", 1)[-1].replace("\n", "").replace("\r", "").strip()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean}"}})
    content.append({"type": "text", "text": _SCENE_PROMPT})

    try:
        from core.brain import _execute_gemma_completion
        reply = _execute_gemma_completion(
            messages=[{"role": "user", "content": content}],
            temperature=0.2,
            n_predict=120,
        ).get("content") or ""
    except Exception as e:
        print(f"[Awareness] Scene-describe call failed: {e}")
        return out

    if not reply:
        return out

    for line in reply.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("SCREEN:"):
            out["screen"] = stripped.split(":", 1)[1].strip() or None
        elif upper.startswith("WEBCAM:"):
            webcam_text = stripped.split(":", 1)[1].strip()
            out["webcam"] = webcam_text or None
            low = webcam_text.lower()
            if "dim" in low or "dark" in low:
                out["lighting"] = "dim"
            elif "bright" in low or "well lit" in low or "well-lit" in low:
                out["lighting"] = "bright"
            elif "normal" in low or "moderate" in low:
                out["lighting"] = "normal"
    return out


# ── mood inference (A4) ───────────────────────────────────────────────────────
_TIRED_POSTURE = ("slumped", "leaning back", "tired", "yawn", "rubbing", "tired eyes")
_ANIMATED_KEYS = ("!", "?!", "lmao", "lol")


def record_user_message(text: str) -> None:
    """Called by the brain whenever a user message arrives — feeds the mood reader."""
    try:
        clean = (text or "").strip()
        has_emoji = bool(re.search(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", clean))
        _recent_user_msgs.append((time.time(), len(clean), has_emoji))
    except Exception:
        pass


def _infer_mood() -> str:
    now = time.time()
    hour = datetime.now().hour
    recent = [m for m in _recent_user_msgs if m[0] >= now - 300]

    webcam = (current_context.get("webcam") or "").lower()
    if any(k in webcam for k in _TIRED_POSTURE):
        return "tired"

    if hour >= 1 and hour < 5 and recent and all(m[1] < 40 for m in recent):
        return "tired"

    last_90s = [m for m in _recent_user_msgs if m[0] >= now - 90]
    if len(last_90s) >= 4:
        return "animated"

    last_5 = list(_recent_user_msgs)[-5:]
    if last_5 and all(not m[2] for m in last_5):
        avg_len = sum(m[1] for m in last_5) / len(last_5)
        if avg_len < 20:
            return "terse"

    if current_context.get("screen"):
        return "focused"
    return "neutral"


# ── context-block rendering (A0 + A3) ─────────────────────────────────────────
def _time_aware_block() -> str:
    now = datetime.now()
    return (
        "CURRENT CONTEXT:\n"
        f"- Local time: {now.strftime('%I:%M %p').lstrip('0')}\n"
        f"- Day: {now.strftime('%A, %B %d')}\n"
        f"- Session duration: {_format_session_duration()}\n"
        f"- Hour acknowledged: {'yes' if _late_night_remark_made else 'no'}"
    )


def _ambient_block() -> str | None:
    if not current_context.get("last_capture"):
        return None
    age = datetime.now() - current_context["last_capture"]
    minutes_ago = max(0, int(age.total_seconds() // 60))
    lines = [f"[Ambient Context, updated {minutes_ago} min ago]"]
    if current_context.get("screen"):
        lines.append(f"Screen: {current_context['screen']}")
    if current_context.get("webcam"):
        lines.append(f"Webcam: {current_context['webcam']}")
    if current_context.get("lighting"):
        lines.append(f"Lighting: {current_context['lighting']}")
    lines.append(f"Mood read: {current_context.get('mood', 'neutral')}")
    if getattr(config, "FACE_EMOTION_ENABLED", False):
        lines.append(f"Face read: {current_context.get('face_mood', 'neutral')}")
    if getattr(config, "AMBIENT_AUDIO_ENABLED", False):
        from tools.emotion_recognition import get_ambient_voice_mood
        lines.append(f"Voice tone: {get_ambient_voice_mood()}")
    if current_context.get("new_observations"):
        joined = "; ".join(current_context["new_observations"])
        lines.append(f"Unsurfaced: {joined}")
    return "\n".join(lines)


def render_context_block() -> str | None:
    """Return the ephemeral context block to inject for the upcoming turn.

    Combines the A0 time-aware block (always rendered) with the A3 ambient
    block (rendered when the daemon has produced at least one snapshot).
    Returns None only if awareness is fully disabled.
    """
    if not getattr(config, "AWARENESS_ACTIVE", True):
        return None
    # Refresh mood before rendering so the brain sees the latest read.
    with _lock:
        current_context["mood"] = _infer_mood()
    blocks = [_time_aware_block()]
    ambient = _ambient_block()
    if ambient:
        blocks.append(ambient)
    return "\n\n".join(blocks)


def mark_observations_surfaced() -> None:
    """Called by the brain after a turn that may have used the unsurfaced list."""
    with _lock:
        current_context["new_observations"] = []


def mark_late_night_remark_made() -> None:
    """Called when Aster's reply (between 12am–5am) acknowledges the hour."""
    global _late_night_remark_made
    _late_night_remark_made = True


def reset_session() -> None:
    """Reset session-scoped state — call from aster_shutdown_protocol."""
    global _session_start, _late_night_remark_made
    _session_start = datetime.now()
    _late_night_remark_made = False


# ── brain-busy semaphore (used by core/brain.py to defer the daemon's call) ───
def acquire_brain() -> None:
    _brain_busy.set()


def release_brain() -> None:
    _brain_busy.clear()


# ── proactive nudges (A5, A6) ─────────────────────────────────────────────────
def _push_nudge(nudge: str) -> None:
    """Route a [System Internal] cue through WebRTC if a call is live, else Telegram."""
    if not _budget_remaining():
        return
    _record_unsolicited()
    try:
        import webrtc_bridge
        if webrtc_bridge.call_is_active() and webrtc_bridge.speak_intervention(nudge):
            return
    except Exception as e:
        print(f"[Awareness] LiveKit delivery failed, falling back to Telegram: {e}")
    try:
        from core.brain import process_user_input
        response = process_user_input(nudge, None)
        _send_telegram(response)
    except Exception as e:
        print(f"[Awareness] Brain delivery failed: {e}")


def _send_telegram(text: str) -> None:
    clean = re.sub(r"\[NATIVE_AUDIO_PAYLOAD:.*?\]\s*", "", str(text or ""), flags=re.DOTALL).strip()
    if not clean or not config.bot:
        return
    try:
        config.bot.send_message(config.AUTHORIZED_CHAT_ID, clean)
    except Exception as e:
        print(f"[Awareness] Telegram text failed: {e}")
        return
    try:
        from tools.audio import generate_kokoro_voice
        payload = generate_kokoro_voice(clean)
        match = re.search(r"\[NATIVE_AUDIO_PAYLOAD:(.*?)\]", payload)
        if match:
            with open(match.group(1).strip(), "rb") as voice:
                config.bot.send_voice(config.AUTHORIZED_CHAT_ID, voice)
    except Exception as e:
        print(f"[Awareness] Telegram voice note failed: {e}")


def _detect_one_change(before: str, after: str) -> str | None:
    """Tiny Gemma call: name one concrete change between two webcam descriptions."""
    if not before or not after:
        return None
    prompt = (
        "Two webcam observations of the same person, taken minutes apart.\n"
        f"BEFORE: {before}\n"
        f"AFTER: {after}\n"
        "Return ONE short phrase naming a concrete change (clothing, hair, posture, "
        "the room), or exactly the word 'nothing' if nothing notable changed. "
        "No preamble, no quotes."
    )
    try:
        from core.brain import _execute_gemma_completion
        reply = _execute_gemma_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            n_predict=40,
        ).get("content") or ""
    except Exception:
        return None
    if not reply:
        return None
    first_line = next((ln.strip() for ln in reply.splitlines() if ln.strip()), "")
    low = first_line.lower()
    if not first_line or low.startswith("nothing") or "nothing notable" in low:
        return None
    return first_line.strip(".\"' ")


def _maybe_trigger_compliment(prev_presence: str) -> None:
    """A5: user just returned from a ≥ AWARENESS_ABSENCE_THRESHOLD absence."""
    if not _initiative_settings().get("compliment"):
        return
    if prev_presence != "away":
        return
    absence_since = current_context.get("absence_since")
    if not absence_since:
        return
    away_for = (datetime.now() - absence_since).total_seconds()
    if away_for < config.AWARENESS_ABSENCE_THRESHOLD:
        return
    change = _detect_one_change(_pre_absence_webcam or "", current_context.get("webcam") or "")
    if not change:
        return
    minutes = max(1, int(away_for // 60))
    nudge = (
        f"[System Internal: {config.OWNER_NAME} just returned to his desk after about {minutes} "
        f"minutes away. One concrete change: {change}. Break in, in your persona, "
        f"to remark on that specific change in a single sentence. Do not list other "
        f"observations.]"
    )
    print(f"[Awareness] Compliment trigger — change={change!r}, away={minutes}m")
    _push_nudge(nudge)


def _maybe_trigger_env_nudge(prev_lighting: str | None) -> None:
    """A6: lighting shifted to dim; rate-limited by AWARENESS_ENV_COOLDOWN."""
    global _last_env_nudge
    if not _initiative_settings().get("env_nudges"):
        return
    if current_context.get("lighting") != "dim":
        return
    if prev_lighting == "dim":
        return
    if time.time() - _last_env_nudge < config.AWARENESS_ENV_COOLDOWN:
        return
    _last_env_nudge = time.time()
    nudge = (
        "[System Internal: The room lighting has dimmed noticeably since the last "
        f"check. Break in, in your persona, to mention it and suggest {config.OWNER_NAME} "
        "raise his monitor brightness or turn on a lamp. One sentence.]"
    )
    print("[Awareness] Environment nudge — room darkened.")
    _push_nudge(nudge)


# ── proactive mood check-in (Idea 1) ──────────────────────────────────────────
_last_checkin_streak: int = -1

# ── Google integration ambient nudges ─────────────────────────────────────────
_last_email_checkin_count: int = 0    # highest unread count already notified about
_last_email_checkin_time: float = 0.0
_reauth_reminder_sent: bool = False   # latched until reauth_needed() clears (a successful re-auth)


def _seconds_since_last_user_msg() -> float | None:
    """Seconds since Mohamed's most recent message, or None if none recorded."""
    if not _recent_user_msgs:
        return None
    return time.time() - _recent_user_msgs[-1][0]


def _maybe_trigger_mood_checkin() -> None:
    """Idea 1: a heavy (sad/anxious) or upbeat (happy) mood has been *sustained*
    and Mohamed has now gone *quiet* — open a gentle, unsolicited check-in.

    Split-by-context: this fires only after the user has been quiet for
    ``MOOD_CHECKIN_QUIET_SECONDS`` (the inline-offer path, Idea 2, owns active
    chat). Coordinated with Idea 2 via the shared streak-guard + touch cooldown
    in ``tools.emotion_recognition`` so the two never double-nudge.
    """
    global _last_checkin_streak
    if not getattr(config, "MOOD_CHECKIN_ENABLED", False):
        return
    if not _initiative_settings().get("mood_checkin"):
        return

    try:
        from tools import emotion_recognition as er
    except Exception:
        return

    streak_id, mood = er.get_mood_streak()          # debounced by MOOD_SUSTAIN_TURNS
    if mood not in ("sad", "anxious", "happy"):
        return
    if streak_id == _last_checkin_streak:
        return                                       # already checked in this streak

    # Quiet gate — only break in once Mohamed has gone quiet post-streak.
    quiet_for = _seconds_since_last_user_msg()
    if quiet_for is None or quiet_for < config.MOOD_CHECKIN_QUIET_SECONDS:
        return
    # Don't stack on a just-fired Idea-2 offer.
    if er.mood_touch_recent(config.MOOD_TOUCH_COOLDOWN):
        return
    if not _budget_remaining():
        return

    if mood == "happy":
        nudge = (
            f"[System Internal: {config.OWNER_NAME} has been upbeat for a while and just went "
            "quiet. Break in briefly, in your persona, to share the good energy — "
            "one short, warm sentence.]"
        )
    else:
        nudge = (
            f"[System Internal: {config.OWNER_NAME} has seemed {mood} across several messages "
            f"and has now gone quiet. Break in, in your persona, with a gentle, "
            f"low-pressure check-in — offer to talk or just keep him company. One "
            f"or two sentences, and don't try to fix anything.]"
        )

    _last_checkin_streak = streak_id
    er.note_mood_touch()
    print(f"[Awareness] Mood check-in trigger — mood={mood}, quiet={int(quiet_for)}s")
    _push_nudge(nudge)


def _maybe_trigger_email_checkin() -> None:
    """Ambient 'you have N unread emails' nudge. Only re-fires when the
    unread count grows past what was already notified (not every tick) and
    respects its own cooldown on top of that, so a persistently full inbox
    doesn't nag every AWARENESS_INTERVAL."""
    global _last_email_checkin_count, _last_email_checkin_time
    if not getattr(config, "EMAIL_CHECKIN_ENABLED", False):
        return
    if not _initiative_settings().get("email_checkin"):
        return

    try:
        from tools import gmail_tool
        from tools import emotion_recognition as er
    except Exception:
        return

    count = gmail_tool.get_unread_primary_count()
    if count < config.EMAIL_CHECKIN_THRESHOLD:
        _last_email_checkin_count = 0  # reset so a future re-crossing can re-fire
        return
    if count <= _last_email_checkin_count:
        return
    if time.time() - _last_email_checkin_time < config.EMAIL_CHECKIN_COOLDOWN:
        return
    if er.mood_touch_recent(config.MOOD_TOUCH_COOLDOWN):
        return
    if not _budget_remaining():
        return

    nudge = (
        f"[System Internal: {config.OWNER_NAME} has {count} unread emails in their primary "
        f"inbox. Mention it briefly, in your persona, and offer to summarize them if he wants "
        f"— one short sentence, don't be pushy.]"
    )
    _last_email_checkin_count = count
    _last_email_checkin_time = time.time()
    er.note_mood_touch()
    print(f"[Awareness] Email check-in trigger — unread={count}")
    _push_nudge(nudge)


def _maybe_trigger_reauth_reminder() -> None:
    """Proactively tells the owner when Google's ~7-day Testing-mode refresh
    token has lapsed, instead of the Gmail/Calendar tools just silently
    failing next time they're used. Latches so it only nudges once per
    lapse — clears itself once tools.google_auth reports success again."""
    global _reauth_reminder_sent
    if not getattr(config, "GOOGLE_REAUTH_REMINDER_ENABLED", True):
        return
    if not config.GOOGLE_AVAILABLE:
        return

    try:
        import tools.google_auth as google_auth
    except Exception:
        return

    if not google_auth.reauth_needed():
        _reauth_reminder_sent = False
        return
    if _reauth_reminder_sent:
        return
    if not _budget_remaining():
        return

    nudge = (
        f"[System Internal: Google account access (Gmail/Calendar) has lapsed and needs "
        f"re-authorization — this is expected roughly every ~7 days for a personal-use app. "
        f"Let {config.OWNER_NAME} know briefly, in your persona, that he should reconnect it "
        f"from the dashboard when convenient.]"
    )
    _reauth_reminder_sent = True
    print("[Awareness] Google re-auth reminder trigger")
    _push_nudge(nudge)


# ── diff + state update ───────────────────────────────────────────────────────
def _short_diff(label: str, before: str | None, after: str | None) -> str | None:
    """Return a one-line observation if before/after meaningfully differ."""
    if not after:
        return None
    if not before:
        return None
    if before == after:
        return None
    # Reject trivial wording changes — require ≥ 3 non-shared lowercase words.
    bset = set(re.findall(r"[a-z]{3,}", before.lower()))
    aset = set(re.findall(r"[a-z]{3,}", after.lower()))
    new_words = aset - bset
    if len(new_words) < 3:
        return None
    return f"{label} changed: {after}"


def _update_state(scene: dict) -> tuple[str, str | None]:
    """Merge the new scene into current_context; return (prev_presence, prev_lighting)."""
    global _pre_absence_webcam
    with _lock:
        prev_presence = current_context.get("presence", "unknown")
        prev_lighting = current_context.get("lighting")
        prev_screen = current_context.get("screen")
        prev_webcam = current_context.get("webcam")

        # Update raw fields.
        if scene.get("screen"):
            current_context["screen"] = scene["screen"]
        if scene.get("webcam"):
            current_context["webcam"] = scene["webcam"]
        if scene.get("lighting"):
            current_context["lighting"] = scene["lighting"]
        current_context["last_capture"] = datetime.now()

        # Presence transitions.
        webcam_text = (scene.get("webcam") or "").lower()
        if webcam_text:
            if "no one" in webcam_text or "nobody" in webcam_text or "empty" in webcam_text:
                if prev_presence != "away":
                    _pre_absence_webcam = prev_webcam
                    current_context["absence_since"] = datetime.now()
                current_context["presence"] = "away"
            else:
                current_context["presence"] = "present"

        # Diff new observations (for context-anchored questions #2).
        observations: list[str] = list(current_context.get("new_observations", []))
        screen_obs = _short_diff("Screen", prev_screen, scene.get("screen"))
        if screen_obs:
            observations.append(screen_obs)
        # Cap at 3 to avoid context bloat.
        current_context["new_observations"] = observations[-3:]

    return prev_presence, prev_lighting


# ── the daemon ────────────────────────────────────────────────────────────────
def awareness_daemon() -> None:
    print("[Aster Core] Awareness Subsystem initialized.")
    print(f"[awareness] daemon started — interval={config.AWARENESS_INTERVAL}s, "
          f"initiative={getattr(config, 'INITIATIVE_LEVEL', 2)}")

    # First poll: brief initial delay so the engine warmup completes before
    # we hit it with an image-bearing call.
    time.sleep(10)

    while True:
        try:
            if not (AWARENESS_ACTIVE and getattr(config, "AWARENESS_ACTIVE", True)):
                time.sleep(2)
                continue

            # Defer if the brain is busy with a real user turn — we don't want
            # to race on the single llama-server instance.
            if _brain_busy.is_set():
                time.sleep(2)
                continue

            from tools.vision import capture_screen_base64, capture_frame_base64
            screen_b64 = capture_screen_base64()
            webcam_b64 = capture_frame_base64()

            scene = _brief_scene_describe(screen_b64, webcam_b64)
            if not (scene.get("screen") or scene.get("webcam")):
                # Capture failed entirely — back off and retry on next tick.
                time.sleep(config.AWARENESS_INTERVAL)
                continue

            prev_presence, prev_lighting = _update_state(scene)

            # Tier-2 facial emotion — reuse the frame we already captured (no
            # extra camera open). Hold/clear when Mohamed isn't in frame.
            if getattr(config, "FACE_EMOTION_ENABLED", False):
                try:
                    from tools import emotion_recognition as _er
                    if current_context.get("presence") == "away":
                        _er.reset_face_mood()
                        with _lock:
                            current_context["face_mood"] = "neutral"
                    else:
                        # Owner gate: re-capture with face recognition and only
                        # trust the read when Mohamed is the recognized face — a
                        # visiting friend's / intruder's expression must not
                        # poison the mood. On an owner-recognition miss we hold
                        # the last read (don't adopt anyone else's), matching the
                        # ambient-voice owner gate.
                        from tools.vision import capture_webcam_base64
                        face_b64, face_names = capture_webcam_base64()
                        if isinstance(face_b64, str) and config.OWNER_NAME in face_names:
                            face_mood = _er.detect_face_emotion(face_b64)
                            with _lock:
                                current_context["face_mood"] = face_mood
                except Exception as _fe:
                    print(f"[Awareness] Face emotion read failed: {_fe}")

            # Proactive triggers — gated by initiative + cooldowns.
            _maybe_trigger_env_nudge(prev_lighting)
            _maybe_trigger_compliment(prev_presence)
            _maybe_trigger_mood_checkin()
            _maybe_trigger_email_checkin()
            _maybe_trigger_reauth_reminder()
        except Exception as e:
            print(f"[Aster Awareness Error: {e}]")

        time.sleep(config.AWARENESS_INTERVAL)


# ── tool-facing actions (#15 — initiative dial) ───────────────────────────────
_INITIATIVE_PHRASES = {
    "silent": 0, "off": 0, "mute": 0, "no initiative": 0,
    "less initiative": -1, "lower initiative": -1, "tone it down": -1,
    "quiet": -1, "calm down": -1, "more initiative": +1, "louder": +1,
    "raise initiative": +1, "be more proactive": +1, "high initiative": 3,
    "max initiative": 3, "full initiative": 3,
}


def set_initiative(level) -> str:
    """Set the initiative dial (0-3) or interpret a natural-language phrase.

    Accepts: integer 0-3, string "0".."3", or a phrase like "less initiative".
    Returns a system note suitable for the brain's tool-result history.
    """
    current = max(0, min(3, int(getattr(config, "INITIATIVE_LEVEL", 2))))
    target: int | None = None

    if isinstance(level, bool):
        target = 2 if level else 0
    elif isinstance(level, int):
        target = level
    elif isinstance(level, str):
        raw = level.strip().lower()
        if raw.isdigit():
            target = int(raw)
        elif raw in _INITIATIVE_PHRASES:
            delta = _INITIATIVE_PHRASES[raw]
            target = delta if delta in (0, 3) else current + delta
        else:
            # Loose substring match for phrases.
            for phrase, delta in _INITIATIVE_PHRASES.items():
                if phrase in raw:
                    target = delta if delta in (0, 3) else current + delta
                    break

    if target is None:
        return (
            f"[System Note: Could not interpret initiative level '{level}'. "
            f"Current level is {current}.]"
        )

    target = max(0, min(3, target))
    config.INITIATIVE_LEVEL = target
    print(f"[Awareness] Initiative level set to {target}.")
    return f"[System Note: Initiative level set to {target} (was {current}).]"
