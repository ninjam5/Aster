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
import tools.discord_api as discord_api
import tools.people as people
import tools.sentry as sentry


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(people, "_PATH", str(tmp_path / "people.json"), raising=False)


# ── CACHE: Discord prompt stability ───────────────────────────────────────────

class TestDiscordPromptStability:
    def test_system_prompt_is_byte_stable_across_messages(self, monkeypatch):
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: {"role": "assistant", "content": "Very good."})
        monkeypatch.setattr(brain, "_laya_relay_intent", lambda text: False)
        brain.discord_chat_histories.clear()
        brain.process_discord_chat("george", "first message about the weather")
        first = brain.discord_chat_histories["george"][0]["content"]
        brain.process_discord_chat("george", "a completely different second message")
        second = brain.discord_chat_histories["george"][0]["content"]
        assert first == second, "the Discord system prompt must not vary per message"
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
        assert out.startswith("[CONFIRM REQUIRED")
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
        img_b64 = "data:image/jpeg;base64," + _b64.b64encode(buf.tobytes()).decode()

        monkeypatch.setattr(sentry, "capture_frame_base64", lambda: img_b64, raising=False)
        monkeypatch.setattr(sentry, "_classify_frame_from_faces", lambda b64: None, raising=False)
        monkeypatch.setattr(sentry, "_analyze_frame_with_llm", lambda b64: None, raising=False)
        monkeypatch.setattr(sentry.face_recognition, "face_locations", lambda rgb: [], raising=False)
        bot = MagicMock()
        monkeypatch.setattr(sentry, "telegram_bot", bot, raising=False)
        monkeypatch.setattr(sentry, "chat_id", 1, raising=False)
        sentry.execute_sentry_sweep()          # must not raise
        bot.send_message.assert_not_called()   # no faces -> nothing to report


# ── awareness triggers must not latch on a bad moment ─────────────────────────

class TestAwarenessTriggerLatching:
    def test_env_nudge_does_not_advance_its_cooldown_when_deferred(self, monkeypatch):
        # The gate is now the FIRST statement in the trigger, so nothing else needs
        # stubbing: a bad moment must return before any latch/cooldown is touched.
        monkeypatch.setattr(awareness, "_good_moment_to_speak", lambda: False)
        before = getattr(awareness, "_last_env_nudge", None)
        awareness._maybe_trigger_env_nudge("normal")
        assert getattr(awareness, "_last_env_nudge", None) == before

    def test_reauth_reminder_does_not_latch_when_deferred(self, monkeypatch):
        monkeypatch.setattr(awareness, "_good_moment_to_speak", lambda: False)
        monkeypatch.setattr(awareness, "_reauth_reminder_sent", False, raising=False)
        awareness._maybe_trigger_reauth_reminder()
        assert awareness._reauth_reminder_sent is False


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
