"""Ambient-action offers — mood → suggestion policy (roadmap Idea 2).

When a mood is *sustained* during active chatting, Aster *offers* (never
auto-executes) a mood-appropriate action built entirely from tools that already
exist. The persona only actually runs a tool if Mohamed agrees in the reply —
this module just injects a short ``[System Internal: …offer…]`` instruction into
the current turn so the offer rides along with Aster's same-turn response.

Policy (always an offer, never an action — "want me to put something on?"):

    sad / anxious  → calming playlist (play_spotify_playlist / play_liked_songs)
                     + lower the volume ~20% (set_volume)
    frustrated     → close the active distraction window (close_distraction_window)
    happy          → hype playlist (play_spotify_playlist)

Coordination: consumes ``get_mood_streak()`` so it fires at most once per
sustained-mood streak, respects ``MOOD_ACTIONS_COOLDOWN``, and registers a
``note_mood_touch()`` so the proactive check-in (Idea 1) won't stack on top.
Everything here is best-effort and never raises into the brain.
"""

from __future__ import annotations

import time

import config
from tools.emotion_recognition import get_mood_streak, note_mood_touch

# Mood → the offer instruction the persona should voice this turn.
_MOOD_POLICY: dict[str, str] = {
    "sad": (
        f"{config.OWNER_NAME} has seemed down for a few turns. If it feels natural, gently OFFER "
        "(do not just do it) to put on a calming playlist and lower the volume a bit. "
        "Ask first, keep it to one warm sentence."
    ),
    "anxious": (
        f"{config.OWNER_NAME} has seemed anxious for a few turns. If it feels natural, gently OFFER "
        "(do not just do it) to put on something calming and lower the volume a bit. "
        "Ask first, keep it to one steadying sentence."
    ),
    "frustrated": (
        f"{config.OWNER_NAME} has seemed frustrated for a few turns. If it feels natural, OFFER "
        "(do not just do it) to close whatever distracting window is open, or to help "
        "him step back for a sec. Ask first, one sentence."
    ),
    "happy": (
        f"{config.OWNER_NAME} has been upbeat for a few turns. Match the energy and OFFER (do not "
        "just do it) a hype playlist. One short, upbeat sentence."
    ),
}

# Streak/cooldown state.
_last_action_streak: int = -1
_last_action_time: float = 0.0


def maybe_mood_action_nudge() -> str | None:
    """Return a ``[System Internal: …]`` offer nudge if a sustained mood warrants
    one this turn, else None. Records the streak + touch so it won't repeat.

    Gating, in order: feature enabled → mood sustained & in policy → not already
    offered on this streak → off cooldown.
    """
    global _last_action_streak, _last_action_time
    if not config.MOOD_ACTIONS_ENABLED:
        return None

    streak_id, mood = get_mood_streak()      # debounced by MOOD_SUSTAIN_TURNS
    if mood is None or mood not in _MOOD_POLICY:
        return None
    if streak_id == _last_action_streak:
        return None                                   # already offered this streak
    if (time.monotonic() - _last_action_time) < config.MOOD_ACTIONS_COOLDOWN:
        return None

    _last_action_streak = streak_id
    _last_action_time = time.monotonic()
    note_mood_touch()
    return f"[System Internal: {_MOOD_POLICY[mood]}]"
