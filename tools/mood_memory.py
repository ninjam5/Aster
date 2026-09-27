"""Mood-trend memory — emotional continuity (roadmap Idea 3).

Reads the per-turn mood log written by ``tools.emotion_recognition.log_mood_turn``
(``Aster_Vault/emotion_log.jsonl``), and on a time-gated cadence folds a rolled-up
summary into long-term memory so Aster notices *patterns* a real friend would:

    "you've been frustrated and wired every evening this week — what's going on?"

Storage decision (approved): summaries are written to a dedicated
``Aster_Vault/mood_trends.md`` + ChromaDB (id prefix ``moodtrend_``). ``memory.md``
is left reserved for personal facts so ``evaluate_and_memorize``'s dedup-blacklist
prompt never bloats with a new dated mood line every day. Semantic recall still
works because the summary lands in the shared ``aster_long_term_memory`` collection.

The flush is driven from ``core.brain``'s existing auto-consolidation cycle (every
``MEMORIZE_EVERY_N_TURNS`` real turns) — no new daemon. Everything here is
best-effort and never raises into the brain.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import config
from tools.emotion_recognition import read_mood_log, prune_mood_log

# Order moods are reported in when tied / for stable output.
_MOOD_ORDER = ("frustrated", "anxious", "sad", "happy", "neutral")


# ── Flush state (last summary timestamp) ─────────────────────────────────────
def _load_state() -> dict:
    path = config.MOOD_FLUSH_STATE_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        with open(config.MOOD_FLUSH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[Aster Mood] flush-state save failed: {e}")


# ── Summary construction (pure / testable) ───────────────────────────────────
def distribution(entries: list[dict]) -> dict[str, int]:
    """Count moods across log entries → {mood: count}."""
    counts: dict[str, int] = {}
    for rec in entries:
        mood = str(rec.get("mood", "neutral")).lower()
        counts[mood] = counts.get(mood, 0) + 1
    return counts


def nonneutral_fraction(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return (total - counts.get("neutral", 0)) / total


def _sorted_moods(counts: dict[str, int]) -> list[tuple[str, int]]:
    # Highest count first; ties broken by _MOOD_ORDER (non-neutral before neutral).
    def key(item):
        mood, n = item
        rank = _MOOD_ORDER.index(mood) if mood in _MOOD_ORDER else len(_MOOD_ORDER)
        return (-n, rank)
    return sorted(counts.items(), key=key)


def build_mood_summary(entries: list[dict], window_hours: float) -> str | None:
    """Format a human-readable mood-trend summary for `entries`.

    Returns None when there is nothing worth recording (no entries, or the period
    was mostly neutral per ``MOOD_FLUSH_NONNEUTRAL_FRAC``). The caller still owns
    the time / min-turns gating; this only formats and applies the neutrality gate.
    """
    total = len(entries)
    if total == 0:
        return None
    counts = distribution(entries)
    if nonneutral_fraction(counts) < config.MOOD_FLUSH_NONNEUTRAL_FRAC:
        return None

    ordered = _sorted_moods(counts)
    dominant = next((m for m, _ in ordered if m != "neutral"), None)
    if dominant is None:
        return None

    dist_str = ", ".join(
        f"{mood} {round(100 * n / total)}%" for mood, n in ordered if n > 0
    )

    # One representative context snippet for the dominant mood, for recall colour.
    example = ""
    for rec in reversed(entries):
        if str(rec.get("mood", "")).lower() == dominant:
            snippet = str(rec.get("context", "")).strip()
            if snippet:
                example = f' e.g. "{snippet[:120]}"'
                break

    day = datetime.now().strftime("%Y-%m-%d")
    hrs = round(window_hours)
    return (
        f"[Mood trend {day}] Over the past ~{hrs}h ({total} logged turns), "
        f"{config.OWNER_NAME}'s mood skewed mostly {dominant} ({dist_str}).{example}"
    )


# ── Persistence ──────────────────────────────────────────────────────────────
def _write_summary(summary: str) -> None:
    """Append the summary to mood_trends.md and add it to ChromaDB. Best-effort."""
    # 1. Human-readable markdown trend log (separate from memory.md).
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(config.MOOD_TRENDS_FILE, "a", encoding="utf-8") as f:
            f.write(f"- **[{ts}]** {summary}\n")
    except Exception as e:
        print(f"[Aster Mood] mood_trends.md write failed: {e}")

    # 2. Semantic store so recall_memory / awareness can surface trends.
    try:
        if getattr(config, "MEMORY_AVAILABLE", False) and config.memory_collection is not None:
            config.memory_collection.add(
                documents=[summary], ids=[f"moodtrend_{int(time.time())}"]
            )
    except Exception as e:
        print(f"[Aster Mood] mood-trend ChromaDB add failed: {e}")


# ── Orchestrator (called from the brain's auto-consolidation cycle) ──────────
def maybe_flush_mood_summary(now: datetime | None = None) -> str | None:
    """Prune the log, and if a flush is due, fold a mood-trend summary into memory.

    Gating, in order:
      1. Feature enabled.
      2. Time gate — at least ``MOOD_FLUSH_INTERVAL_HOURS`` since the last flush.
      3. Data gate — at least ``MOOD_FLUSH_MIN_TURNS`` logged turns in the window.
         (If not met we DON'T advance the clock, so we keep waiting for data.)
      4. Neutrality gate (inside build_mood_summary) — skip mostly-neutral periods,
         but DO advance the clock so the window stays bounded.

    Returns the summary string written, or None if nothing was written.
    """
    if not (config.EMOTION_ENABLED and config.MOOD_TREND_ENABLED):
        return None

    # Retention prune runs whenever we're invoked (cheap, every few turns).
    try:
        prune_mood_log()
    except Exception:
        pass

    now = now or datetime.now()
    state = _load_state()
    retention_hours = max(1.0, config.MOOD_LOG_RETENTION_DAYS * 24)

    last_flush = None
    raw = state.get("last_flush")
    if raw:
        try:
            last_flush = datetime.fromisoformat(raw)
        except Exception:
            last_flush = None

    if last_flush is not None:
        hours_since = (now - last_flush).total_seconds() / 3600.0
        if hours_since < config.MOOD_FLUSH_INTERVAL_HOURS:
            return None                      # not due yet
        window_hours = min(hours_since, retention_hours)
    else:
        # First ever flush — summarize everything still within retention.
        window_hours = retention_hours

    entries = read_mood_log(since_hours=window_hours)
    if len(entries) < config.MOOD_FLUSH_MIN_TURNS:
        return None                          # not enough data; keep waiting

    # Gates passed — mark this period processed regardless of what we write,
    # so the window can't grow unbounded over quiet/neutral stretches.
    state["last_flush"] = now.isoformat(timespec="seconds")
    _save_state(state)

    summary = build_mood_summary(entries, window_hours)
    if not summary:
        return None                          # mostly-neutral period — nothing to log

    _write_summary(summary)
    print(f"[Aster Mood] Folded mood-trend summary into memory: {summary}")
    return summary
