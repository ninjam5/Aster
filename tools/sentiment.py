"""
Sentiment classifier for the reactor face (ideas.md #4).

A cheap keyword match over Aster's final response text — no extra LLM call.
The result rides the existing /api/logs event stream as a "sentiment" event
and drives the Tauri reactor-face colour.

Five moods: calm (blue), working (gold), alert (red), music (white),
success (green). Priority when several match: alert > music > success > calm,
so an error mentioning a song still reads as red.
"""

import re

_ALERT = (
    "error", "failed", "fail", "unable", "cannot", "can't", "couldn't",
    "warning", "danger", "alert", "problem", "i'm sorry", "something went wrong",
)
_MUSIC = (
    "spotify", "playing", "now playing", "music", "song", "track",
    "playlist", "album",
)
_SUCCESS = (
    "done", "completed", "finished", "all set", "taken care of",
    "success", "set the timer", "set the alarm",
)


def classify_sentiment(text: str) -> str:
    """Return one of: calm, working, alert, music, success."""
    lowered = re.sub(r"\s+", " ", str(text or "").lower())
    if any(kw in lowered for kw in _ALERT):
        return "alert"
    if any(kw in lowered for kw in _MUSIC):
        return "music"
    if any(kw in lowered for kw in _SUCCESS):
        return "success"
    return "calm"
