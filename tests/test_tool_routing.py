"""Tests for the overlapping-tool tie-break (ID 8 of laya-integration.md).

Several ADMIN_TOOLS descriptions overlap (research/browse_web, save_note/memorize_fact,
smart_type/type_text, look_at_screen/highlight_on_screen), which is a known source of
harness `single_tool` flakiness (C1-06). Laya picks within the cluster and the brain
injects a one-line round-0 hint. It is a HINT, never an override.

No model, no network: Laya is mocked. Ordinary chat must cost NO model call.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.system1 as system1
import core.tool_routing as tr


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


class TestTriggerPrefilter:
    def test_ordinary_chat_costs_no_model_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        out = tr.pick_tool_for_request("what time is it")
        assert out["cluster"] is None and out["tool"] is None
        assert called == []

    def test_empty_text_is_safe(self):
        assert tr.pick_tool_for_request("")["tool"] is None
        assert tr.pick_tool_for_request(None)["tool"] is None

    def test_triggers_are_cheap_and_targeted(self):
        for text, want in [("read this link https://x.com/a", "read_web"),
                           ("remember that I hate mushrooms", "remember"),
                           ("type my name in the search bar", "type"),
                           ("what's on my screen?", "screen"),
                           ("tell me a joke", None)]:
            got = None
            for name, spec in tr.CLUSTERS.items():
                if spec["trigger"].search(text):
                    got = name
                    break
            assert got == want, text


class TestPick:
    def _kernel(self, monkeypatch, choice, escalate=False, enabled=True):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: enabled)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": choice, "escalate": escalate,
                                             "margin": 0.8})

    def test_picks_a_tool(self, monkeypatch):
        self._kernel(monkeypatch, "research")
        out = tr.pick_tool_for_request("read this link https://example.com")
        assert out["cluster"] == "read_web" and out["tool"] == "research"
        assert out["escalate"] is False

    def test_picks_the_other_tool(self, monkeypatch):
        self._kernel(monkeypatch, "browse_web")
        out = tr.pick_tool_for_request("read this link https://example.com")
        assert out["tool"] == "browse_web"

    def test_none_key_means_no_hint(self, monkeypatch):
        self._kernel(monkeypatch, "Z")
        assert tr.pick_tool_for_request("read this link")["tool"] is None

    def test_escalation_means_no_hint(self, monkeypatch):
        self._kernel(monkeypatch, "research", escalate=True)
        out = tr.pick_tool_for_request("read this link")
        assert out["tool"] is None and out["cluster"] == "read_web"

    def test_kernel_off_means_no_hint_and_no_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        out = tr.pick_tool_for_request("read this link")
        assert out["tool"] is None and called == []

    def test_kernel_failure_means_no_hint(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        out = tr.pick_tool_for_request("read this link")
        assert out["tool"] is None and "kernel error" in out["reason"]

    def test_every_cluster_tool_is_a_real_tool_name(self):
        import core.brain as brain
        real = {t["function"]["name"] for t in brain.ADMIN_TOOLS}
        for name, spec in tr.CLUSTERS.items():
            for tool in spec["criteria"]:
                assert tool in real, f"{name}: {tool} is not a registered tool"


class TestHint:
    def test_hint_names_the_tool(self):
        hint = tr.hint_for_tool("research")
        assert "research" in hint and hint.startswith("[System note:")

    def test_hint_for_an_unknown_tool_is_empty(self):
        assert tr.hint_for_tool("play_spotify_track") == ""
        assert tr.hint_for_tool("") == ""
