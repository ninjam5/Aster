"""Tests for the Phase 3 selection gates.

  ID 16 — which of a friend's staged facts to inject (tools/memory_manager).
  ID 12 — reranking recalled memory candidates (core/memory).

Both are CONSERVATIVE: any doubt (kernel off, failure, nothing clears the bar) returns
the original behaviour unchanged. No model, no ChromaDB, no network.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.memory as memory
import core.system1 as system1
import tools.memory_manager as mm


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


# ── ID 16: friend-fact selection ──────────────────────────────────────────────

class TestFactSelection:
    FACTS = ["farah plays guitar", "farah's favourite colour is blue",
             "farah has a cat named Mimi", "farah is studying law"]

    def _scores(self, monkeypatch, normalized, confidence=None):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "score_candidates",
                            lambda *a, **k: {"normalized": normalized,
                                             "answer_confidence": confidence or {},
                                             "escalate": False})

    def test_kernel_off_keeps_everything(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert mm._laya_select_facts(self.FACTS, "what should I get her?") == self.FACTS

    def test_no_query_keeps_everything(self):
        assert mm._laya_select_facts(self.FACTS, "") == self.FACTS

    def test_keeps_only_relevant(self, monkeypatch):
        self._scores(monkeypatch, {"f0": 0.0, "f1": 0.0, "f2": 1.0, "f3": 0.0})
        kept = mm._laya_select_facts(self.FACTS, "how is Mimi?")
        assert kept == ["farah has a cat named Mimi"]

    def test_keeps_vaguely_related_and_above(self, monkeypatch):
        self._scores(monkeypatch, {"f0": 0.34, "f1": 0.1, "f2": 0.67, "f3": 0.0})
        assert mm._laya_select_facts(self.FACTS, "music?") == ["farah plays guitar",
                                                              "farah has a cat named Mimi"]

    def test_nothing_clears_the_bar_keeps_everything(self, monkeypatch):
        self._scores(monkeypatch, {"f0": 0.0, "f1": 0.0, "f2": 0.0, "f3": 0.0})
        assert mm._laya_select_facts(self.FACTS, "unrelated") == self.FACTS

    def test_noisy_scores_are_ignored(self, monkeypatch):
        self._scores(monkeypatch, {"f0": 1.0}, {"f0": 0.1})
        assert mm._laya_select_facts(self.FACTS, "q") == self.FACTS

    def test_failure_keeps_everything(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "score_candidates", boom)
        assert mm._laya_select_facts(self.FACTS, "q") == self.FACTS

    def test_get_user_facts_without_query_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(mm, "_load_memories",
                            lambda: {"farah": [{"fact": "a"}, {"fact": "b"}]})
        monkeypatch.setattr(mm, "_normalize_records", lambda r: r)
        assert mm.get_user_facts("farah") == "a; b"

    def test_get_user_facts_with_query_selects(self, monkeypatch):
        monkeypatch.setattr(mm, "_load_memories",
                            lambda: {"farah": [{"fact": "a"}, {"fact": "b"}]})
        monkeypatch.setattr(mm, "_normalize_records", lambda r: r)
        monkeypatch.setattr(mm, "_laya_select_facts", lambda facts, q: ["b"])
        assert mm.get_user_facts("farah", query="tell me about b") == "b"


# ── ID 12: recall rerank ──────────────────────────────────────────────────────

class TestRecallRerank:
    CANDS = ["Aster sent an email reply to X", "Aster sent an email reply to Y",
             "Mohamed's favourite pet is a parrot", "Mohamed lives in Alexandria"]

    def test_short_list_is_untouched(self):
        assert memory._laya_rerank("q", ["a", "b"], 3) == ["a", "b"]

    def test_kernel_off_keeps_the_original_order(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert memory._laya_rerank("q", self.CANDS, 3) == self.CANDS[:3]

    def test_reranks_by_score(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "score_candidates",
                            lambda *a, **k: {"normalized": {"c0": 0.0, "c1": 0.0,
                                                            "c2": 1.0, "c3": 0.5},
                                             "escalate": False})
        out = memory._laya_rerank("what pet does he have?", self.CANDS, 3)
        assert out[0] == "Mohamed's favourite pet is a parrot"
        assert len(out) == 3

    def test_missing_scores_keep_the_original_order(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "score_candidates",
                            lambda *a, **k: {"normalized": {}, "escalate": False})
        assert memory._laya_rerank("q", self.CANDS, 3) == self.CANDS[:3]

    def test_failure_keeps_the_original_order(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "score_candidates", boom)
        assert memory._laya_rerank("q", self.CANDS, 3) == self.CANDS[:3]
