"""Regression tests for the reliability detectors in core/brain.py.

Fixtures are the VERBATIM live turns of 2026-09-28 (Aster_Vault/Conversations +
reliability_log.jsonl): the model used the research tool, got stale/irrelevant
Wikipedia content, then denied having web search, then refused on a knowledge
cutoff — and all three detectors missed every one of them.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.brain import _claims_knowledge_cutoff, _claims_tool_execution, _denies_capability

# ── verbatim live text (11:53:58) ─────────────────────────────────────────────
T1 = ("The data I have doesn't go quite that far ahead, Sir. My research turned up information on ICC "
      "warrants and related geopolitical events through mid-2025, but nothing specific about Netanyahu's "
      "UN attendance for September 2026. Shall I try a live web search to track down the latest on this?")

# ── verbatim live text (12:32:44) ─────────────────────────────────────────────
T2 = ("I'm afraid I cannot directly execute a web search. I can only access information from my training "
      "data. Would you like me to look up something specific from what I know?")

# ── verbatim live text (12:33:32) ─────────────────────────────────────────────
T3 = ("I cannot provide that information as my knowledge is not current enough to cover events in 2026. "
      "You would need to consult a news source for that. Let me check if there's any information available "
      "through my knowledge base. I'll search for information about this meeting and the Prime Minister of "
      "Israel's attendance. I cannot provide that information as my knowledge is not current enough to "
      "cover events in 2026.")

USER_Q = "did the prime minester of israel attend the United nations meeting on september 27th 2026?"
USER_YES = "yes try a live web search"


class TestCutoffDetector:
    def test_catches_the_live_refusal(self):
        assert _claims_knowledge_cutoff(T3) is True

    def test_catches_the_stale_report(self):
        assert _claims_knowledge_cutoff(T1) is True

    def test_phrasings_that_previously_slipped_through(self):
        for text in [
            "I cannot provide that information as my knowledge is not current enough to cover events in 2026.",
            "My knowledge is not current enough to cover events in 2026.",
            "My training data is not current enough to cover 2026 events.",
            "I cannot provide that information.",
            "I cannot verify that information.",
            "My knowledge only goes up to 2024.",
            "The data I have doesn't go quite that far ahead.",
        ]:
            assert _claims_knowledge_cutoff(text) is True, text

    def test_does_not_fire_on_a_real_answer(self):
        assert _claims_knowledge_cutoff(
            "Yes — Netanyahu addressed the UN General Assembly's 81st session on 27 September 2026.") is False
        assert _claims_knowledge_cutoff("It is 07:55, Sir.") is False


class TestCapabilityDenialDetector:
    def test_catches_the_live_denial(self):
        assert _denies_capability(T2) is True

    def test_phrasings(self):
        for text in [
            "I cannot directly execute a web search.",
            "I can only access information from my training data.",
            "I don't have access to the internet.",
            "I'm afraid I cannot access the web.",
            "I do not have the ability to browse the internet.",
        ]:
            assert _denies_capability(text) is True, text

    def test_does_not_fire_on_a_real_answer(self):
        assert _denies_capability(
            "Yes — Netanyahu addressed the UN General Assembly's 81st session on 27 September 2026.") is False
        assert _denies_capability("You're on OpenCode Go, working on a project.") is False


class TestResearchNarrationDetector:
    def test_catches_narrated_search_without_a_call(self):
        assert _claims_tool_execution(USER_Q, T3) is True
        assert _claims_tool_execution(USER_Q, T1) is True

    def test_direct_phrasing(self):
        assert _claims_tool_execution(USER_Q, "I'll search for information about this meeting.") is True
        assert _claims_tool_execution(USER_Q, "Let me look this up.") is True
        assert _claims_tool_execution(USER_Q, "My research turned up nothing specific.") is True

    def test_does_not_fire_when_a_tool_was_called(self):
        msg = {"tool_calls": [{"function": {"name": "research", "arguments": "{}"}}]}
        assert _claims_tool_execution(USER_Q, "I'll search for that.", message=msg) is False

    def test_does_not_fire_on_plain_chit_chat(self):
        assert _claims_tool_execution("tell me a joke",
                                      "You still Google things by hand, Sir.") is False
        assert _claims_tool_execution("hello there", "Hi.") is False
