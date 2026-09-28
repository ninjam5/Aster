"""Tests for the round-8 security fixes (previously untested gates).

  F1 — smart_type's focus click is gated (native) and the DOM type path is gated
  F2 — press_key delete/backspace are destructive activations
  F4 — `confirm_send` as the STRING "false" must not bypass any gate (`_as_bool`)
  F5 — type_text's newline gate
  F7 — forget_fact takes the memory write lock
  F9 — Sentry's Route-2 fallback compares the configured owner name
"""
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
import tools.dom as dom
import tools.sentry as sentry


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


class TestAsBool:
    def test_strings(self):
        for s in ("false", "0", "no", "off", "False", " FALSE "):
            assert brain._as_bool(s) is False, s
        for s in ("true", "1", "yes", "on", "True"):
            assert brain._as_bool(s) is True, s

    def test_non_strings(self):
        assert brain._as_bool(True) is True
        assert brain._as_bool(False) is False
        assert brain._as_bool(None) is False
        assert brain._as_bool(0) is False
        assert brain._as_bool([]) is False


class TestConfirmSendStringBypass:
    """QA round 8: `bool("false")` is True, so a model emitting the STRING "false"
    self-approved every send gate."""

    def test_press_key_enter_with_the_string_false_is_still_blocked(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Checkout",
                            raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        out = brain.execute_tool("press_key", {"key": "enter", "confirm_send": "false"})
        assert out.startswith("FAILED") and "BLOCKED" in out
        assert pressed == []

    def test_smart_type_submit_with_the_string_false_is_blocked(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        # QA round 11: a bare MagicMock loc made is_send_like raise, so the FOCUS gate's
        # fail-closed produced the same BLOCKED string. Use a benign loc + benign
        # is_send_like and assert the submit-specific wording.
        monkeypatch.setattr(brain, "locate_ui_element_ex",
                            lambda goal: {"x": 1, "y": 2, "source": "uia",
                                          "text": "Message", "score": 0.9}, raising=False)
        monkeypatch.setattr(dom, "is_send_like", lambda node: False, raising=False)
        out = brain.execute_tool("smart_type", {"goal": "the login form", "text": "u",
                                                "submit": True, "confirm_send": "false"})
        assert "would submit a form" in out

    def test_boolean_true_still_bypasses(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Checkout",
                            raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None, raising=False)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda t, action: t,
                            raising=False)
        brain.execute_tool("press_key", {"key": "enter", "confirm_send": True})
        assert pressed == ["enter"]


class TestSmartTypeFocusGate:
    def test_a_send_like_focus_target_is_blocked(self, monkeypatch):
        """submit=False used to skip the only gate, so the focus CLICK activated it."""
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "locate_ui_element_ex",
                            lambda goal: {"x": 1, "y": 2, "source": "uia",
                                          "text": "Submit order", "score": 0.9}, raising=False)
        monkeypatch.setattr(brain.config, "USE_DOM_MOTOR", False, raising=False)
        clicked = []
        monkeypatch.setattr(brain.pyautogui, "click", lambda *a: clicked.append(1),
                            raising=False)
        out = brain.execute_tool("smart_type", {"goal": "the order form", "text": "x",
                                                "submit": False})
        assert out.startswith("FAILED") and "BLOCKED" in out
        assert clicked == []

    def test_the_dom_type_path_is_gated_too(self, monkeypatch):
        """QA round 8: _web_type_on_page had NO send gate, and with dom_motor on it runs
        before the native one."""
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(dom, "_snapshot_impl", lambda page: "x", raising=False)
        monkeypatch.setattr(dom, "filter_nodes", lambda snap: [{"role": "button",
                                                                "name": "Send", "text": ""}],
                            raising=False)
        monkeypatch.setattr(dom, "build_shortlist",
                            lambda *a, **k: [{"role": "button", "name": "Send", "text": "",
                                              "score": 0.9}], raising=False)
        monkeypatch.setattr(dom, "_kernel_pick", lambda *a, **k: None, raising=False)
        filled = []
        page = MagicMock()
        page.fill = MagicMock(side_effect=lambda *a, **k: filled.append(1))
        out = dom._web_type_on_page(page, "the Send button", "hi", submit=False)
        assert isinstance(out, str) and "BLOCKED" in out
        assert filled == []      # nothing was typed


