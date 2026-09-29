"""Tests for contact routing (ID 15 of laya-integration.md — Parts A/B/C).

Part A — case-insensitive contact resolution (the live 'Adham' bug).
Part B — Laya picks the contact among the known names (candidates as criteria).
Part C — a conservative Laya relay-intent gate for phrasings the verb regex misses.

No model, no Discord, no network: `system1` is mocked and requests are never reached.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.system1 as system1
import tools.discord_api as discord_api

FAKE_CONTACTS = {"george": "111", "farah": "222", "Adham": "333", "masky": "444"}


@pytest.fixture(autouse=True)
def _contacts(monkeypatch):
    monkeypatch.setattr(discord_api, "CONTACTS", dict(FAKE_CONTACTS), raising=False)
    # QA round 2: these tests must not depend on the owner's real secrets.yaml.
    monkeypatch.setattr(discord_api, "DISCORD_BOT_TOKEN", "test-token", raising=False)


# ── Part A: case-insensitive resolution ───────────────────────────────────────

class TestResolveContact:
    def test_exact(self):
        assert discord_api.resolve_contact("george") == ("george", "111")

    def test_capitalised_key_is_reachable(self):
        """The live bug: CONTACTS has 'Adham' but every lookup lowercased first."""
        for probe in ["Adham", "adham", "ADHAM", " Adham "]:
            assert discord_api.resolve_contact(probe) == ("Adham", "333"), probe

    def test_unknown(self):
        assert discord_api.resolve_contact("nobody") is None
        assert discord_api.resolve_contact("") is None
        assert discord_api.resolve_contact(None) is None

    def test_contact_names_preserves_case(self):
        assert "Adham" in discord_api.contact_names()


# ── Part B: the Laya pick ─────────────────────────────────────────────────────

class TestLayaPickContact:
    def test_a_bare_name_is_exact_and_needs_no_model(self, monkeypatch):
        called = []
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        pick = discord_api.laya_pick_contact("Adham")
        assert pick["name"] == "Adham" and pick["exact"] is True
        assert called == []

    def test_a_name_inside_a_sentence_is_NOT_exact(self, monkeypatch):
        """QA 2026-09-28: a whole-text substring check made any name anywhere in the
        request 'exact' — so 'tell my brother to say hi to Farah' skipped the confirm
        gate and DMed farah. A name inside a sentence is now a guess."""
        called = []
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        pick = discord_api.laya_pick_contact("tell my brother to say hi to farah")
        assert pick["name"] == "farah" and pick["exact"] is False
        assert called == []   # no model call either way

    def test_a_name_that_is_a_substring_of_another_does_not_match(self, monkeypatch):
        monkeypatch.setattr(discord_api, "CONTACTS",
                            {"ann": "1", "anna": "2"}, raising=False)
        pick = discord_api.laya_pick_contact("text anna saying hi")
        assert pick["name"] == "anna"

    def test_nickname_is_a_laya_pick_and_not_exact(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.7})
        pick = discord_api.laya_pick_contact("tell my brother I'll be late")
        assert pick["name"] == "farah" and pick["exact"] is False

    def test_none_key_returns_no_contact(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "Z", "escalate": False})
        assert discord_api.laya_pick_contact("tell the plumber")["name"] is None

    def test_escalation_returns_no_contact(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": True})
        assert discord_api.laya_pick_contact("tell someone")["name"] is None

    def test_kernel_off_returns_no_contact(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert discord_api.laya_pick_contact("tell my brother")["name"] is None

    def test_kernel_failure_returns_no_contact(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        assert discord_api.laya_pick_contact("tell my brother")["name"] is None


# ── the confirm gate in send_discord_message ──────────────────────────────────

class TestSendConfirmGate:
    def test_exact_name_skips_the_gate_and_attempts_send(self, monkeypatch):
        """Exact -> straight to the HTTP path (no confirm note)."""
        monkeypatch.setattr(discord_api.requests, "post",
                            MagicMock(side_effect=discord_api.requests.RequestException("no network")))
        out = discord_api.send_discord_message("Adham", "hello")
        assert "CONFIRM REQUIRED" not in out
        assert "Failed to open Discord DM channel" in out

    def test_fuzzy_target_refuses_and_asks_to_confirm(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.7})
        posted = []
        monkeypatch.setattr(discord_api.requests, "post",
                            MagicMock(side_effect=lambda *a, **k: posted.append(1)))
        out = discord_api.send_discord_message("my brother", "hello")
        assert "CONFIRM REQUIRED" in out and out.startswith("FAILED")
        assert "george" in out          # the guess is named
        assert "confirm=true" in out
        assert posted == []             # nothing was sent

    def test_fuzzy_target_with_confirm_proceeds(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.7})
        monkeypatch.setattr(discord_api.requests, "post",
                            MagicMock(side_effect=discord_api.requests.RequestException("no network")))
        out = discord_api.send_discord_message("my brother", "hello", confirm=True)
        assert "CONFIRM REQUIRED" not in out
        assert "Failed to open Discord DM channel" in out   # it tried to send

    def test_unknown_target_reports_honestly(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "Z", "escalate": False})
        out = discord_api.send_discord_message("the plumber", "hello")
        assert out.startswith("Error: Unknown Discord contact")

    def test_empty_message_is_rejected(self):
        assert discord_api.send_discord_message("george", "  ").startswith("Error")


# ── Part B/C wiring in the brain ──────────────────────────────────────────────

class TestIntentParser:
    def test_exact_name(self):
        d = brain._extract_discord_message_intent("text george saying I will be late")
        assert d["target"] == "george" and d["exact"] is True
        assert "late" in d["payload"]

    def test_capitalised_name_keeps_its_case(self):
        d = brain._extract_discord_message_intent("text Adham saying hello")
        assert d["target"] == "Adham"

    def test_bad_tokenisation_falls_back_to_laya(self, monkeypatch):
        monkeypatch.setattr(brain, "laya_pick_contact",
                            lambda text: {"name": "farah", "exact": True, "escalate": False,
                                          "reason": "verbatim"})
        d = brain._extract_discord_message_intent("send a message to farah saying hi")
        assert d["target"] == "farah"
        assert d["payload"] == "send a message to farah saying hi"

    def test_not_a_relay(self):
        assert brain._extract_discord_message_intent("what time is it") is None
        assert brain._extract_discord_message_intent("tell me a joke") is None

    def test_part_c_recognises_a_phrasing_without_the_verb_pattern(self, monkeypatch):
        monkeypatch.setattr(brain, "_laya_relay_intent", lambda text: True)
        monkeypatch.setattr(brain, "laya_pick_contact",
                            lambda text: {"name": "george", "exact": True, "escalate": False,
                                          "reason": "verbatim"})
        d = brain._extract_discord_message_intent("let george know I'll be late")
        assert d["target"] == "george" and d["exact"] is True

    def test_is_request_true_for_a_relay(self):
        assert brain._is_discord_message_request("text george saying hi") is True


class TestRelayIntentGate:
    def test_cheap_prefilter_skips_the_model_for_ordinary_chat(self, monkeypatch):
        called = []
        monkeypatch.setattr(system1, "check_state", lambda *a, **k: called.append(1) or {})
        assert brain._laya_relay_intent("what is the capital of France") is False
        assert called == []

    def test_send_verb_triggers_the_gate(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: {"answer": True, "escalate": False, "margin": 0.7})
        assert brain._laya_relay_intent("let george know I'll be late") is True

    def test_doubt_returns_false(self, monkeypatch):
        # QA round 6: needs a contact name, or the round-5 prefilter returns before
        # check_state and the escalate branch is never exercised.
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        called = []
        monkeypatch.setattr(system1, "check_state",
                            lambda *a, **k: called.append(1) or {"answer": True,
                                                                 "escalate": True})
        assert brain._laya_relay_intent("message george about the thing") is False
        assert called == [1]   # the escalate branch really was consulted

    def test_a_verb_without_a_contact_costs_no_model_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(system1, "check_state", lambda *a, **k: called.append(1) or {})
        assert brain._laya_relay_intent("I'll text you later") is False
        assert called == []

    def test_kernel_off_returns_false(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert brain._laya_relay_intent("text george hi") is False


# ── deterministic typo resolution (difflib, no model) ─────────────────────────

class TestFuzzyTokenMatch:
    def test_resolves_misspellings(self):
        assert discord_api._fuzzy_token_match("tell geroge I will be late") == "george"
        assert discord_api._fuzzy_token_match("message farrah saying hi") == "farah"
        assert discord_api._fuzzy_token_match("ping maski") == "masky"

    def test_returns_none_for_non_names(self):
        for text in ["tell my brother I'll be late", "message the plumber about the sink",
                     "what time is it", "text a"]:
            assert discord_api._fuzzy_token_match(text) is None, text

    def test_capitalised_key_resolves(self):
        assert discord_api._fuzzy_token_match("tell Adhm hello") == "Adham"

    def test_typo_pick_needs_no_model_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(system1, "choose", lambda *a, **k: called.append(1) or {})
        pick = discord_api.laya_pick_contact("tell geroge I will be late")
        assert pick["name"] == "george" and pick["exact"] is False
        assert called == []  # difflib handled it; no Laya call


# ── typo in the ADDRESSEE position is confident (owner request 2026-09-29) ─────
#
# The owner: "I clearly meant george, so he should have sent a message to george …
# I don't need to use the same exact name." A misspelling of a contact in the address
# slot ("tell geroge I'm late") now sends without a confirmation round-trip — but a
# typo in the MESSAGE BODY stays a guess, so a name mentioned in the payload can
# never misdirect the send.

class TestTypoInAddressee:
    def test_resolve_typo(self):
        assert discord_api.resolve_typo("geroge") == "george"
        assert discord_api.resolve_typo("farrah") == "farah"
        assert discord_api.resolve_typo("brother") is None
        assert discord_api.resolve_typo("my brother") is None

    def test_a_typo_in_the_address_position_is_exact(self):
        intent = brain._extract_discord_message_intent("tell geroge I'll be late")
        assert intent is not None
        assert intent["target"] == "george"
        assert intent["exact"] is True   # may send without confirmation

    def test_an_exact_name_is_unchanged(self):
        intent = brain._extract_discord_message_intent("tell george I'll be late")
        assert intent["target"] == "george" and intent["exact"] is True

    def test_a_typo_in_the_payload_stays_a_guess(self):
        """'tell my brother to say hi to geroge' — the typo is in the BODY, not the
        address, so it must NOT be treated as a confident send."""
        intent = brain._extract_discord_message_intent("tell my brother to say hi to geroge")
        assert not (intent and intent.get("exact")), intent

    def test_a_target_typo_sends_without_a_confirmation(self, monkeypatch):
        posts = []

        class _Resp:
            status_code = 200
            text = ""
            def json(self):
                return {"id": "chan-1"}

        def fake_post(url, headers=None, json=None, timeout=None):
            posts.append(url)
            return _Resp()

        monkeypatch.setattr(discord_api.requests, "post", fake_post)
        out = discord_api.send_discord_message("geroge", "I'll be late", confirm=False)
        assert "delivered" in out.lower(), out
        assert any("/messages" in u for u in posts)

    def test_an_unknown_non_typo_target_is_still_refused(self, monkeypatch):
        monkeypatch.setattr(
            discord_api.requests, "post",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not send")))
        out = discord_api.send_discord_message("brother", "hi", confirm=False)
        assert "Unknown Discord contact" in out
