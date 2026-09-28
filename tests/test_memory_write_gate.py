"""Tests for the Phase 2 Laya memory-write gate (laya-integration.md IDs 9/10/11).

The gate lives in `core/memory.memorize_fact` — the single choke point every writer
goes through (the tool, the Discord sync, Gmail, the face server, vision,
consolidation). It can SKIP a write (confident duplicate or junk) and report a
detected conflict. It must never drop a fact on a weak signal.

No model, no ChromaDB, no network: `_gate_fact` is driven by mocked Laya answers.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.memory as memory
import core.system1 as system1


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A throwaway vault + a fake Chroma collection + no real reliability logging."""
    monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(memory, "MD_FILE", str(tmp_path / "memory.md"), raising=False)
    monkeypatch.setattr(memory, "memory_collection", MagicMock(), raising=False)
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    yield


def _mock_gate(monkeypatch, *, junk=None, dup=None, repl=None, contra=None, kernel=True):
    """Mock the two shapes the gate uses: check_state (junk) + choose (candidate picks).

    The candidate-pick questions are keyed by `key`: "duplicate" / "replaces" /
    "contradicts". Anything unspecified returns the "none" key.
    """
    monkeypatch.setattr(system1, "kernel_enabled", lambda: kernel)
    monkeypatch.setattr(system1, "check_state",
                        lambda *a, **k: junk if junk is not None
                        else {"answer": True, "escalate": True})
    table = {"duplicate": dup, "replaces": repl, "contradicts": contra}

    def fake_choose(question, criteria, key="choice", state=None, kind="choice",
                    min_margin=None):
        return table.get(key) or {"choice": "Z", "escalate": False}

    monkeypatch.setattr(system1, "choose", fake_choose)


def _with_vault(tmp_path, text):
    p = tmp_path / "memory.md"
    p.write_text(text, encoding="utf-8")
    memory.MD_FILE = str(p)


# ── the gate ──────────────────────────────────────────────────────────────────

class TestGateFact:
    def test_kernel_off_writes(self, monkeypatch):
        _mock_gate(monkeypatch, kernel=False)
        assert memory._gate_fact("Mohamed has two cats")["skip"] is False

    def test_confident_junk_is_skipped(self, monkeypatch):
        _mock_gate(monkeypatch, junk={"answer": False, "escalate": False, "margin": 0.55})
        d = memory._gate_fact("Aster sent an email reply to X")
        assert d["skip"] is True and d["category"] == "junk"

    def test_junk_escalation_writes(self, monkeypatch):
        _mock_gate(monkeypatch, junk={"answer": False, "escalate": True, "margin": 0.2})
        assert memory._gate_fact("borderline item")["skip"] is False

    def test_junk_answer_true_writes(self, monkeypatch):
        _mock_gate(monkeypatch, junk={"answer": True, "escalate": False, "margin": 0.6})
        assert memory._gate_fact("Mohamed has two cats")["skip"] is False

    def test_confident_duplicate_is_skipped(self, monkeypatch, tmp_path):
        _with_vault(tmp_path, "- **[2026-01-01 00:00:00]** Mohamed has two cats\n")
        _mock_gate(monkeypatch, dup={"choice": "A", "escalate": False, "margin": 0.72})
        d = memory._gate_fact("Mohamed has two cats")
        assert d["skip"] is True and "already saved" in d["reason"]

    def test_duplicate_escalation_writes(self, monkeypatch, tmp_path):
        _with_vault(tmp_path, "- **[2026-01-01 00:00:00]** Mohamed has cats\n")
        _mock_gate(monkeypatch, dup={"choice": "A", "escalate": True, "margin": 0.14})
        assert memory._gate_fact("Mohamed has two cats named A and B")["skip"] is False

    def test_none_pick_writes(self, monkeypatch, tmp_path):
        _with_vault(tmp_path, "- **[2026-01-01 00:00:00]** Mohamed has cats\n")
        _mock_gate(monkeypatch)
        assert memory._gate_fact("Mohamed's favourite language is Python")["skip"] is False

    def test_no_candidates_skips_the_pick_questions(self, monkeypatch, tmp_path):
        _with_vault(tmp_path, "")
        called = []
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": True, "escalate": False})
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: called.append(k.get("key")) or {"choice": "Z",
                                                                            "escalate": False})
        memory._gate_fact("a brand new fact")
        assert called == []

    def test_conflict_detection_is_not_attempted(self, monkeypatch, tmp_path):
        """ID 9 is deliberately OFF — measured inert (margins 0.00-0.28). Fence:
        the gate must not spend Laya calls on the replaces/contradicts questions."""
        _with_vault(tmp_path, "- **[2026-01-01 00:00:00]** Mohamed lives in Alexandria\n")
        asked = []
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": True, "escalate": False})
        monkeypatch.setattr(system1, "choose",
                            lambda question, criteria, key="choice", **k:
                            asked.append(key) or {"choice": "Z", "escalate": False})
        d = memory._gate_fact("Mohamed moved to Cairo")
        assert asked == ["duplicate"]
        assert d["conflict"] == "" and d["supersedes"] is None

    def test_exception_writes(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "check_state", boom)
        d = memory._gate_fact("Mohamed has two cats")
        assert d["skip"] is False and "gate error" in d["reason"]