class TestDestructiveKeys:
    def test_delete_and_backspace_are_gated(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Explorer",
                            raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        for key in ("delete", "backspace"):
            out = brain.execute_tool("press_key", {"key": key})
            assert "BLOCKED" in out, key
        assert pressed == []


class TestTypeTextNewlineGate:
    def test_multiline_text_is_blocked(self, monkeypatch):
        typed = []
        monkeypatch.setattr(brain.pyperclip, "copy", lambda t: typed.append(t), raising=False)
        out = brain.execute_tool("type_text", {"text": "echo hi\nrm -rf /"})
        assert out.startswith("FAILED") and "BLOCKED" in out
        assert typed == []

    def test_single_line_text_is_allowed(self, monkeypatch):
        typed = []
        monkeypatch.setattr(brain.pyperclip, "copy", lambda t: typed.append(t), raising=False)
        monkeypatch.setattr(brain.pyautogui, "hotkey", lambda *a: None, raising=False)
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None, raising=False)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda t, action: t,
                            raising=False)
        brain.execute_tool("type_text", {"text": "hello"})
        assert typed == ["hello"]

    def test_multiline_is_allowed_with_confirm_send(self, monkeypatch):
        typed = []
        monkeypatch.setattr(brain.pyperclip, "copy", lambda t: typed.append(t), raising=False)
        monkeypatch.setattr(brain.pyautogui, "hotkey", lambda *a: None, raising=False)
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None, raising=False)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda t, action: t,
                            raising=False)
        brain.execute_tool("type_text", {"text": "a\nb", "confirm_send": True})
        assert typed == ["a\nb"]


