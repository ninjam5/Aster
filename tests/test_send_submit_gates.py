"""Tests for the send/submit gates added in QA rounds 4-5 (previously untested).

These are the model-reachable security paths the round-6 review flagged: without them a
regression would go unnoticed.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import tools.dom as dom


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


class TestSubmitNeedsConfirm:
    def test_allow_policy_never_needs_confirm(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "allow")
        assert dom.submit_needs_confirm("login form") is False

    def test_confirm_policy_needs_confirm_for_a_plain_goal(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        assert dom.submit_needs_confirm("login form") is True

    def test_search_like_goals_are_exempt(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        for goal in ["the search bar", "find my order", "query field", "look up flights"]:
            assert dom.submit_needs_confirm(goal) is False, goal

    def test_unknown_policy_is_validated_to_confirm(self, monkeypatch):
        # `_send_policy` itself validates: anything unexpected fails closed to "confirm".
        monkeypatch.setattr(config, "DOM_MOTOR_SEND_POLICY", "banana", raising=False)
        assert dom._send_policy() == "confirm"


class TestSmartTypeSubmitGate:
    def test_a_non_search_submit_is_blocked(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "locate_ui_element_ex", MagicMock(), raising=False)
        out = brain.execute_tool("smart_type", {"goal": "the login form",
                                                "text": "user", "submit": True})
        assert out.startswith("FAILED") and "BLOCKED" in out

    def test_a_search_submit_is_allowed(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        typed = []
        monkeypatch.setattr(brain, "locate_ui_element_ex",
                            lambda goal: {"x": 1, "y": 2, "source": "uia",
                                          "text": "Search", "score": 0.9}, raising=False)
        monkeypatch.setattr(brain.pyautogui, "click", lambda *a: None, raising=False)
        monkeypatch.setattr(brain.pyautogui, "hotkey", lambda *a: None, raising=False)
        monkeypatch.setattr(brain.pyperclip, "copy", lambda t: typed.append(t), raising=False)
        # QA round 7: the real UIA type is "EditControl"; with "Edit" the focus guard
        # rejected it and the test passed without ever typing.
        monkeypatch.setattr(brain, "focused_control_type", lambda: "EditControl", raising=False)
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None, raising=False)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda t, action: t,
                            raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        out = brain.execute_tool("smart_type", {"goal": "the search bar",
                                                "text": "weather", "submit": True})
        assert "BLOCKED" not in out
        assert typed == ["weather"]            # it actually typed
        assert pressed == ["enter"]            # and submitted
        assert out.startswith("Typed")

    def test_confirm_send_true_bypasses_the_gate(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "locate_ui_element_ex",
                            lambda goal: {"x": 1, "y": 2, "source": "uia",
                                          "text": "Submit", "score": 0.9}, raising=False)
        monkeypatch.setattr(brain, "focused_control_type", lambda: "EditControl", raising=False)
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None, raising=False)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda t, action: t,
                            raising=False)
        # QA round 7: patch the input primitives — this used to really click (1,1),
        # Ctrl+A, Ctrl+V and Enter on the owner's desktop.
        typed = []
        monkeypatch.setattr(brain.pyperclip, "copy", lambda t: typed.append(t), raising=False)
        monkeypatch.setattr(brain.pyautogui, "click", lambda *a: None, raising=False)
        monkeypatch.setattr(brain.pyautogui, "hotkey", lambda *a: None, raising=False)
        monkeypatch.setattr(brain.pyautogui, "press", lambda *a: None, raising=False)
        out = brain.execute_tool("smart_type", {"goal": "the login form", "text": "u",
                                                "submit": True, "confirm_send": True})
        assert "BLOCKED" not in out and typed == ["u"]


class TestPressKeySubmitGate:
    def test_enter_is_blocked_on_a_non_search_window(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Checkout",
                            raising=False)
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        out = brain.execute_tool("press_key", {"key": "enter"})
        assert out.startswith("FAILED") and "BLOCKED" in out
        assert pressed == []

    def test_space_is_gated_too(self, monkeypatch):
        monkeypatch.setattr(dom, "_send_policy", lambda: "confirm")
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Checkout",
                            raising=False)
        out = brain.execute_tool("press_key", {"key": "space"})
        assert "BLOCKED" in out

    def test_enter_is_allowed_on_a_search_window(self, monkeypatch):
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

    def test_an_ordinary_key_is_not_gated(self, monkeypatch):
        pressed = []
        monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k),
                            raising=False)
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None, raising=False)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda t, action: t,
                            raising=False)
        out = brain.execute_tool("press_key", {"key": "tab"})
        assert "BLOCKED" not in out
        assert pressed == ["tab"]   # QA round 7: prove it actually pressed


class TestNativeClickFailClosed:
    def test_a_gate_error_refuses_instead_of_clicking(self, monkeypatch):
        monkeypatch.setattr(brain, "locate_ui_element_ex",
                            lambda goal: {"x": 1, "y": 2, "source": "uia",
                                          "text": "Submit order", "score": 0.9}, raising=False)
        monkeypatch.setattr(brain.config, "USE_DOM_MOTOR", False, raising=False)
        monkeypatch.setattr(dom, "is_send_like",
                            MagicMock(side_effect=RuntimeError("boom")), raising=False)
        clicked = []
        monkeypatch.setattr(brain.pyautogui, "click", lambda *a: clicked.append(1),
                            raising=False)
        out = brain.execute_tool("smart_click", {"goal": "the Submit order button"})
        assert out.startswith("FAILED") and "BLOCKED" in out
        assert clicked == []
