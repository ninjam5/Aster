"""Tests for the round-7 fixes and the coverage gaps round 7 identified.

  * the ID-19 gate runs OFF the event loop and restores the transcript on barge-in
  * the mood tag no longer defeats the relay parser's `^` anchor (F3)
  * the consolidation digest window + the 8-attempt cap
  * the strict per-call margins the safety gates pass
  * the ID 8 hint is not computed for a relay turn
  * memory writes are serialized under the write lock
"""
import asyncio
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.memory as memory
import core.system1 as system1
import webrtc_bridge


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


# ── ID 19: off-loop + barge-in restore ────────────────────────────────────────

class TestLiveKitGateWiring:
    def test_the_gate_runs_off_the_event_loop(self, monkeypatch):
        """QA round 7: the docs claimed this was pinned; it was not. Laya is a
        synchronous CPU pass, so it must go through asyncio.to_thread."""
        import io as _io
        src = _io.open(webrtc_bridge.__file__, encoding="utf-8", errors="replace").read()
        assert "asyncio.to_thread(_addressed_to_aster" in src
        assert "insert(1" not in src

    def test_a_barge_in_restores_the_pending_transcript(self, monkeypatch):
        bridge = webrtc_bridge.ManualWebRTCBridge.__new__(webrtc_bridge.ManualWebRTCBridge)
        bridge._pending_transcript = "hello aster"
        bridge._debounce_task = None

        async def _cancel(*a, **k):
            raise asyncio.CancelledError()

        monkeypatch.setattr(webrtc_bridge, "_addressed_to_aster", lambda t: True)
        monkeypatch.setattr(webrtc_bridge.asyncio, "to_thread", _cancel, raising=False)
        monkeypatch.setattr(webrtc_bridge._config, "UTTERANCE_DEBOUNCE", 0, raising=False)

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bridge._flush_pending_transcript())
        assert bridge._pending_transcript == "hello aster"   # kept for the retry


# ── F3: the mood tag must not defeat the relay parser ─────────────────────────

class TestMoodTagStripping:
    def test_a_mood_tagged_relay_is_still_exact(self, monkeypatch):
        import tools.discord_api as d
        monkeypatch.setattr(d, "CONTACTS", {"george": "1"}, raising=False)
        d._CONTACT_LOOKUP = None
        out = brain._extract_discord_message_intent("[Mood: neutral] tell george I'll be late")
        assert out is not None, "the mood tag defeated the relay parser"
        assert out["target"] == "george" and out["exact"] is True
        assert "[Mood" not in out["payload"]

    def test_a_speaker_tag_is_stripped_too(self, monkeypatch):
        import tools.discord_api as d
        monkeypatch.setattr(d, "CONTACTS", {"george": "1"}, raising=False)
        out = brain._extract_discord_message_intent("[Speaker: farah] tell george hello")
        assert out is not None and out["target"] == "george"


# ── consolidation digest window + attempt cap ─────────────────────────────────

class TestConsolidationBounds:
    def test_the_digest_window_is_8_turns_of_1200_chars(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(config, "MEMORY_AVAILABLE", True, raising=False)
        monkeypatch.setattr(brain, "_session_may_hold_new_facts",
                            lambda turns: seen.update(turns=turns) or (True, "t"))
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: {"role": "assistant", "content": "NO_UPDATE"})
        brain.messages[:] = [brain.messages[0]] + [
            {"role": "user", "content": "x" * 5000} for _ in range(12)]
        brain.evaluate_and_memorize("TEST")
        assert len(seen["turns"]) == 8
        assert all(len(t) <= 1200 for t in seen["turns"])
        brain.messages[:] = [brain.messages[0]]

    def test_the_attempt_cap_bounds_the_gate(self, monkeypatch):
        calls = []
        monkeypatch.setattr(config, "MEMORY_AVAILABLE", True, raising=False)
        monkeypatch.setattr(brain, "_session_may_hold_new_facts", lambda t: (True, "t"))
        tool_calls = [{"id": str(i), "function": {"name": "memorize_fact",
                                                  "arguments": '{"fact": "f%d"}' % i}}
                      for i in range(20)]
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: {"role": "assistant", "content": "",
                                         "tool_calls": tool_calls})
        monkeypatch.setattr(brain, "execute_tool",
                            lambda name, args: calls.append(args["fact"]) or "Successfully committed")
        brain.evaluate_and_memorize("TEST")
        assert len(calls) == 8   # capped


