"""Tests for the Discord conversation-ending store (tools/conversations.py).

Policy only — the Laya hostility gate lives in core.brain and is mocked here.
Spec: conversation-ending.md.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import tools.conversations as conv


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(conv, "_PATH", str(tmp_path / "conversation_state.json"), raising=False)
    monkeypatch.setattr(config, "DISCORD_CONVERSATION_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "DISCORD_STRIKES_TO_MUTE", 3, raising=False)
    monkeypatch.setattr(config, "DISCORD_STRIKE_WINDOW_MINUTES", 720, raising=False)
    monkeypatch.setattr(config, "DISCORD_MUTE_MINUTES", 60, raising=False)
    monkeypatch.setattr(config, "DISCORD_HOSTILITY_MARGIN", 0.75, raising=False)
    yield


class TestStrikes:
    def test_three_strikes_then_mute(self):
        assert conv.record_strike("ninja", "a")["strikes"] == 1
        r2 = conv.record_strike("ninja", "b")
        assert r2["strikes"] == 2 and r2["should_warn"] and not r2["should_mute"]
        r3 = conv.record_strike("ninja", "c")
        assert r3["strikes"] == 3 and r3["should_mute"]

    def test_strikes_decay_after_the_window(self):
        conv.record_strike("ninja", "a")
        conv.record_strike("ninja", "b")
        data = conv._load()
        data["ninja"]["last_strike_ts"] = time.time() - (720 * 60) - 1
        conv._save(data)
        assert conv.record_strike("ninja", "c")["strikes"] == 1

    def test_mute_resets_the_strike_cycle(self):
        for msg in ("a", "b", "c"):
            conv.record_strike("ninja", msg)
        conv.register_mute("ninja")
        assert conv.get_state("ninja")["strikes"] == 0

    def test_empty_name_is_safe(self):
        assert conv.record_strike("", "x")["strikes"] == 0
        assert conv.get_state("")["strikes"] == 0


class TestMute:
    def test_check_mute_reports_minutes(self):
        conv.register_mute("ninja", minutes=60)
        muted, mins = conv.check_mute("ninja")
        assert muted and 1 <= mins <= 60

    def test_expired_mute_auto_clears(self):
        conv.register_mute("ninja", minutes=60)
        data = conv._load()
        data["ninja"]["muted_until"] = time.time() - 1
        conv._save(data)
        muted, mins = conv.check_mute("ninja")
        assert not muted and mins == 0
        assert conv.get_state("ninja")["muted_until"] == 0.0

    def test_mute_count_persists_across_unmute(self):
        conv.register_mute("ninja")
        conv.clear_mute("ninja")
        assert conv.get_state("ninja")["mute_count"] == 1
        conv.register_mute("ninja")
        assert conv.get_state("ninja")["mute_count"] == 2

    def test_case_insensitive(self):
        conv.register_mute("Ninja")
        assert conv.check_mute("ninja")[0]


class TestRecord:
    def test_incidents_describe_a_person(self):
        conv.record_strike("ninja", "fuck u aster", 0.84)
        conv.register_mute("ninja", message="fuck u aster")
        text = conv.incidents("ninja")
        assert "ninja" in text and "1 mute" in text
        assert "strike" in text and "mute" in text

    def test_incidents_unknown_and_all(self):
        assert "No Discord incidents" in conv.incidents("nobody")
        conv.record_strike("george", "x")
        assert "george" in conv.incidents()

    def test_corrupt_file_reads_empty_and_is_backed_up(self):
        with open(conv._PATH, "w", encoding="utf-8") as f:
            f.write("{not json")
        assert conv.get_state("ninja")["strikes"] == 0
        assert os.path.exists(conv._PATH + ".corrupt")

    def test_while_muted_messages_are_logged(self):
        conv.register_mute("ninja")
        conv.log_while_muted("ninja", "still mad")
        kinds = [i["kind"] for i in conv.get_state("ninja")["incidents"]]
        assert "while_muted" in kinds


class TestMessages:
    def test_countdown_minutes(self):
        assert "minutes" in conv.render_countdown(30)
        assert "30 minutes" in conv.render_countdown(30)

    def test_countdown_sub_minute(self):
        assert "less than a minute" in conv.render_countdown(0.2)

    def test_final_mentions_ordinal_only_after_the_first(self):
        assert "for the" not in conv.render_final(1)
        assert "second time" in conv.render_final(2)
        assert "third time" in conv.render_final(3)
