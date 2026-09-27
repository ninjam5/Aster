"""Tests for mood-trend memory (emotion roadmap — Idea 3).

No llama-server required: this exercises the per-turn mood log, the in-memory
sustained-mood window, and the periodic summary/flush logic in isolation.

We import ``tools.emotion_recognition`` with emotion *disabled* so its eager
Tier-0 text-model load is skipped, then re-enable the feature for the logic
under test (the functions here read ``config.*`` at call time, not import time).
"""

import json
from datetime import datetime, timedelta

import pytest

import config

# Skip the eager text-model load at import, then turn the feature back on.
config.EMOTION_ENABLED = False
config.MOOD_TREND_ENABLED = True
import tools.emotion_recognition as er          # noqa: E402
import tools.mood_memory as mm                  # noqa: E402
config.EMOTION_ENABLED = True


def _write_entries(path, specs):
    """specs: list of (mood, context, datetime) → JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for mood, context, ts in specs:
            f.write(json.dumps(
                {"ts": ts.isoformat(timespec="seconds"), "mood": mood, "context": context}
            ) + "\n")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EMOTION_ENABLED", True)
    monkeypatch.setattr(config, "MOOD_TREND_ENABLED", True)
    monkeypatch.setattr(config, "MOOD_LOG_PATH", str(tmp_path / "emotion_log.jsonl"))
    monkeypatch.setattr(config, "MOOD_TRENDS_FILE", str(tmp_path / "mood_trends.md"))
    monkeypatch.setattr(config, "MOOD_FLUSH_STATE_FILE", str(tmp_path / "state.json"))
    # Default tuning knobs (mirror config defaults; some tests override).
    monkeypatch.setattr(config, "MOOD_SUSTAIN_TURNS", 3)
    monkeypatch.setattr(config, "MOOD_LOG_RETENTION_DAYS", 30)
    monkeypatch.setattr(config, "MOOD_FLUSH_INTERVAL_HOURS", 24)
    monkeypatch.setattr(config, "MOOD_FLUSH_MIN_TURNS", 6)
    monkeypatch.setattr(config, "MOOD_FLUSH_NONNEUTRAL_FRAC", 0.4)
    # No real ChromaDB writes from the flush.
    monkeypatch.setattr(config, "MEMORY_AVAILABLE", False, raising=False)
    er._mood_window.clear()
    return tmp_path


# ── log_mood_turn / read_mood_log ────────────────────────────────────────────
def test_log_and_read(store):
    er.log_mood_turn("frustrated", "the   build\nkeeps failing")
    er.log_mood_turn("neutral", "ok")
    entries = er.read_mood_log()
    assert [e["mood"] for e in entries] == ["frustrated", "neutral"]
    # Whitespace flattened.
    assert entries[0]["context"] == "the build keeps failing"


def test_log_coerces_invalid_mood(store):
    er.log_mood_turn("furious", "x")
    entries = er.read_mood_log()
    assert entries[0]["mood"] == "neutral"


def test_log_disabled_is_noop(store, monkeypatch):
    monkeypatch.setattr(config, "MOOD_TREND_ENABLED", False)
    er.log_mood_turn("sad", "down")
    assert er.read_mood_log() == []


def test_read_since_hours_and_skips_malformed(store):
    now = datetime.now()
    path = config.MOOD_LOG_PATH
    _write_entries(path, [
        ("sad", "old", now - timedelta(hours=50)),
        ("happy", "recent", now - timedelta(minutes=5)),
    ])
    # Append a garbage line that must be skipped, not crash.
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    recent = er.read_mood_log(since_hours=1)
    assert [e["mood"] for e in recent] == ["happy"]
    assert len(er.read_mood_log()) == 2  # both valid lines, garbage skipped


def test_prune_drops_old_entries(store):
    now = datetime.now()
    _write_entries(config.MOOD_LOG_PATH, [
        ("sad", "ancient", now - timedelta(days=40)),
        ("happy", "fresh", now - timedelta(hours=1)),
    ])
    kept = er.prune_mood_log(retention_days=30)
    assert kept == 1
    assert [e["mood"] for e in er.read_mood_log()] == ["happy"]


# ── get_sustained_mood (the debounce gate Ideas 1 & 2 consume) ───────────────
def test_sustained_all_same_nonneutral(store):
    for _ in range(3):
        er.record_mood("happy")
    assert er.get_sustained_mood(3) == "happy"


def test_sustained_broken_by_recent_blip(store):
    for _ in range(3):
        er.record_mood("happy")
    er.record_mood("neutral")          # one off-label turn breaks the streak
    assert er.get_sustained_mood(3) is None


def test_sustained_neutral_never_counts(store):
    for _ in range(5):
        er.record_mood("neutral")
    assert er.get_sustained_mood(3) is None


def test_sustained_requires_enough_turns(store):
    er.record_mood("sad")
    er.record_mood("sad")
    assert er.get_sustained_mood(3) is None


# ── summary construction ─────────────────────────────────────────────────────
def test_distribution_and_fraction():
    entries = [{"mood": "frustrated"}, {"mood": "frustrated"}, {"mood": "neutral"}]
    counts = mm.distribution(entries)
    assert counts == {"frustrated": 2, "neutral": 1}
    assert mm.nonneutral_fraction(counts) == pytest.approx(2 / 3)


def test_build_summary_neutral_period_returns_none(store):
    now = datetime.now()
    entries = [{"mood": "neutral", "context": "", "ts": now.isoformat()} for _ in range(8)]
    entries.append({"mood": "happy", "context": "yay", "ts": now.isoformat()})
    # 1/9 non-neutral < 0.4 → no summary.
    assert mm.build_mood_summary(entries, window_hours=24) is None


def test_build_summary_nonneutral_has_distribution_and_example(store):
    now = datetime.now()
    entries = [{"mood": "frustrated", "context": f"thing {i}", "ts": now.isoformat()}
               for i in range(6)]
    entries += [{"mood": "neutral", "context": "", "ts": now.isoformat()} for _ in range(2)]
    summary = mm.build_mood_summary(entries, window_hours=24)
    assert summary is not None
    assert "frustrated" in summary
    assert "%" in summary
    assert "8 logged turns" in summary
    assert "thing 5" in summary           # most-recent frustrated context as example


# ── maybe_flush_mood_summary (gating + persistence) ──────────────────────────
def test_flush_not_enough_turns(store):
    now = datetime.now()
    _write_entries(config.MOOD_LOG_PATH,
                   [("frustrated", "x", now)] * 3)   # < MOOD_FLUSH_MIN_TURNS
    assert mm.maybe_flush_mood_summary() is None
    import os
    assert not os.path.exists(config.MOOD_FLUSH_STATE_FILE)   # clock not advanced


def test_flush_writes_summary_then_time_gates(store):
    import os
    now = datetime.now()
    _write_entries(config.MOOD_LOG_PATH, [("frustrated", "build broke", now)] * 8)

    summary = mm.maybe_flush_mood_summary()
    assert summary is not None and "frustrated" in summary
    assert os.path.exists(config.MOOD_TRENDS_FILE)
    with open(config.MOOD_TRENDS_FILE, encoding="utf-8") as f:
        assert "frustrated" in f.read()
    assert os.path.exists(config.MOOD_FLUSH_STATE_FILE)

    # Immediate second call is inside the interval → no second write.
    assert mm.maybe_flush_mood_summary() is None


def test_flush_neutral_period_advances_clock_without_writing(store):
    import os
    now = datetime.now()
    _write_entries(config.MOOD_LOG_PATH, [("neutral", "", now)] * 8)
    assert mm.maybe_flush_mood_summary() is None     # boring period → no summary
    assert os.path.exists(config.MOOD_FLUSH_STATE_FILE)        # but clock advanced
    assert not os.path.exists(config.MOOD_TRENDS_FILE)         # nothing written
