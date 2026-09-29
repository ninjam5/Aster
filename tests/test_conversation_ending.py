"""Tests for the Discord conversation-ending ladder (spec: conversation-ending.md §4).

`process_discord_chat` is driven end-to-end with the Laya gate and the LLM mocked, so
the strike/mute/countdown policy is exercised for real. No model, no network.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.system1 as system1
import tools.conversations as conv
import tools.people as people


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(conv, "_PATH", str(tmp_path / "conversation_state.json"), raising=False)
    monkeypatch.setattr(people, "_PATH", str(tmp_path / "people.json"), raising=False)
    monkeypatch.setattr(config, "DISCORD_CONVERSATION_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "DISCORD_STRIKES_TO_MUTE", 3, raising=False)
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(brain, "publish_terminal", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(brain, "get_user_facts", lambda s: "", raising=False)
    monkeypatch.setattr(
        people, "resolve_identity",
        lambda name, facts="": {"pronoun": "unknown", "honorific": "Sir",
                                "needs_ask": False, "source": "test"},
        raising=False)
    monkeypatch.setattr(
        brain, "_execute_llm_completion",
        lambda **k: {"role": "assistant", "content": "Very good, Sir.", "tool_calls": None},
        raising=False)
    brain.discord_chat_histories.clear()
    yield
    brain.discord_chat_histories.clear()


def _hostility(monkeypatch, strike=True, margin=0.9):
    monkeypatch.setattr(
        brain, "_discord_hostility",
        lambda text: {"strike": strike, "margin": margin, "reason": "", "escalate": False},
        raising=False)


class TestLadder:
    def test_first_strike_answers_normally(self, monkeypatch):
        _hostility(monkeypatch)
        out = brain.process_discord_chat("ninja", "fuck u aster")
        assert out == "Very good, Sir."          # a normal (cool) reply
        assert conv.get_state("ninja")["strikes"] == 1

    def test_second_strike_warns(self, monkeypatch):
        _hostility(monkeypatch)
        brain.process_discord_chat("ninja", "fuck u")
        out = brain.process_discord_chat("ninja", "u little shit")
        assert out == conv.WARNING_LINE
        assert conv.get_state("ninja")["strikes"] == 2

    def test_third_strike_mutes_and_says_so(self, monkeypatch):
        _hostility(monkeypatch)
        brain.process_discord_chat("ninja", "a")
        brain.process_discord_chat("ninja", "b")
        out = brain.process_discord_chat("ninja", "c")
        assert "ending this conversation" in out
        assert conv.check_mute("ninja")[0] is True
        assert conv.get_state("ninja")["mute_count"] == 1

    def test_muted_inbound_is_a_countdown_with_no_llm(self, monkeypatch):
        _hostility(monkeypatch)
        calls = []
        monkeypatch.setattr(
            brain, "_execute_llm_completion",
            lambda **k: calls.append(1) or {"role": "assistant", "content": "x"},
            raising=False)
        for msg in ("a", "b", "c"):
            brain.process_discord_chat("ninja", msg)   # -> muted
        calls.clear()
        out = brain.process_discord_chat("ninja", "still mad")
        assert "not accepting responses" in out
        assert calls == [], "the countdown must not call the LLM"
        assert any(i["kind"] == "while_muted"
                   for i in conv.get_state("ninja")["incidents"])

    def test_second_cycle_mentions_second_time(self, monkeypatch):
        _hostility(monkeypatch)
        for i in range(3):
            brain.process_discord_chat("ninja", f"x{i}")   # mute #1
        assert conv.get_state("ninja")["mute_count"] == 1
        conv.clear_mute("ninja")                            # owner unmutes
        brain.process_discord_chat("ninja", "y1")           # strike 1
        brain.process_discord_chat("ninja", "y2")           # strike 2 -> warn
        out = brain.process_discord_chat("ninja", "y3")     # strike 3 -> mute #2
        assert "second time" in out
        assert conv.get_state("ninja")["mute_count"] == 2

    def test_not_hostile_answers_normally(self, monkeypatch):
        _hostility(monkeypatch, strike=False)
        out = brain.process_discord_chat("ninja", "hey aster")
        assert out == "Very good, Sir."
        assert conv.get_state("ninja")["strikes"] == 0

    def test_disabled_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_CONVERSATION_ENABLED", False, raising=False)
        _hostility(monkeypatch)
        out = brain.process_discord_chat("ninja", "fuck u aster")
        assert out == "Very good, Sir."
        assert conv.get_state("ninja")["strikes"] == 0


class TestHostilityGate:
    def test_confident_yes_strikes(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": True, "escalate": False, "margin": 0.9})
        assert brain._discord_hostility("fuck u aster")["strike"] is True

    def test_confident_no_does_not_strike(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": False, "escalate": False, "margin": 0.9})
        assert brain._discord_hostility("hey aster")["strike"] is False

    def test_escalate_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": True, "escalate": True, "margin": 0.2})
        assert brain._discord_hostility("meh")["strike"] is False

    def test_kernel_off_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert brain._discord_hostility("fuck u aster")["strike"] is False