# ── strict per-call margins ───────────────────────────────────────────────────

class TestStrictThresholds:
    def test_the_safety_gates_pass_a_strict_margin(self, monkeypatch):
        # QA round 8: assert each gate INDIVIDUALLY - the old all(None-or->=0.5) form let
        # any single gate silently drop its margin.
        seen = {}

        def fake_choose(*a, **k):
            seen[k.get("key")] = k.get("min_margin")
            return {"choice": None, "escalate": True}

        def fake_check_state(*a, **k):
            seen[str(k.get("state_text", "check"))[:24]] = k.get("min_margin")
            return {"answer": None, "escalate": True}

        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose", fake_choose)
        monkeypatch.setattr(system1, "check_state", fake_check_state)

        brain._needs_action_screenshot("Pressed 'tab'. Foreground window: 'X'.", "press_key")
        brain._session_may_hold_new_facts(["hello"])
        brain._laya_relay_intent("message george about it")
        import tools.people as people
        people.laya_guess_gender("x")
        people.laya_guess_relationship("x")
        webrtc_bridge._addressed_to_aster("something")

        assert seen.get("need_screenshot", 0) >= 0.5
        assert seen.get("new_fact", 0) >= 0.5
        assert seen.get("gender", 0) >= 0.5
        assert seen.get("relationship", 0) >= 0.5
        assert seen.get("addressed", 0) >= 0.6


# ── ID 8 hint is not computed for a relay turn ────────────────────────────────

class TestToolHintNotForRelays:
    def test_a_relay_turn_does_not_pick_a_tool(self, monkeypatch):
        calls = []
        import core.tool_routing as tr
        monkeypatch.setattr(tr, "pick_tool_for_request",
                            lambda text: calls.append(text) or {"tool": None})
        monkeypatch.setattr(config, "AUTO_COMPACT_ENABLED", False, raising=False)
        monkeypatch.setattr(brain, "_log_turn_mood", lambda t: None, raising=False)
        monkeypatch.setattr(brain.awareness, "render_context_block", lambda: None, raising=False)
        monkeypatch.setattr(brain, "trim_memory", lambda m: m, raising=False)
        monkeypatch.setattr(brain, "publish_terminal", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: {"role": "assistant", "content": "Very good."})
        monkeypatch.setattr(brain, "send_discord_message",
                            lambda *a, **k: "[System Note: Message delivered]")
        saved = list(brain.messages)
        try:
            brain.messages[:] = [brain.messages[0]]
            brain.process_user_input("tell george this link https://example.com", None)
        finally:
            brain.messages[:] = saved
        assert calls == [], "the ID 8 hint must not be computed for a relay turn"


# ── memory writes are serialized ──────────────────────────────────────────────

class TestWriteLock:
    def test_concurrent_writes_do_not_interleave_the_gate(self, monkeypatch, tmp_path):
        monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True, raising=False)
        monkeypatch.setattr(memory, "MD_FILE", str(tmp_path / "m.md"), raising=False)
        monkeypatch.setattr(memory, "memory_collection", MagicMock(), raising=False)
        inside = {"n": 0, "max": 0}

        def slow_gate(fact, source="agent"):
            inside["n"] += 1
            inside["max"] = max(inside["max"], inside["n"])
            time.sleep(0.05)
            inside["n"] -= 1
            return {"skip": False, "reason": "", "category": "fact",
                    "conflict": "", "supersedes": None}

        monkeypatch.setattr(memory, "_gate_fact", slow_gate)
        threads = [threading.Thread(target=lambda i=i: memory.memorize_fact("fact %d" % i))
                   for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert inside["max"] == 1   # never two writers inside the gate at once


@pytest.fixture(autouse=True)
def _isolate_brain_turn(tmp_path, monkeypatch):
    """QA round 8: these tests run a real admin turn, which appended to the real
    Aster_Vault/Conversations/ and could tag the text with a mood (changing the input)."""
    import config as _c
    monkeypatch.setattr(_c, "CONVERSATIONS_DIR", str(tmp_path / "Conversations"), raising=False)
    monkeypatch.setattr(_c, "MOOD_LOG_PATH", str(tmp_path / "emotion_log.jsonl"), raising=False)
    monkeypatch.setattr(brain, "_maybe_tag_text_mood", lambda t: t, raising=False)
