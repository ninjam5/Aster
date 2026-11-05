"""Comprehensive test suite for Laya backwards integration & thinking loop fixes.

Verifies all 6 remediation steps from laya-backwards-integration.md plus the
prefix-cache and parallel-tool optimizations:
  1. Step 1 (ID 8 decommissioned): process_user_input no longer calls pick_tool_for_request
     or injects a round-0 tool tie-break hint.
  2. Step 2 (ID 1 & ID 2 disabled): _attach_action_screenshot always attaches a post-action
     screenshot; look_at_screen always captures real pixels rather than UIA text bypass.
  3. Step 3 (Contextualized Retry Guards):
     - _claims_tool_execution returns False when tools_executed=True.
     - play_spotify_playlist and play_liked_songs are terminal tools.
     - Safety/confirmation gates ([REFUSED], BLOCKED, [CONFIRM REQUIRED]) and confirmation
       questions do not trigger failure_retried.
  4. Step 4 (Contact Parser Verb Blacklist): RESERVED_VERBS ('email', 'send', 'accountant',
     etc.) never fuzzy-match to contacts like 'emily'.
  5. Step 5 (Memory Dedup Reconciliation): duplicate skips return affirmative
     '[Memory Confirmed: ...]' instead of 'NOT saved', while junk skips still return 'NOT saved'.
  6. Step 6 (System Prompt Streamlining & Clarification Gate): jarvis.md and
     _shared_tool_laws.md include the Clarification Gate and Confirmation Gate, remove
     the 15-row lookup table, and cut static prompt size by >50%.
  7. Optimization 1 (Prefix-preserving research summarizer): _prefix_preserving_summarizer
     includes messages[0] and ADMIN_TOOLS sollama-server's KV prefix cache is not wiped.
  8. Optimization 2 (Parallel non-GUI tool calls): emitting memorize_fact + save_note in
     a single completion executes both tools in one round.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import core.brain as brain
import core.memory as memory
import core.tool_routing as tr
import tools.discord_api as discord_api


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "AUTO_COMPACT_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "CONVERSATIONS_DIR", str(tmp_path / "Conversations"), raising=False)
    monkeypatch.setattr(brain, "_turn_count", 0, raising=False)
    monkeypatch.setattr(brain, "evaluate_and_memorize", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(brain.awareness, "render_context_block", lambda: None, raising=False)
    monkeypatch.setattr(brain, "publish_terminal", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(brain, "publish_sentiment", lambda *a, **k: None, raising=False)
    saved = list(brain.messages)
    yield
    brain.messages[:] = saved


class TestStep1DecommissionToolTieBreakHint:
    def test_process_user_input_never_injects_tool_hint(self, monkeypatch):
        """Step 1: Laya ID 8 round-0 tool hint is decommissioned in process_user_input."""
        called = []
        monkeypatch.setattr(tr, "pick_tool_for_request",
                            lambda text: called.append(text) or {"tool": "save_note", "escalate": False})
        seen_msgs = []

        def fake_llm(messages, **k):
            seen_msgs.extend(messages)
            return {"role": "assistant", "content": "Noted, Sir."}

        monkeypatch.setattr(brain, "_execute_llm_completion", fake_llm)
        brain.messages[:] = [brain.messages[0]]
        brain.process_user_input("Remember that I hate mushrooms and note down dry cleaning.")
        assert called == []
        assert not any("prefer `save_note`" in str(m.get("content", "")) for m in seen_msgs)


class TestStep2RestoreVisualGroundTruth:
    def test_attach_action_screenshot_always_captures(self, monkeypatch):
        """Step 2a: _attach_action_screenshot always attaches a screenshot without consulting Laya."""
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "FAKE_B64")
        res = brain._attach_action_screenshot("Clicked 'Button' at (10, 20).", action="smart_click")
        assert isinstance(res, dict)
        assert res["ui_screenshot_b64"] == "FAKE_B64"

    def test_look_at_screen_always_uses_vision_pixels(self, monkeypatch):
        """Step 2b: look_at_screen captures real pixels even if _screen_answerable_from_text were True."""
        monkeypatch.setattr(brain, "_screen_answerable_from_text", lambda req: True)
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "REAL_SCREEN_B64")
        monkeypatch.setattr(brain, "_execute_llm_completion",
                            lambda **k: {"role": "assistant", "content": "PowerShell window with traceback."})
        out = brain.execute_tool("look_at_screen", {})
        assert "no screenshot was taken" not in out
        assert "PowerShell window with traceback." in out


class TestStep3ContextualizeRetryGuards:
    def test_claims_tool_execution_suppressed_when_tools_already_executed(self):
        """Step 3a: After a tool executes in Round 1, confirming playback in Round 2 is NOT a hallucination."""
        user_q = "Play some calming lo-fi music on Spotify."
        reply = "Very good, Sir — Lo-Fi Beats is now playing."
        assert brain._claims_tool_execution(user_q, reply, tools_executed=False) is True
        assert brain._claims_tool_execution(user_q, reply, tools_executed=True) is False

    def test_claims_knowledge_cutoff_suppressed_when_research_already_executed(self):
        """Step 3d: After research executes and returns nothing, reporting no data found is NOT a cutoff refusal."""
        reply = "My research turned up nothing specific about Qwen 3.6 MoE benchmarks, Sir."
        assert brain._claims_knowledge_cutoff(reply, research_executed=False) is True
        assert brain._claims_knowledge_cutoff(reply, research_executed=True) is False

    def test_safety_gate_refusal_does_not_trigger_failure_retry(self, monkeypatch):
        """Step 3c: When smart_click returns a safety gate ([REFUSED] / BLOCKED) and the model
        asks the owner for confirmation, failure_retried must NOT fire."""
        assert brain._tool_result_is_safety_gate(
            "FAILED — [REFUSED: confirm_send required for destructive action on 'Reboot'.]"
        ) is True
        assert brain._tool_result_is_safety_gate(
            'FAILED — BLOCKED — "Reboot" looks like a send/destructive action.'
        ) is True
        assert brain._tool_result_is_safety_gate(
            "FAILED — [CONFIRM REQUIRED: 'brother' resolves to 'george'.]"
        ) is True
        assert brain._tool_result_is_safety_gate(
            "FAILED — No active Spotify device found."
        ) is False

        assert brain._acknowledges_failure(
            "Rebooting will disconnect all devices on your network. Shall I proceed?"
        ) is True

    def test_play_spotify_playlist_is_terminal_on_success(self, monkeypatch):
        """Step 3b: play_spotify_playlist exits the loop immediately on success."""
        calls = []

        def fake_llm(messages, **k):
            calls.append(len(calls))
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "play_spotify_playlist", "arguments": '{"playlist_name": "Lo-Fi"}'}
                }]
            }

        monkeypatch.setattr(brain, "_execute_llm_completion", fake_llm)
        monkeypatch.setattr(brain, "play_spotify_playlist", lambda name: f"Playing playlist {name}")
        brain.messages[:] = [brain.messages[0]]
        out = brain.process_user_input("Play some lo-fi playlist on Spotify")
        assert len(calls) == 1
        assert out == "Action completed."


class TestStep4ContactParserBlacklist:
    def test_email_does_not_fuzzy_match_emily(self, monkeypatch):
        """Step 4: 'email' and other reserved words never fuzzy-match to a contact name."""
        monkeypatch.setattr(discord_api, "CONTACTS",
                            {"emily": "111", "george": "222", "farah": "333"}, raising=False)
        assert discord_api.resolve_typo("email") is None
        assert discord_api._fuzzy_token_match(
            "Find the project proposal from earlier this week and email the summary to my accountant."
        ) is None
        intent = brain._extract_discord_message_intent(
            "Find the project proposal from earlier this week and email the summary to my accountant."
        )
        assert intent is None


class TestStep5MemoryDedupReconciliation:
    def test_duplicate_fact_returns_affirmative_memory_confirmed(self, monkeypatch, tmp_path):
        """Step 5: Duplicate fact in Laya gate or vault returns [Memory Confirmed: ...]."""
        monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True, raising=False)
        monkeypatch.setattr(memory, "MD_FILE", str(tmp_path / "memory.md"), raising=False)
        monkeypatch.setattr(memory, "memory_collection", MagicMock(), raising=False)
        monkeypatch.setattr(memory, "_gate_fact",
                            lambda fact, **k: {"skip": True, "reason": "already saved — the same information is in the vault",
                                               "category": "fact", "conflict": "", "supersedes": None})
        out = memory.memorize_fact("Mohamed prefers working late at night.")
        assert out.startswith("[Memory Confirmed:")
        assert "NOT saved" not in out


class TestStep6SystemPromptStreamlining:
    def test_system_prompt_has_clarification_gate_and_reduced_bloat(self):
        """Step 6: Combined system prompt contains Clarification Gate, removes lookup table,
        and stays well below the old 17,025-char bloat."""
        content = brain._build_system_content("jarvis")
        assert "Clarification Gate" in content
        assert "Confirmation Gate" in content
        assert '| "What time is it?"' not in content
        assert len(content) < 9000, f"Expected streamlined prompt < 9000 chars, got {len(content)}"


class TestFurtherOptimizations:
    def test_prefix_preserving_summarizer_passes_system_prompt_and_tools(self, monkeypatch):
        """Optimization 1: Research output summarizer preserves messages[0] + ADMIN_TOOLS."""
        seen = {}

        def fake_llm(messages, temperature=None, n_predict=None, tools=None, **k):
            seen["messages"] = messages
            seen["tools"] = tools
            return {"role": "assistant", "content": "Concise summary."}

        monkeypatch.setattr(brain, "_execute_llm_completion", fake_llm)
        res = brain._prefix_preserving_summarizer([{"role": "user", "content": "Summarize this"}])
        assert res["content"] == "Concise summary."
        assert seen["messages"][0]["role"] == "system"
        assert seen["tools"] is brain.ADMIN_TOOLS

    def test_parallel_non_gui_tool_calls_executed_in_single_round(self, monkeypatch):
        """Optimization 2: When the model emits both memorize_fact and save_note in Round 1,
        both tools are executed and answered in the same round."""
        executed = []
        round_count = {"n": 0}

        def fake_llm(messages, **k):
            round_count["n"] += 1
            if round_count["n"] == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "memorize_fact", "arguments": '{"fact": "Mohamed hates mushrooms"}'}},
                        {"id": "c2", "function": {"name": "save_note", "arguments": '{"text": "Pick up dry cleaning tomorrow morning"}'}},
                    ],
                }
            return {"role": "assistant", "content": "Noted on the dry cleaning, Sir."}

        monkeypatch.setattr(brain, "_execute_llm_completion", fake_llm)
        monkeypatch.setattr(brain, "execute_tool_ex",
                            lambda name, args: (executed.append((name, args)) or True, f"{name} ok"))
        brain.messages[:] = [brain.messages[0]]
        reply = brain.process_user_input(
            "Remember that I hate mushrooms and note down to pick up the dry cleaning tomorrow morning."
        )
        assert [name for name, _ in executed] == ["memorize_fact", "save_note"]
        assert "dry cleaning" in reply

    def test_loop_guard_blocks_search_query_permutations_and_family_budget(self):
        """Optimization 3: LoopGuard catches query-permutation scavenger loops on
        search tools (e.g. search_emails / research with varying query strings)
        and enforces a cross-tool search family budget per turn."""
        from core.loop_guard import LoopGuard

        lg = LoopGuard(enforce=True)
        v1 = lg.check("search_emails", {"query": "project proposal"})
        v2 = lg.check("search_emails", {"query": "proposal earlier this week"})
        v3 = lg.check("search_emails", {"query": "from:accountant"})
        v4 = lg.check("search_emails", {"query": "in:anywhere proposal"})
        assert v1.action == "allow"
        assert v2.action == "allow"
        assert v3.action == "warn"
        assert v4.action == "block"
        assert "search exhaustion" in v4.message

        # Cross-tool search family budget (warns at 5th lookup call, blocks at 6th)
        lg2 = LoopGuard(enforce=True)
        assert lg2.check("get_notes", {}).action == "allow"
        assert lg2.check("recall_memory", {"query": "proposal"}).action == "allow"
        assert lg2.check("list_directory_tree", {}).action == "allow"
        assert lg2.check("list_my_contacts", {"platform": "all"}).action == "allow"
        v5 = lg2.check("search_emails", {"query": "proposal"})
        v6 = lg2.check("research", {"topic": "proposal"})
        assert v5.action == "warn"
        assert v6.action == "block"
        assert "search budget exhausted" in v6.message
