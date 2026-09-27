"""Intervention Mode — proactive focus daemon (ideas.md #3).

Polls the foreground window; once Mohamed has spent INTERVENTION_THRESHOLD
seconds continuously on a distracting app, Aster breaks in (in persona, via the
brain) to offer to close it. Coexists with Sentry/Gesture — no webcam/GPU use.
"""

import re
import time
import threading
from datetime import datetime

import pygetwindow

import config

# ── module-level state (mirrors tools/sentry.py) ──────────────────────────────
INTERVENTION_ACTIVE = False

_GRACE_POLLS = 1                 # tolerated glance-away polls before the timer resets
_lock = threading.Lock()
_focus_category: str | None = None
_focus_seconds: float = 0.0
_grace_left: int = 0
_flagged_window = None           # pygetwindow window captured at trigger time
_snooze_until: float = 0.0
_last_intervention: float = 0.0


def classify_window(title: str) -> str | None:
    """Return the friendly distraction name for a window title, or None.

    Pure — no side effects. Matches config.DISTRACTION_KEYWORDS substrings
    case-insensitively.
    """
    lowered = str(title or "").lower()
    for keyword, friendly in config.DISTRACTION_KEYWORDS.items():
        if keyword in lowered:
            return friendly
    return None


def _get_foreground():
    """Return (window, title) for the active window; (None, "") on any failure."""
    try:
        win = pygetwindow.getActiveWindow()
        return (win, getattr(win, "title", "") or "") if win else (None, "")
    except Exception:
        return (None, "")


def _in_quiet_hours() -> bool:
    qh = config.INTERVENTION_QUIET_HOURS
    if not qh:
        return False
    start, end = qh
    hour = datetime.now().hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _reset_accumulator() -> None:
    global _focus_category, _focus_seconds, _grace_left
    _focus_category = None
    _focus_seconds = 0.0
    _grace_left = 0


def _send_telegram_intervention(text: str) -> None:
    """Fallback delivery when no LiveKit call is active — text + Kokoro voice note."""
    clean = re.sub(r"\[NATIVE_AUDIO_PAYLOAD:.*?\]\s*", "", str(text or ""), flags=re.DOTALL).strip()
    if not clean or not config.bot:
        return
    try:
        config.bot.send_message(config.AUTHORIZED_CHAT_ID, clean)
    except Exception as e:
        print(f"[Intervention] Telegram text failed: {e}")
        return
    try:
        from tools.audio import generate_kokoro_voice
        payload = generate_kokoro_voice(clean)
        match = re.search(r"\[NATIVE_AUDIO_PAYLOAD:(.*?)\]", payload)
        if match:
            with open(match.group(1).strip(), "rb") as voice:
                config.bot.send_voice(config.AUTHORIZED_CHAT_ID, voice)
    except Exception as e:
        print(f"[Intervention] Telegram voice note failed: {e}")


def _trigger_intervention(category: str, window) -> None:
    """Flag the window and route an in-persona nudge through the brain."""
    global _flagged_window, _last_intervention
    minutes = max(1, round(_focus_seconds / 60))
    _flagged_window = window
    _last_intervention = time.time()
    _reset_accumulator()

    nudge = (
        f"[System Internal: {config.OWNER_NAME} has spent roughly {minutes} minutes "
        f"continuously on {category}. Break in, in your persona, to point this "
        f"out and offer to close that window. Keep it to one or two sentences.]"
    )
    print(f"[Intervention] Triggering — {minutes} min on {category}.")

    try:
        import webrtc_bridge
        if webrtc_bridge.call_is_active() and webrtc_bridge.speak_intervention(nudge):
            return
    except Exception as e:
        print(f"[Intervention] LiveKit delivery failed, falling back to Telegram: {e}")

    try:
        from core.brain import process_user_input
        response = process_user_input(nudge, None)
        _send_telegram_intervention(response)
    except Exception as e:
        print(f"[Intervention] Brain delivery failed: {e}")


def intervention_daemon() -> None:
    """Background thread — gated by INTERVENTION_ACTIVE; tracks focus time."""
    global _focus_category, _focus_seconds, _grace_left
    print("[Aster Core] Intervention Subsystem initialized.")
    while True:
        if not INTERVENTION_ACTIVE:
            time.sleep(2)
            continue

        try:
            window, title = _get_foreground()
            category = classify_window(title)
            interval = config.INTERVENTION_CHECK_INTERVAL

            with _lock:
                if category and category == _focus_category:
                    _focus_seconds += interval
                    _grace_left = _GRACE_POLLS
                elif category:
                    _focus_category = category
                    _focus_seconds = float(interval)
                    _grace_left = _GRACE_POLLS
                elif _focus_category is not None:
                    # Not on a distraction — tolerate one glance-away poll, then reset.
                    if _grace_left > 0:
                        _grace_left -= 1
                    else:
                        _reset_accumulator()

                ready = (
                    _focus_category is not None
                    and _focus_seconds >= config.INTERVENTION_THRESHOLD
                    and not _in_quiet_hours()
                    and time.time() >= _snooze_until
                    and time.time() - _last_intervention >= config.INTERVENTION_COOLDOWN
                )
                trigger_category = _focus_category
                trigger_window = window

            if ready:
                _trigger_intervention(trigger_category, trigger_window)
        except Exception as e:
            print(f"[Aster Intervention Error: {e}]")

        time.sleep(config.INTERVENTION_CHECK_INTERVAL)


# ── tool-facing actions ───────────────────────────────────────────────────────
def toggle_intervention(state: bool) -> str:
    global INTERVENTION_ACTIVE
    INTERVENTION_ACTIVE = bool(state)
    if INTERVENTION_ACTIVE:
        with _lock:
            _reset_accumulator()
    status = "ON" if INTERVENTION_ACTIVE else "OFF"
    print(f"[Aster Internal: Intervention Mode toggled {status}]")
    return f"[System Note: Intervention Mode is now {status}.]"


def snooze(minutes: int) -> str:
    global _snooze_until
    try:
        mins = max(1, int(minutes))
    except (TypeError, ValueError):
        mins = 10
    _snooze_until = time.time() + mins * 60
    with _lock:
        _reset_accumulator()
    return f"[System Note: Intervention Mode snoozed for {mins} minutes.]"


def close_flagged_window() -> str:
    global _flagged_window
    window = _flagged_window
    _flagged_window = None
    if window is None:
        return "[System Note: No distracting window is currently flagged.]"
    try:
        title = getattr(window, "title", "the window")
        window.close()
        return f"[System Note: Closed the distracting window ({title}).]"
    except Exception as e:
        return f"[System Error: Could not close the flagged window — {e}]"