class TestForgetFactLock:
    def test_forget_fact_serializes_with_memorize_fact(self, monkeypatch, tmp_path):
        """QA round 9: the first version only counted entries into `_gate_fact`, which
        `forget_fact` never calls — an unlocked forget_fact kept it green. This measures
        BOTH critical sections."""
        monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True, raising=False)
        monkeypatch.setattr(memory, "MD_FILE", str(tmp_path / "m.md"), raising=False)
        monkeypatch.setattr(memory, "memory_collection", MagicMock(), raising=False)
        with open(memory.MD_FILE, "w", encoding="utf-8") as f:
            f.write("- **[2026-01-01 00:00:00]** hello world\n")
        inside = {"n": 0, "max": 0}

        def _enter():
            inside["n"] += 1
            inside["max"] = max(inside["max"], inside["n"])
            time.sleep(0.05)
            inside["n"] -= 1

        def slow_gate(fact, source="agent"):
            _enter()
            return {"skip": False, "reason": "", "category": "fact",
                    "conflict": "", "supersedes": None}

        def slow_forget(text):
            _enter()
            return "[System Note: forgotten]"

        monkeypatch.setattr(memory, "_gate_fact", slow_gate)
        monkeypatch.setattr(memory, "_forget_fact_locked", slow_forget)
        threads = [threading.Thread(target=lambda: memory.memorize_fact("new fact")),
                   threading.Thread(target=lambda: memory.forget_fact("hello world"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert inside["max"] == 1, "forget_fact and memorize_fact overlapped"


class TestSentryRoute2OwnerName:
    def test_the_configured_owner_is_not_alerted_in_the_fallback(self, monkeypatch):
        import numpy as np
        import cv2
        import base64 as _b64
        ok, buf = cv2.imencode(".jpg", np.zeros((40, 40, 3), dtype=np.uint8))
        frame = _b64.b64encode(buf.tobytes()).decode()

        monkeypatch.setattr(config, "OWNER_NAME", "Dan", raising=False)
        monkeypatch.setattr(sentry, "capture_frame_base64", lambda: frame, raising=False)
        monkeypatch.setattr(sentry, "_classify_frame_from_faces", lambda b64: None, raising=False)
        monkeypatch.setattr(sentry, "_analyze_frame_with_llm", lambda b64: None, raising=False)
        monkeypatch.setattr(sentry.face_recognition, "face_locations",
                            lambda rgb: [(0, 0, 1, 1)], raising=False)
        monkeypatch.setattr(sentry.face_recognition, "face_encodings",
                            lambda rgb, locs: [np.zeros(128)], raising=False)
        monkeypatch.setattr(sentry.face_recognition, "compare_faces",
                            lambda known, enc, tolerance=0.5: [True], raising=False)
        monkeypatch.setattr(sentry, "known_face_encodings", [np.zeros(128)], raising=False)
        monkeypatch.setattr(sentry, "known_face_names", ["Dan"], raising=False)
        monkeypatch.setattr(sentry, "WAITING_FOR_ID", False, raising=False)
        bot = MagicMock()
        monkeypatch.setattr(sentry, "telegram_bot", bot, raising=False)
        monkeypatch.setattr(sentry, "chat_id", 1, raising=False)
        sentry.notification_cooldowns.clear()

        sentry.execute_sentry_sweep()   # Route 2 must skip the owner
        bot.send_message.assert_not_called()
        assert sentry.WAITING_FOR_ID is False


# ── QA round 9 ────────────────────────────────────────────────────────────────

class TestRound9Gates:
    def test_del_alias_is_gated(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Explorer",
                            raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        out = brain.execute_tool("press_key", {"key": "del"})
        assert "BLOCKED" in out
        assert pressed == []

    def test_destructive_keys_ignore_the_search_exemption(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title",
                            lambda: "Google Search - Chrome", raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        for key in ("delete", "backspace"):
            assert "BLOCKED" in brain.execute_tool("press_key", {"key": key}), key
        assert pressed == []

    def test_submit_keys_keep_the_search_exemption(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title",
                            lambda: "Google Search - Chrome", raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None, raising=False)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda t, action: t,
                            raising=False)
        brain.execute_tool("press_key", {"key": "enter"})
        assert pressed == ["enter"]

    def test_smart_type_multiline_is_blocked(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        # QA round 10: the focus gate's fail-closed error also yields BLOCKED; assert the
        # NEWLINE-specific message with a benign loc so the newline gate is what fires.
        monkeypatch.setattr(brain, "locate_ui_element_ex",
                            lambda goal: {"x": 1, "y": 2, "source": "uia",
                                          "text": "Message", "score": 0.9}, raising=False)
        monkeypatch.setattr(dom, "is_send_like", lambda node: False, raising=False)
        out = brain.execute_tool("smart_type", {"goal": "the terminal", "text": "a\nb"})
        assert "contains a newline" in out

    def test_state_string_false_turns_a_mode_off(self, monkeypatch):
        import config as _c
        monkeypatch.setattr(_c, "AWARENESS_ACTIVE", True, raising=False)
        out = brain.execute_tool("toggle_awareness_mode", {"state": "false"})
        assert "OFF" in out or "off" in out.lower()
        assert isinstance(_c.AWARENESS_ACTIVE, bool) and _c.AWARENESS_ACTIVE is False


class TestAffirmativeConfirmation:
    def test_affirmatives_are_recognised(self):
        for t in ["yes", "yes, send it to george", "ok go ahead", "confirm", "Sure!"]:
            assert brain._looks_affirmative(t) is True, t

    def test_non_affirmatives(self):
        for t in ["tell geroge I'll be late", "no", "what time is it"]:
            assert brain._looks_affirmative(t) is False, t

    def test_the_decision_helper_covers_every_case(self, monkeypatch):
        """QA round 10: driving a whole turn was config-dependent and order-sensitive, so
        the decision lives in `_should_force_relay_confirm` and is tested directly."""
        guess = {"target": "george", "payload": "x", "exact": False}
        exact = {"target": "george", "payload": "x", "exact": True}

        brain._clear_pending_relay_confirm()
        # a fresh guess is always forced (the pending target is captured BEFORE the turn)
        assert brain._should_force_relay_confirm(guess, "tell geroge I'm late", "") is True
        # an affirmative prefix with NO pre-turn pending is still a new guess
        assert brain._should_force_relay_confirm(guess, "ok tell geroge I'm late", "") is True
        # an exact relay is never forced
        assert brain._should_force_relay_confirm(exact, "tell george I'm late", "george") is False
        # a pending for THIS target + an affirmative reply is the confirmation
        assert brain._should_force_relay_confirm(guess, "yes, send it to george", "george") is False
        assert brain._should_force_relay_confirm(guess, "[Mood: happy] yes, do it", "george") is False
        # ... but a NEW command in the same window is still forced
        assert brain._should_force_relay_confirm(guess, "tell geroge I'm late", "george") is True
        # a pending for a DIFFERENT target must not authorise this one (round 12)
        other = {"target": "bob", "payload": "x", "exact": False}
        assert brain._should_force_relay_confirm(other, "yes, send it to bob", "george") is True
        brain._clear_pending_relay_confirm()

    def test_the_tagged_affirmative_is_recognised(self):
        assert brain._looks_affirmative("[Mood: happy] yes, send it to george") is True

    def test_the_four_toggle_tools_accept_a_string_state(self, monkeypatch):
        """QA round 10/11: an undefined _as__as_bool left these raising NameError, and
        the first version asserted only 'not an error' — a deleted handler returns None
        and also passed. Assert the POLARITY."""
        import tools.awareness as _aw
        monkeypatch.setattr(config, "MOOD_ACTIONS_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "MOOD_CHECKIN_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "FACE_EMOTION_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "AMBIENT_AUDIO_ENABLED", True, raising=False)
        monkeypatch.setattr(_aw, "AWARENESS_ACTIVE", True, raising=False)
        for tool in ("toggle_mood_actions", "toggle_mood_checkins",
                     "toggle_face_emotion", "toggle_ambient_audio"):
            out = brain.execute_tool(tool, {"state": "false"})
            assert "OFF" in str(out), (tool, out)


@pytest.fixture(autouse=True)
def _reset_pending_relay(monkeypatch):
    """QA round 11: `_pending_relay_confirm` is module-global."""
    brain._clear_pending_relay_confirm()
    yield
    brain._clear_pending_relay_confirm()
