"""Tests pinning the round-2 QA fixes (2026-09-28).

  CACHE — the Discord system prompt must be byte-stable across messages (it used to be
          rewritten with a query-dependent fact list, re-prefilling the whole
          conversation every turn).
  CACHE — consolidation sends the full ADMIN_TOOLS so it keeps the main prefix warm.
  the relay guess gate is enforced by the TOOL, not only by an advisory directive.
  Sentry Route 2 is reachable (a function-local `import base64` used to shadow it).
  an awareness trigger must not latch its cooldown when the moment is bad.
  a chosen key with no probability in the distribution escalates.
  an inconclusive gender/relationship guess is not re-run forever.
No model, no network, no camera.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.system1 as system1
import tools.awareness as awareness
import tools.conversations as conversations
import tools.discord_api as discord_api
import tools.people as people
import tools.sentry as sentry


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(people, "_PATH", str(tmp_path / "people.json"), raising=False)
    # QA 2026-09-29: process_discord_chat now consults the conversation-ending store.
    monkeypatch.setattr(conversations, "_PATH", str(tmp_path / "conv.json"), raising=False)


# ── CACHE: Discord prompt stability ───────────────────────────────────────────

class TestDiscordPromptStability:
    def test_system_prompt_is_byte_stable_across_messages(self, monkeypatch):
        # QA round 3: the first version could not fail — with no staged facts and the
        # kernel forced off, a query-dependent get_user_facts returns the SAME string.
        # So assert the CALL SHAPE (no query may be passed) AND byte-stability.
        seen_queries = []

        def spy_facts(name, query=None):
            seen_queries.append(query)
            return "farah plays guitar"

        monkeypatch.setattr(brain, "get_user_facts", spy_facts)
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: {"role": "assistant", "content": "Very good."})
        monkeypatch.setattr(brain, "_laya_relay_intent", lambda text: False)
        brain.discord_chat_histories.clear()
        brain.process_discord_chat("george", "first message about the weather")
        first = brain.discord_chat_histories["george"][0]["content"]
        brain.process_discord_chat("george", "a completely different second message")
        second = brain.discord_chat_histories["george"][0]["content"]
        assert first == second, "the Discord system prompt must not vary per message"
        assert all(q is None for q in seen_queries), \
            f"the Discord path must not use a per-message fact query: {seen_queries}"
        assert "farah plays guitar" in first
        brain.discord_chat_histories.clear()


# ── the relay guess gate is enforced by the tool ──────────────────────────────

class TestRelayGuessEnforcedByTool:
    def test_assume_guess_refuses_even_for_an_exact_key(self, monkeypatch):
        """The parser hands the tool a CANONICAL key, so resolve_contact succeeded and
        the confirmation was skipped. assume_guess closes that."""
        monkeypatch.setattr(discord_api, "CONTACTS", {"george": "123"}, raising=False)
        posted = []
        monkeypatch.setattr(discord_api.requests, "post",
                            MagicMock(side_effect=lambda *a, **k: posted.append(1)))
        out = discord_api.send_discord_message("george", "hello", confirm=False, assume_guess=True)
        assert "CONFIRM REQUIRED" in out and out.startswith("FAILED")
        assert posted == []          # nothing was sent

    def test_assume_guess_with_confirm_sends(self, monkeypatch):
        monkeypatch.setattr(discord_api, "CONTACTS", {"george": "123"}, raising=False)
        monkeypatch.setattr(discord_api, "DISCORD_BOT_TOKEN", "t", raising=False)
        monkeypatch.setattr(discord_api.requests, "post",
                            MagicMock(side_effect=discord_api.requests.RequestException("no net")))
        out = discord_api.send_discord_message("george", "hello", confirm=True, assume_guess=True)
        assert "CONFIRM REQUIRED" not in out
        assert "Failed to open Discord DM channel" in out   # it tried

    def test_exact_stated_name_still_sends(self, monkeypatch):
        monkeypatch.setattr(discord_api, "CONTACTS", {"george": "123"}, raising=False)
        monkeypatch.setattr(discord_api, "DISCORD_BOT_TOKEN", "t", raising=False)
        monkeypatch.setattr(discord_api.requests, "post",
                            MagicMock(side_effect=discord_api.requests.RequestException("no net")))
        out = discord_api.send_discord_message("george", "hello")
        assert "CONFIRM REQUIRED" not in out


# ── Sentry Route 2 reachable ──────────────────────────────────────────────────

class TestSentryRoute2:
    def test_route2_runs_when_both_earlier_routes_return_none(self, monkeypatch):
        """A function-local `import base64` in the UNKNOWN branch used to shadow the
        module global, so Route 2 raised UnboundLocalError and silently no-opped."""
        import numpy as np
        import cv2
        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        ok, buf = cv2.imencode(".jpg", frame)
        assert ok
        import base64 as _b64
        # Raw base64, exactly like the real capture_frame_base64 (no data: prefix).
        img_b64 = _b64.b64encode(buf.tobytes()).decode()

        monkeypatch.setattr(sentry, "capture_frame_base64", lambda: img_b64, raising=False)
        monkeypatch.setattr(sentry, "_classify_frame_from_faces", lambda b64: None, raising=False)
        monkeypatch.setattr(sentry, "_analyze_frame_with_llm", lambda b64: None, raising=False)
        ran = []
        monkeypatch.setattr(sentry.face_recognition, "face_locations",
                            lambda rgb: ran.append(1) or [], raising=False)
        # A leaked WAITING_FOR_ID=True from an earlier test early-returns the sweep.
        monkeypatch.setattr(sentry, "WAITING_FOR_ID", False, raising=False)
        bot = MagicMock()
        monkeypatch.setattr(sentry, "telegram_bot", bot, raising=False)
        monkeypatch.setattr(sentry, "chat_id", 1, raising=False)
        sentry.execute_sentry_sweep()          # must not raise
        assert ran == [1], "Route 2 (face_recognition) must actually run"
        bot.send_message.assert_not_called()   # no faces -> nothing to report


# ── awareness triggers must not latch on a bad moment ─────────────────────────

class TestAwarenessTriggerLatching:
    def test_env_nudge_does_not_advance_its_cooldown_when_deferred(self, monkeypatch):
        # QA round 3: the first version was vacuous — lighting was never "dim", so the
        # trigger returned at the cheap guard whether or not the gate existed. Set up
        # the state so the trigger WOULD fire, then assert the gate stops it first.
        monkeypatch.setattr(awareness, "_good_moment_to_speak", lambda: False)
        monkeypatch.setattr(awareness, "_initiative_settings",
                            lambda: {"env_nudges": True}, raising=False)
        monkeypatch.setattr(awareness, "_last_env_nudge", 0.0, raising=False)
        awareness.current_context["lighting"] = "dim"
        awareness._maybe_trigger_env_nudge("normal")
        assert awareness._last_env_nudge == 0.0   # latch NOT advanced
        awareness.current_context["lighting"] = None

    def test_env_nudge_latches_when_the_moment_is_good(self, monkeypatch):
        # positive control: with the gate open the same setup DOES latch.
        monkeypatch.setattr(awareness, "_good_moment_to_speak", lambda: True)
        # QA round 7: the budget is now checked before the latch too.
        monkeypatch.setattr(awareness, "_budget_remaining", lambda: True)
        monkeypatch.setattr(awareness, "_initiative_settings",
                            lambda: {"env_nudges": True}, raising=False)
        monkeypatch.setattr(awareness, "_last_env_nudge", 0.0, raising=False)
        monkeypatch.setattr(awareness, "_push_nudge", lambda nudge: None, raising=False)
        awareness.current_context["lighting"] = "dim"
        awareness._maybe_trigger_env_nudge("normal")
        assert awareness._last_env_nudge > 0.0
        awareness.current_context["lighting"] = None

    def test_moment_gate_is_memoized_per_tick(self, monkeypatch):
        # QA round 3 cost fix: five triggers + _push_nudge all ask within one poll.
        calls = []
        monkeypatch.setattr(awareness, "_good_moment_to_speak_uncached",
                            lambda: calls.append(1) or True, raising=False)
        awareness._MOMENT_CACHE.update(at=0.0, ok=True)
        for _ in range(5):
            awareness._good_moment_to_speak()
        assert len(calls) == 1


# ── a chosen key with no probability must escalate ────────────────────────────

class TestAbsentKeyMargin:
    def test_choice_absent_from_the_distribution_has_no_margin(self):
        answer = {"choice": "A", "probabilities": {"B": 0.9, "C": 0.1}}
        choice, margin, _ = system1._extract_choice(answer, {"A", "B", "C"})
        assert choice == "A" and margin is None   # not 0.8 borrowed from B/C


# ── inconclusive guesses are not re-run forever ───────────────────────────────

class TestNoReGuess:
    def test_gender_is_guessed_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(people, "laya_guess_gender",
                            lambda name, facts="": calls.append(1) or
                            {"gender": "unknown", "escalate": True, "reason": "x"})
        people.resolve_identity("masky")
        people.resolve_identity("masky")
        people.resolve_identity("masky")
        assert len(calls) == 1

    def test_relationship_is_guessed_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(people, "laya_guess_relationship",
                            lambda name, facts="": calls.append(1) or
                            {"relationship": "unknown", "escalate": True, "reason": "x"})
        people.resolve_relationship("masky")
        people.resolve_relationship("masky")
        assert len(calls) == 1
        assert people.resolve_relationship("masky") == "friend"


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """QA round 4: these tests set module-level state that leaked into later tests
    (sentry.WAITING_FOR_ID stayed True for the rest of the session)."""
    import tools.sentry as _sentry
    import tools.awareness as _awareness
    monkeypatch.setattr(_sentry, "WAITING_FOR_ID", False, raising=False)
    _awareness._MOMENT_CACHE.update(at=0.0, ok=True)
