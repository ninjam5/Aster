"""Tests for Phase 4 — live call + background daemons (laya-integration.md).

  ID 19 — webrtc_bridge._addressed_to_aster (is the utterance for Aster?)
  ID 20 — awareness._good_moment_to_speak (should we speak now?)
  ID 5  — awareness._brief_scene_describe (screen from window/UIA text)
  ID 4  — sentry._classify_frame_from_faces (text-only person classification)

These are LIVE, latency-sensitive paths, so every one is FAIL-OPEN: kernel off, low
margin, a failure or an unrecognized verdict returns today's behaviour. The only hard
stops are deterministic (the brain is mid-turn) or a complete answer (no faces).

No model, no camera, no LiveKit: Laya and the frame decoding are mocked.
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
import tools.sentry as sentry
import webrtc_bridge


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


# ── ID 19: addressed to Aster? ────────────────────────────────────────────────

class TestAddressedToAster:
    def test_naming_aster_needs_no_model(self, monkeypatch):
        called = []
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        assert webrtc_bridge._addressed_to_aster("Hey Aster, what's the weather?") is True
        assert called == []

    def test_kernel_off_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert webrtc_bridge._addressed_to_aster("what time is it") is True

    def test_confident_not_for_aster_is_ignored(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        assert webrtc_bridge._addressed_to_aster("did you take the bins out") is False

    def test_confident_addressed_is_sent(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.8})
        assert webrtc_bridge._addressed_to_aster("turn the lights off") is True

    def test_escalation_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": True})
        assert webrtc_bridge._addressed_to_aster("maybe a question") is True

    def test_kernel_failure_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        assert webrtc_bridge._addressed_to_aster("something") is True

    def test_empty_transcript_is_not_a_turn(self):
        assert webrtc_bridge._addressed_to_aster("") is False


# ── ID 20: good moment to speak? ──────────────────────────────────────────────

class TestGoodMomentToSpeak:
    def test_never_interrupts_a_mid_turn_brain(self, monkeypatch):
        awareness._brain_busy.set()
        try:
            called = []
            monkeypatch.setattr(system1, "check_state", lambda *a, **k: called.append(1) or {})
            assert awareness._good_moment_to_speak() is False
            assert called == []   # deterministic, no model call
        finally:
            awareness._brain_busy.clear()

    def test_kernel_off_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert awareness._good_moment_to_speak() is True

    def test_bad_moment_is_respected(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": False, "escalate": False, "margin": 0.8})
        assert awareness._good_moment_to_speak() is False

    def test_escalation_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": False, "escalate": True})
        assert awareness._good_moment_to_speak() is True

    def test_failure_fails_open(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "check_state", boom)
        assert awareness._good_moment_to_speak() is True

    def test_push_nudge_is_dropped_on_a_bad_moment(self, monkeypatch):
        monkeypatch.setattr(awareness, "_budget_remaining", lambda: True)
        monkeypatch.setattr(awareness, "_good_moment_to_speak", lambda: False)
        delivered = []
        monkeypatch.setattr(awareness, "_record_unsolicited", lambda: delivered.append("sent"))
        awareness._push_nudge("[System Internal: say something]")
        assert delivered == []   # nothing recorded, nothing sent


# ── ID 5: screen from window/UIA text ─────────────────────────────────────────

class TestSceneDescribe:
    def _capture(self, monkeypatch):
        seen = {}

        def fake_completion(messages, temperature=None, n_predict=None, **k):
            seen["content"] = messages[0]["content"]
            return {"content": "SCREEN: code editor\nWEBCAM: a man, well lit"}

        monkeypatch.setattr(brain, "_execute_llm_completion", fake_completion)
        return seen

    def test_screen_text_removes_the_screen_image(self, monkeypatch):
        monkeypatch.setattr(brain, "_foreground_text_state",
                            lambda: "Foreground window: 'Notepad'; Visible elements: edit: Body")
        seen = self._capture(monkeypatch)
        out = awareness._brief_scene_describe("data:image/jpeg;base64,AAAA", "data:image/jpeg;base64,BBBB")
        assert out["screen"].startswith("Foreground window: 'Notepad'")
        images = [c for c in seen["content"] if c.get("type") == "image_url"]
        assert len(images) == 1   # only the webcam frame
        assert "BBBB" in images[0]["image_url"]["url"]

    def test_no_webcam_and_text_screen_skips_the_llm_entirely(self, monkeypatch):
        monkeypatch.setattr(brain, "_foreground_text_state", lambda: "Foreground window: 'X'")
        called = []
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: called.append(1) or {"content": ""})
        out = awareness._brief_scene_describe("data:image/jpeg;base64,AAAA", None)
        assert out["screen"] == "Foreground window: 'X'"
        assert called == []

    def test_text_unavailable_keeps_the_old_two_image_path(self, monkeypatch):
        monkeypatch.setattr(brain, "_foreground_text_state", lambda: "")
        seen = self._capture(monkeypatch)
        awareness._brief_scene_describe("data:image/jpeg;base64,AAAA", "data:image/jpeg;base64,BBBB")
        images = [c for c in seen["content"] if c.get("type") == "image_url"]
        assert len(images) == 2

    def test_no_images_at_all_is_a_noop(self):
        out = awareness._brief_scene_describe(None, None)
        assert out == {"screen": None, "webcam": None, "lighting": None}


# ── ID 4: Sentry text classification ──────────────────────────────────────────

class TestSentryClassification:
    def _report(self, monkeypatch, report, names=("mohamed", "george")):
        monkeypatch.setattr(sentry, "_face_match_report", lambda b64: (report, list(names)))

    def test_no_faces_is_empty_without_a_model(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        self._report(monkeypatch, "no faces visible")
        called = []
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        assert sentry._classify_frame_from_faces("b64") == "EMPTY"
        assert called == []

    def test_owner_label(self, monkeypatch):
        self._report(monkeypatch, "face 1: closest enrolled face is 'mohamed' (distance 0.31)")
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.8})
        assert sentry._classify_frame_from_faces("b64") == config.OWNER_NAME.upper()

    def test_known_label(self, monkeypatch):
        """The owner is excluded from the known list, so 'B' is the first OTHER name."""
        self._report(monkeypatch, "face 1: closest enrolled face is 'george' (distance 0.38)",
                     names=("mohamed", "george"))
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        assert sentry._classify_frame_from_faces("b64") == "KNOWN:george"

    def test_many_names_do_not_collide_with_the_special_keys(self, monkeypatch):
        """Regression: names used B,C,D… while EMPTY was also 'E', so the 4th enrolled
        person was silently overwritten by EMPTY and became unselectable."""
        self._report(monkeypatch, "face 1: distance 0.40",
                     names=("a", "b", "c", "d", "e"))
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "F", "escalate": False, "margin": 0.8})
        assert sentry._classify_frame_from_faces("b64") == "KNOWN:d"

    def test_kernel_off_skips_the_face_work_entirely(self, monkeypatch):
        """Kernel off must not add a per-sweep face pass to installs that never opted in."""
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        monkeypatch.setattr(sentry, "_face_match_report",
                            MagicMock(side_effect=AssertionError("must not decode")))
        assert sentry._classify_frame_from_faces("b64") is None

    def test_unknown_label(self, monkeypatch):
        self._report(monkeypatch, "face 1: closest enrolled face is 'george' (distance 0.72)")
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "U", "escalate": False, "margin": 0.8})
        assert sentry._classify_frame_from_faces("b64") == "UNKNOWN"

    def test_escalation_falls_back_to_vision(self, monkeypatch):
        self._report(monkeypatch, "face 1: distance 0.49")
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "U", "escalate": True})
        assert sentry._classify_frame_from_faces("b64") is None

    def test_cannot_tell_falls_back(self, monkeypatch):
        self._report(monkeypatch, "face 1: distance 0.49")
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "Z", "escalate": False, "margin": 0.8})
        assert sentry._classify_frame_from_faces("b64") is None

    def test_kernel_off_falls_back(self, monkeypatch):
        self._report(monkeypatch, "face 1: distance 0.49")
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert sentry._classify_frame_from_faces("b64") is None

    def test_undecodable_frame_falls_back(self, monkeypatch):
        # QA round 2: must enable the kernel, or the guard short-circuits and the
        # report mock is never consulted.
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        self._report(monkeypatch, "", names=())
        assert sentry._classify_frame_from_faces("b64") is None


# ── QA follow-ups (independent review, 2026-09-28) ────────────────────────────

class TestQAFollowUps:
    def test_aster_shortcut_is_word_bounded(self, monkeypatch):
        """"aster" must not fire inside master/disaster/faster/plaster."""
        called = []
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: called.append(1) or {"choice": "A", "escalate": False})
        webrtc_bridge._addressed_to_aster("I rebased the master branch")
        assert called == [1]   # no shortcut: the gate was consulted
        called.clear()
        webrtc_bridge._addressed_to_aster("aster, turn it off")
        assert called == []    # real name -> shortcut, no model call

    def test_push_nudge_records_once_when_allowed(self, monkeypatch):
        monkeypatch.setattr(awareness, "_budget_remaining", lambda: True)
        monkeypatch.setattr(awareness, "_good_moment_to_speak", lambda: True)
        recorded = []
        monkeypatch.setattr(awareness, "_record_unsolicited", lambda: recorded.append(1))
        # QA round 2: `_push_nudge` does `from core.brain import process_user_input`
        # INSIDE the function, so patching awareness.* was a no-op and the real brain
        # ran. Patch core.brain's attribute instead.
        brain_calls = []
        monkeypatch.setattr(brain, "process_user_input",
                            lambda text, media=None: brain_calls.append(text) or "ok")
        monkeypatch.setattr(awareness, "_send_telegram", lambda text: None)
        import webrtc_bridge as _w
        monkeypatch.setattr(_w, "call_is_active", lambda: False)
        awareness._push_nudge("[System Internal: hello]")
        assert recorded == [1]
        assert brain_calls == ["[System Internal: hello]"]   # actually routed

    def test_intervention_respects_the_moment_gate(self, monkeypatch):
        """Regression: _trigger_intervention had its own delivery path and could speak
        over a live call (the exact bug ID 20 fixes)."""
        import tools.intervention as intervention
        monkeypatch.setattr(awareness, "_good_moment_to_speak", lambda: False)
        delivered = []
        monkeypatch.setattr(intervention, "_send_telegram_intervention",
                            lambda t: delivered.append(t), raising=False)
        monkeypatch.setattr(intervention, "process_user_input",
                            MagicMock(side_effect=AssertionError("must not speak")),
                            raising=False)
        intervention._trigger_intervention("youtube", "some window")
        assert delivered == []

    def test_truncate_on_boundary_cuts_between_nodes(self):
        text = "Foreground window: 'X'; " + "; ".join(f"node {i}" for i in range(60))
        out = awareness._truncate_on_boundary(text, 80)
        assert len(out) <= 80
        assert not out.endswith(";")
        assert not out.endswith("nod")

    def test_truncate_on_boundary_short_text_untouched(self):
        assert awareness._truncate_on_boundary("short", 80) == "short"

    def test_predict_is_serialized(self, monkeypatch):
        """The kernel is called from four threads now; agent.predict is not documented
        as reentrant, so it runs under a lock."""
        import threading as _threading
        import time as _t

        state = {"inside": 0, "overlap": 0, "calls": 0}

        class _Agent:
            def predict(self, _state, _questions):
                state["inside"] += 1
                if state["inside"] > 1:
                    state["overlap"] += 1
                _t.sleep(0.05)
                state["inside"] -= 1
                state["calls"] += 1
                return {"answers": {}}

        monkeypatch.setattr(system1, "_load_model", lambda: _Agent())
        system1.reset_model()
        threads = [_threading.Thread(target=lambda: system1._predict({}, {"q": {}}))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state["calls"] == 4        # every call actually ran
        assert state["overlap"] == 0      # and never concurrently
        system1.reset_model()