# ── candidate retrieval ───────────────────────────────────────────────────────

class TestSimilarExistingFacts:
    def test_returns_most_similar_and_strips_the_prefix(self, tmp_path):
        p = tmp_path / "memory.md"
        p.write_text(
            "- **[2026-01-01 00:00:00]** Mohamed has two cats\n"
            "- **[2026-01-01 00:00:01]** Mohamed works on Aster\n"
            "- **[2026-01-01 00:00:02]** Mohamed has three cats\n",
            encoding="utf-8")
        memory.MD_FILE = str(p)
        out = memory._similar_existing_facts("Mohamed has two cats", 1)
        assert out == ["Mohamed has two cats"]

    def test_missing_file_is_safe(self, tmp_path):
        memory.MD_FILE = str(tmp_path / "nope.md")
        assert memory._similar_existing_facts("anything") == []
class TestMemorizeFactGate:
    def test_skip_does_not_write(self, monkeypatch):
        monkeypatch.setattr(memory, "_gate_fact",
                            lambda fact: {"skip": True, "reason": "exact duplicate — already saved",
                                          "category": "", "conflict": "", "supersedes": None})
        out = memory.memorize_fact("Mohamed has two cats")
        assert "NOT saved" in out
        memory.memory_collection.add.assert_not_called()
        assert not os.path.exists(memory.MD_FILE)

    def test_write_appends_and_indexes(self, monkeypatch):
        monkeypatch.setattr(memory, "_gate_fact",
                            lambda fact: {"skip": False, "reason": "", "category": "fact",
                                          "conflict": "", "supersedes": None})
        out = memory.memorize_fact("Mohamed has two cats")
        assert "Successfully committed" in out
        assert "Mohamed has two cats" in open(memory.MD_FILE, encoding="utf-8").read()
        memory.memory_collection.add.assert_called_once()

    def test_doc_ids_are_unique_within_the_same_second(self, monkeypatch):
        monkeypatch.setattr(memory, "_gate_fact",
                            lambda fact: {"skip": False, "reason": "", "category": "fact",
                                          "conflict": "", "supersedes": None})
        ids = []
        memory.memory_collection.add.side_effect = lambda documents, ids_=None, **k: ids.extend(
            k.get("ids") or [])
        memory.memorize_fact("fact one")
        memory.memorize_fact("fact two")
        assert len(set(ids)) == 2

    def test_supersede_note_is_returned(self, monkeypatch):
        monkeypatch.setattr(memory, "_gate_fact",
                            lambda fact: {"skip": False, "reason": "", "category": "fact",
                                          "conflict": "supersedes",
                                          "supersedes": "Mohamed lives in Alexandria"})
        out = memory.memorize_fact("Mohamed moved to Cairo")
        assert "SUPERSEDE" in out and "Alexandria" in out

    def test_contradiction_note_is_returned(self, monkeypatch):
        monkeypatch.setattr(memory, "_gate_fact",
                            lambda fact: {"skip": False, "reason": "", "category": "fact",
                                          "conflict": "contradicts",
                                          "supersedes": "Mohamed does not have any pets"})
        out = memory.memorize_fact("Mohamed's favorite pet is a parrot")
        assert "CONTRADICTS" in out

    def test_skip_gate_bypasses_the_gate(self, monkeypatch):
        called = []
        monkeypatch.setattr(memory, "_gate_fact", lambda fact: called.append(1) or
                            {"skip": True, "reason": "x", "category": "", "conflict": "",
                             "supersedes": None})
        out = memory.memorize_fact("forced fact", skip_gate=True)
        assert called == [] and "Successfully committed" in out

    def test_empty_fact_is_rejected(self):
        assert memory.memorize_fact("   ") == "Error: empty fact."
