"""Tests for the Phase 1 Laya gates in core/brain.py.

  ID 1 — post-action screenshot gate: attach the screenshot after a GUI action only
          when Laya is confident it is needed. The image enters conversation history
          and is re-prefilled on every later round, so a confident skip is a real
          saving; every uncertain path must attach it.

No model, no screen capture, no network.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.system1 as system1

CLEAN = ("Typed 'weather cairo' into field at (100, 200) (matched via ocr: 'Search', score 0.91). "
         "Foreground window: 'Google Chrome'. Submitted with Enter. [Verify silently — continue.]")
WARN = ("Clicked 'Submit' at (10, 20) (matched via ocr: 'Submit', score 0.40) — LOW CONFIDENCE, "
        "the click may have hit the wrong element. WARNING: the screen did NOT visibly change.")


@pytest.fixture(autouse=True)
def _no_real_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


class TestNeedsActionScreenshot:
    def test_warning_text_always_needs_it_without_a_model_call(self, monkeypatch):
        called = []
        # QA round 4: enable the kernel, or this passed via the kernel-off branch.
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        assert brain._needs_action_screenshot(WARN, "smart_click") is True
        assert called == []  # hard signal short-circuits the kernel

    def test_kernel_disabled_attaches(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert brain._needs_action_screenshot(CLEAN, "smart_type") is True

    def test_confident_no_skips(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        assert brain._needs_action_screenshot(CLEAN, "smart_type") is False

    def test_confident_yes_attaches(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.8})
        assert brain._needs_action_screenshot(CLEAN, "press_key") is True

    def test_escalation_attaches(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": True, "reason": "low margin"})
        assert brain._needs_action_screenshot(CLEAN, "press_key") is True

    def test_kernel_failure_attaches(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        assert brain._needs_action_screenshot(CLEAN, "press_key") is True


class TestAttachActionScreenshot:
    def test_skip_returns_text_and_never_captures(self, monkeypatch):
        monkeypatch.setattr(brain, "_needs_action_screenshot", lambda text, action: False)
        captured = []
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: captured.append(1) or "B64")
        out = brain._attach_action_screenshot(CLEAN, "smart_type")
        assert isinstance(out, str)
        assert "Screenshot omitted" in out
        assert captured == []

    def test_attach_returns_multimodal_dict(self, monkeypatch):
        monkeypatch.setattr(brain, "_needs_action_screenshot", lambda text, action: True)
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "B64")
        out = brain._attach_action_screenshot(WARN, "smart_click")
        assert isinstance(out, dict)
        assert out["ui_screenshot_b64"] == "B64"
        assert out["text"] == WARN

    def test_capture_failure_falls_back_to_text(self, monkeypatch):
        monkeypatch.setattr(brain, "_needs_action_screenshot", lambda text, action: True)
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: None)
        out = brain._attach_action_screenshot(CLEAN, "press_key")
        assert out == CLEAN


class TestVisionNecessityGate:
    """ID 2 — look_at_screen may be answered from window/UIA text (no screenshot).

    Conservative: only a confident "text is enough" skips the image; every other
    path (kernel off, escalate, failure, or an empty text state) uses vision.
    """

    def test_visual_request_never_uses_text(self, monkeypatch):
        """A live probe showed the model calling 'is the chart green or red?' text-answerable."""
        called = []
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or
                            {"choice": "A", "escalate": False})
        for req in ["is the chart green or red?", "describe the layout",
                    "what does the icon look like?", "is the text readable?"]:
            assert brain._screen_answerable_from_text(req) is False, req
        assert called == []  # hard-filtered before the kernel

    def test_kernel_off_uses_vision(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert brain._screen_answerable_from_text("what app is open") is False

    def test_confident_text_enough(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.8})
        assert brain._screen_answerable_from_text("what app is open") is True

    def test_confident_needs_image(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        assert brain._screen_answerable_from_text("is the chart green") is False

    def test_escalation_uses_vision(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": True})
        assert brain._screen_answerable_from_text("q") is False

    def test_kernel_failure_uses_vision(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        assert brain._screen_answerable_from_text("q") is False

    def test_foreground_text_state_includes_title_and_elements(self, monkeypatch):
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Notepad - notes.txt")
        import tools.uia as uia
        monkeypatch.setattr(uia, "uia_nodes",
                            lambda: ([{"role": "button", "name": "Save"},
                                      {"role": "edit", "name": "Body"},
                                      {"role": "button", "name": ""}], (1920, 1080)))
        out = brain._foreground_text_state()
        assert "Notepad - notes.txt" in out
        assert "button: Save" in out and "edit: Body" in out

    def test_foreground_text_state_survives_uia_failure(self, monkeypatch):
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Explorer")
        import tools.uia as uia

        def boom():
            raise RuntimeError("no uia")

        monkeypatch.setattr(uia, "uia_nodes", boom)
        assert brain._foreground_text_state() == "Foreground window: 'Explorer'"
