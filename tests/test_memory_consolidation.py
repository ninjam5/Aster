"""Tests for the memory-consolidation changes of Phase 1 (laya-integration.md).

  ID 7a — the consolidation call sends ONLY the `memorize_fact` schema (it never reads
          any other tool from the response), instead of all 69 tools.
  ID 3  — a Laya pre-filter skips the expensive 35B consolidation pass when the recent
          window is confidently free of new durable facts. Any doubt runs the pass.

No model, no llama-server, no network.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.system1 as system1


@pytest.fixture(autouse=True)
def _no_real_logging(monkeypatch):
    """Never write to the real reliability log from these tests."""
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "MEMORY_AVAILABLE", True, raising=False)


# ── ID 7a: schema trim ────────────────────────────────────────────────────────

class TestMemorizeOnlyTools:
    def test_contains_exactly_memorize_fact(self):
        names = [t["function"]["name"] for t in brain._MEMORIZE_ONLY_TOOLS]
        assert names == ["memorize_fact"]

    def test_consolidation_call_receives_only_that_schema(self, monkeypatch):
        monkeypatch.setattr(brain, "_session_may_hold_new_facts", lambda turns: (True, "test"))
        seen = {}

        def fake_completion(messages, temperature=None, n_predict=1000,
                            top_p=None, top_k=None, tools=None):
            seen["tools"] = tools
            seen["n_messages"] = len(messages)
            return {"role": "assistant", "content": "NO_UPDATE"}

        monkeypatch.setattr(brain, "_execute_llm_completion", fake_completion)
        brain.evaluate_and_memorize("TEST")
        assert seen["tools"] == brain._MEMORIZE_ONLY_TOOLS
        assert len(seen["tools"]) == 1


# ── ID 3: the Laya pre-filter ─────────────────────────────────────────────────

class TestSessionPreFilter:
    def test_kernel_disabled_runs_the_pass(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        run, why = brain._session_may_hold_new_facts(["hello there"])
        assert run is True and "kernel disabled" in why

    def test_empty_digest_runs_the_pass(self):
        assert brain._session_may_hold_new_facts(["", "   "])[0] is True

    def test_confident_no_skips(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.9})
        run, why = brain._session_may_hold_new_facts(["hello there"])
        assert run is False and "confident no" in why

    def test_confident_yes_runs(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.9})
        assert brain._session_may_hold_new_facts(["I have two cats"])[0] is True

    def test_escalation_runs_the_pass(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": True,
                                             "reason": "low or unavailable margin"})
        run, why = brain._session_may_hold_new_facts(["maybe something?"])
        assert run is True and "escalated" in why

    def test_kernel_failure_runs_the_pass(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        run, why = brain._session_may_hold_new_facts(["hello"])
        assert run is True and "kernel failure" in why


# ── evaluate_and_memorize integration ─────────────────────────────────────────

class TestEvaluateAndMemorize:
    def test_skip_avoids_the_expensive_call(self, monkeypatch):
        monkeypatch.setattr(brain, "_session_may_hold_new_facts", lambda turns: (False, "confident no"))
        called = []
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: called.append(1) or {})
        brain.evaluate_and_memorize("AUTO N-TURN CONSOLIDATION")
        assert called == []

    def test_force_bypasses_the_filter(self, monkeypatch):
        def must_not_run(turns):
            raise AssertionError("pre-filter must not run when force=True")

        monkeypatch.setattr(brain, "_session_may_hold_new_facts", must_not_run)
        called = []
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: called.append(1) or
                            {"role": "assistant", "content": "NO_UPDATE"})
        brain.evaluate_and_memorize("MANUAL USER REQUEST", force=True)
        assert called == [1]

    def test_facts_are_memorized_from_the_response(self, monkeypatch):
        monkeypatch.setattr(brain, "_session_may_hold_new_facts", lambda turns: (True, "test"))
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: {"role": "assistant", "content": "",
                                         "tool_calls": [
                                             {"id": "1", "function": {
                                                 "name": "memorize_fact",
                                                 "arguments": '{"fact": "Mohamed has two cats"}'}},
                                             {"id": "2", "function": {
                                                 "name": "play_spotify_track",
                                                 "arguments": "{}"}},
                                         ]})
        saved = []
        monkeypatch.setattr(brain, "execute_tool",
                            lambda name, args: saved.append((name, args.get("fact"))))
        brain.evaluate_and_memorize("TEST")
        assert saved == [("memorize_fact", "Mohamed has two cats")]

    def test_memory_unavailable_returns_early(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_AVAILABLE", False, raising=False)
        called = []
        monkeypatch.setattr(brain, "_execute_llm_completion", lambda **k: called.append(1) or {})
        brain.evaluate_and_memorize("TEST")
        assert called == []
