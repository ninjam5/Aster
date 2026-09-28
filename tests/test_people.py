"""Tests for the structured people record + the pronoun flow (ID 13).

Before this, contacts were only a name->Discord-ID map plus a flat
`config.DISCORD_FEMALE_NAMES` set — there was no machine-readable place for pronouns,
so Discord Aster called everyone "Sir". No model, no network: Laya is mocked.
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.system1 as system1
import tools.people as people


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(people, "_PATH", str(tmp_path / "people.json"), raising=False)
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    yield


# ── the store ─────────────────────────────────────────────────────────────────

class TestStore:
    def test_missing_file_reads_empty(self):
        assert people.get_person("nobody") == {}

    def test_set_then_get_roundtrip(self):
        people.set_person("farah", pronoun="she", gender="female")
        rec = people.get_person("farah")
        assert rec["pronoun"] == "she" and rec["gender"] == "female"
        assert rec["updated"]

    def test_case_insensitive_lookup_and_merge(self):
        people.set_person("Adham", pronoun="he")
        assert people.get_person("adham")["pronoun"] == "he"
        people.set_person("adham", relationship="friend")
        rec = people.get_person("Adham")
        assert rec["pronoun"] == "he" and rec["relationship"] == "friend"

    def test_persists_to_disk(self):
        people.set_person("george", pronoun="he")
        with open(people._PATH, encoding="utf-8") as f:
            assert json.load(f)["george"]["pronoun"] == "he"

    def test_corrupt_file_is_safe(self):
        with open(people._PATH, "w", encoding="utf-8") as f:
            f.write("{not json")
        assert people.get_person("x") == {}

    def test_empty_name_is_safe(self):
        assert people.get_person("") == {}
        assert people.set_person("", pronoun="he") == {}


class TestNormalizePronoun:
    def test_aliases(self):
        for raw, want in [("she/her", "she"), ("her", "she"), ("f", "she"), ("male", "he"),
                          ("they/them", "they"), ("unsure", "unknown")]:
            assert people.normalize_pronoun(raw) == want, raw

    def test_unknown_value(self):
        assert people.normalize_pronoun("banana") == ""


class TestHonorificFor:
    def test_stored_pronoun_wins(self):
        people.set_person("farah", pronoun="she")
        assert people.honorific_for("farah") == "Ma'am"
        people.set_person("george", pronoun="he")
        assert people.honorific_for("george") == "Sir"

    def test_explicit_honorific_beats_pronoun(self):
        people.set_person("x", pronoun="he", honorific="Mx.")
        assert people.honorific_for("x") == "Mx."

    def test_they_has_no_honorific_so_falls_through(self):
        people.set_person("sam", pronoun="they")
        assert people.honorific_for("sam") == "Sir"  # plain respectful default

    def test_config_fallback_still_works(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_FEMALE_NAMES", {"emily"}, raising=False)
        assert people.honorific_for("emily") == "Ma'am"

    def test_unknown_person_defaults_to_sir(self):
        assert people.honorific_for("stranger") == "Sir"


# ── the Laya gender guess ─────────────────────────────────────────────────────

class TestLayaGuessGender:
    def test_kernel_off_is_unknown(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        g = people.laya_guess_gender("farah")
        assert g["gender"] == "unknown" and g["escalate"] is True

    def test_picks_female(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        assert people.laya_guess_gender("farah")["gender"] == "female"

    def test_picks_male(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.8})
        assert people.laya_guess_gender("george")["gender"] == "male"

    def test_cannot_tell(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "C", "escalate": False, "margin": 0.8})
        assert people.laya_guess_gender("masky")["gender"] == "unknown"

    def test_escalation_is_unknown(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": True})
        assert people.laya_guess_gender("farah")["gender"] == "unknown"

    def test_kernel_failure_is_unknown(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        assert people.laya_guess_gender("farah")["gender"] == "unknown"


# ── resolve_identity (the flow the owner described) ───────────────────────────

class TestResolveIdentity:
    def test_stored_pronoun_needs_no_ask(self):
        people.set_person("farah", pronoun="she")
        r = people.resolve_identity("farah")
        assert r["pronoun"] == "she" and r["needs_ask"] is False
        assert r["honorific"] == "Ma'am"

    def test_laya_guess_is_saved_and_used(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        r = people.resolve_identity("farah")
        assert r["pronoun"] == "she" and r["needs_ask"] is False
        assert people.get_person("farah")["source"] == "laya"

    def test_unknown_asks_once_then_stops(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "C", "escalate": False, "margin": 0.8})
        first = people.resolve_identity("masky")
        assert first["needs_ask"] is True
        second = people.resolve_identity("masky")
        assert second["needs_ask"] is False  # never nags


# ── brain wiring ──────────────────────────────────────────────────────────────

class TestBrainWiring:
    def test_honorific_uses_the_store(self, monkeypatch):
        people.set_person("farah", pronoun="she")
        assert brain._discord_honorific("farah") == "Ma'am"

    def test_honorific_survives_a_broken_store(self, monkeypatch):
        monkeypatch.setattr(people, "honorific_for",
                            MagicMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(config, "DISCORD_FEMALE_NAMES", {"emily"}, raising=False)
        assert brain._discord_honorific("emily") == "Ma'am"
        assert brain._discord_honorific("george") == "Sir"

    def test_remember_pronoun_tool_stores(self):
        out = brain.execute_tool("remember_pronoun", {"name": "farah", "pronoun": "she/her"})
        assert "saved" in out.lower()
        assert people.get_person("farah")["pronoun"] == "she"

    def test_remember_pronoun_rejects_a_non_pronoun(self):
        out = brain.execute_tool("remember_pronoun", {"name": "x", "pronoun": "banana"})
        assert out.startswith("FAILED")
        assert people.get_person("x") == {}

    def test_remember_pronoun_needs_both_args(self):
        assert brain.execute_tool("remember_pronoun", {"name": "x"}).startswith("FAILED")

    def test_tool_is_registered_in_both_toolsets(self):
        assert "remember_pronoun" in [t["function"]["name"] for t in brain.ADMIN_TOOLS]
        assert "remember_pronoun" in [t["function"]["name"] for t in brain.DISCORD_TOOLS]


# ── ID 14: relationship classification ────────────────────────────────────────

class TestRelationship:
    def test_kernel_off_is_unknown(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert people.laya_guess_relationship("george")["relationship"] == "unknown"

    def test_picks_friend(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": False, "margin": 0.8})
        assert people.laya_guess_relationship("george")["relationship"] == "friend"

    def test_picks_family(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        assert people.laya_guess_relationship("mom")["relationship"] == "family"

    def test_cannot_tell(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "F", "escalate": False, "margin": 0.8})
        assert people.laya_guess_relationship("masky")["relationship"] == "unknown"

    def test_escalation_is_unknown(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": True})
        assert people.laya_guess_relationship("x")["relationship"] == "unknown"

    def test_stored_relationship_wins(self):
        people.set_person("george", relationship="colleague")
        assert people.resolve_relationship("george") == "colleague"

    def test_guess_is_saved(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False, "margin": 0.8})
        assert people.resolve_relationship("mom") == "family"
        assert people.get_person("mom")["relationship"] == "family"

    def test_defaults_to_friend(self, monkeypatch):
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "F", "escalate": False, "margin": 0.8})
        assert people.resolve_relationship("masky") == "friend"

    def test_brain_helper_survives_a_broken_store(self, monkeypatch):
        monkeypatch.setattr(people, "resolve_relationship",
                            MagicMock(side_effect=RuntimeError("boom")))
        assert brain._discord_relationship("george") == "friend"

    def test_brain_helper_uses_the_store(self):
        people.set_person("george", relationship="colleague")
        assert brain._discord_relationship("george") == "colleague"

    def test_prompt_formats_with_every_call_site_key(self):
        """A missing placeholder key would KeyError every Discord message."""
        out = brain.DISCORD_CHAT_SYSTEM_PROMPT.format(
            sender_name="george", known_facts="f", honorific="Sir",
            owner_name="Mohamed", tool_docs="", relationship="colleague")
        assert "Mohamed's colleague, george" in out
