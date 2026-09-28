"""Tests pinning the fixes from the 5-agent QA review (2026-09-28).

Each one corresponds to a finding that would otherwise regress silently:
  - the send gate was inheriting the loosest threshold in the codebase
  - two score consumers ignored `escalate` and could drop a memory/fact
  - a gate SKIP was marked "synced" in the Discord staging store (permanent loss)
  - the junk filter was owner-scoped, so a friend's fact could be dropped
  - the Laya Wikipedia pick bypassed the lexical relevance gate (the Renzi class)
  - a `choice` disagreeing with its own distribution could pass the gate
  - Sentry checked the owner by bare substring BEFORE KNOWN:, hiding a known visitor
No model, no network, no camera.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.memory as memory
import core.system1 as system1
import tools.dom as dom
import tools.memory_manager as mm
import tools.rag as rag
import tools.sentry as sentry


@pytest.fixture(autouse=True)
def _no_logging(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)


# ── the send gate must use a strict threshold ─────────────────────────────────

class TestSendGateThreshold:
    def test_laya_send_like_passes_a_strict_min_margin(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def fake_check_state(*a, **k):
            seen.update(k)
            return {"answer": False, "escalate": False}

        monkeypatch.setattr(system1, "check_state", fake_check_state)
        dom.is_send_like({"role": "button", "name": "Continue", "text": ""})
        assert seen.get("min_margin", 0) >= 0.5


# ── score consumers must honour escalate ──────────────────────────────────────

class TestScoreConsumersHonourEscalate:
    CANDS = ["a", "b", "c", "d"]

    def test_rerank_keeps_the_original_order_on_escalate(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "score_candidates",
                            lambda *a, **k: {"normalized": {"c0": 0.0, "c1": 1.0,
                                                            "c2": 0.0, "c3": 0.0},
                                             "escalate": True})
        assert memory._laya_rerank("q", self.CANDS, 3) == self.CANDS[:3]

    def test_fact_selection_keeps_everything_on_escalate(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "score_candidates",
                            lambda *a, **k: {"normalized": {"f0": 0.0, "f1": 0.0},
                                             "answer_confidence": {}, "escalate": True})
        assert mm._laya_select_facts(["x", "y"], "q") == ["x", "y"]


# ── a gate SKIP must not be recorded as synced ────────────────────────────────

class TestSyncSkipIsNotSuccess:
    def test_a_skipped_fact_stays_unsynced(self, monkeypatch):
        payload = {"farah": [{"fact": "farah plays guitar", "synced": False}]}
        monkeypatch.setattr(mm, "_load_memories", lambda: payload)
        monkeypatch.setattr(mm, "_save_memories", lambda p: None)
        monkeypatch.setattr(mm, "_normalize_records", lambda r: r)

        out = mm.sync_unsynced_facts(
            lambda fact: "[System Note: NOT saved — already saved. The information is already covered.]")
        assert "Failures: 1" in out
        assert payload["farah"][0].get("synced") is False   # still retryable
        assert "sync_error" in payload["farah"][0]

    def test_a_real_write_is_marked_synced(self, monkeypatch):
        payload = {"farah": [{"fact": "farah plays guitar", "synced": False}]}
        monkeypatch.setattr(mm, "_load_memories", lambda: payload)
        monkeypatch.setattr(mm, "_save_memories", lambda p: None)
        monkeypatch.setattr(mm, "_normalize_records", lambda r: r)
        out = mm.sync_unsynced_facts(lambda fact: "Successfully committed to Vault and Neural DB")
        assert "Absorbed: 1" in out
        assert payload["farah"][0]["synced"] is True


# ── the junk question must be scoped by source ────────────────────────────────

class TestJunkQuestionScope:
    def _capture(self, monkeypatch, source):
        seen = {}
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def fake_check_state(question, **k):
            seen["question"] = question
            return {"answer": True, "escalate": True}   # write

        # Also stub the candidate-pick calls, or they reach the real kernel and load the
        # model (and read the real vault) from a unit test.
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": None, "escalate": True})
        monkeypatch.setattr(memory, "_similar_existing_facts", lambda fact, k=6: [])
        monkeypatch.setattr(system1, "check_state", fake_check_state)
        memory._gate_fact("User farah stated: farah plays guitar", source=source)
        return seen["question"]

    def test_friend_sourced_fact_is_not_owner_scoped(self, monkeypatch):
        q = self._capture(monkeypatch, "discord_sync")
        assert "about the owner" not in q
        assert "about a person" in q

    def test_agent_sourced_fact_is_owner_scoped(self, monkeypatch):
        q = self._capture(monkeypatch, "agent")
        assert "about the owner" in q


# ── the Laya Wikipedia pick must pass the lexical relevance gate ──────────────

class TestWikipediaLayaPickGate:
    def test_an_irrelevant_laya_pick_is_rejected(self, monkeypatch):
        pick = MagicMock(return_value="Matteo Renzi")
        monkeypatch.setattr(rag, "_laya_pick_article", pick)

        class _Page:
            title = "Matteo Renzi"
            content = "x" * 5000

        monkeypatch.setattr(rag.wikipedia, "page", lambda *a, **k: _Page())
        monkeypatch.setattr(rag.wikipedia, "search", lambda *a, **k: ["Matteo Renzi"])
        out = rag._wikipedia_lookup("Israeli Prime Minister Netanyahu United Nations 2026")
        pick.assert_called()          # QA round 2: the mock was never asserted
        assert out is None            # the lexical gate rejects it -> fall through


# ── a choice that disagrees with its distribution must not pass ───────────────

class TestChoiceMustMatchArgmax:
    def test_disagreeing_choice_yields_a_negative_margin(self):
        answer = {"choice": "B", "probabilities": {"A": 0.9, "B": 0.05, "C": 0.05}}
        choice, margin, dist = system1._extract_choice(answer, {"A", "B", "C"})
        assert choice == "B" and margin is not None and margin < 0

    def test_agreeing_choice_keeps_a_positive_margin(self):
        answer = {"choice": "A", "probabilities": {"A": 0.9, "B": 0.05, "C": 0.05}}
        _, margin, _ = system1._extract_choice(answer, {"A", "B", "C"})
        assert margin == pytest.approx(0.85)


# ── Sentry: KNOWN must be checked before the owner substring ──────────────────

class TestSentryOwnerVsKnown:
    def test_a_known_name_containing_the_owner_name_still_alerts(self, monkeypatch):
        monkeypatch.setattr(config, "OWNER_NAME", "Dan", raising=False)
        monkeypatch.setattr(sentry, "SENTRY_ACTIVE", True, raising=False)
        monkeypatch.setattr(sentry, "capture_frame_base64", lambda: "b64", raising=False)
        monkeypatch.setattr(sentry, "_classify_frame_from_faces", lambda b64: "KNOWN:DANA",
                            raising=False)
        sent = []
        bot = MagicMock()
        bot.send_message.side_effect = lambda chat, msg: sent.append(msg)
        monkeypatch.setattr(sentry, "telegram_bot", bot, raising=False)
        monkeypatch.setattr(sentry, "chat_id", 1, raising=False)
        sentry.notification_cooldowns.clear()
        sentry.execute_sentry_sweep()
        assert sent and "Dana" in sent[0]

    def test_the_owner_still_suppresses_the_alert(self, monkeypatch):
        monkeypatch.setattr(config, "OWNER_NAME", "Dan", raising=False)
        monkeypatch.setattr(sentry, "capture_frame_base64", lambda: "b64", raising=False)
        monkeypatch.setattr(sentry, "_classify_frame_from_faces", lambda b64: "DAN",
                            raising=False)
        bot = MagicMock()
        monkeypatch.setattr(sentry, "telegram_bot", bot, raising=False)
        monkeypatch.setattr(sentry, "chat_id", 1, raising=False)
        sentry.execute_sentry_sweep()
        bot.send_message.assert_not_called()
        # positive control: UNKNOWN in the SAME shape DOES send, so this test would
        # catch the owner branch being deleted (QA round 2).
        sentry._classify_frame_from_faces = lambda b64: "UNKNOWN"
        sentry.execute_sentry_sweep()
        assert bot.send_message.called or sentry.WAITING_FOR_ID is True
