"""Tests for Discord brain parity (instrumentation + LoopGuard in
process_discord_chat). Mocked llama-server via requests.post."""
import json
from unittest.mock import MagicMock, patch

import pytest

import config
import core.brain as brain
import tools.conversations as conversations
import tools.people as people


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOOP_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", True)
    monkeypatch.setattr(config, "RELIABILITY_LOG_PATH", str(tmp_path / "rel.jsonl"))
    # QA 2026-09-29: process_discord_chat consults the conversation-ending store and
    # tools/people — keep both off the owner's real Aster_Vault files.
    monkeypatch.setattr(conversations, "_PATH", str(tmp_path / "conv.json"), raising=False)
    monkeypatch.setattr(people, "_PATH", str(tmp_path / "people.json"), raising=False)
    # Fresh per-friend history each test
    brain.discord_chat_histories.pop("tester", None)
    yield
    brain.discord_chat_histories.pop("tester", None)


def _events(tmp_path):
    path = tmp_path / "rel.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _mock_response(message):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": message}]}
    return resp


def _final(text):
    return _mock_response({"role": "assistant", "content": text, "tool_calls": None})


def _tool_call(name, args_json="{}", call_id="dc_1"):
    return _mock_response({
        "role": "assistant", "content": None,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": args_json}}],
    })


def test_final_text_turn_complete_event(tmp_path):
    with patch("requests.post", return_value=_final("Good evening, Sir.")):
        out = brain.process_discord_chat("tester", "hello")
    assert out
    events = _events(tmp_path)
    completes = [e for e in events if e["event"] == "turn_complete"]
    assert len(completes) == 1
    e = completes[0]
    assert e["outcome"] == "final_text"
    assert e["rounds_used"] == 1
    assert e["tool_calls"] == 0
    assert "-discord-" in e["turn_id"]


def test_tool_round_then_final_counts_tool_calls(tmp_path):
    responses = [_tool_call("get_current_track"), _final("Playing a song, Sir.")]
    with patch("requests.post", side_effect=responses), \
         patch.object(brain, "execute_tool", return_value="Song X by Artist Y") as exec_mock:
        out = brain.process_discord_chat("tester", "what song is playing?")
    exec_mock.assert_called_once()
    e = [x for x in _events(tmp_path) if x["event"] == "turn_complete"][0]
    assert e["tool_calls"] == 1
    assert e["rounds_used"] == 2


def test_max_rounds_outcome(tmp_path):
    # 4 rounds of identical tool calls, never a final text.
    with patch("requests.post", side_effect=[_tool_call("get_current_track")] * 4), \
         patch.object(brain, "execute_tool", return_value="Song X"):
        out = brain.process_discord_chat("tester", "song?")
    assert "could not complete" in out
    e = [x for x in _events(tmp_path) if x["event"] == "turn_complete"][0]
    assert e["outcome"] == "max_rounds"


def test_loop_guard_blocks_repeated_discord_tool(tmp_path):
    # Identical call every round: LoopGuard warns at 3, blocks at 4.
    with patch("requests.post", side_effect=[_tool_call("get_current_track")] * 4), \
         patch.object(brain, "execute_tool", return_value="Song X") as exec_mock:
        brain.process_discord_chat("tester", "song?")
    # 4 rounds but the 4th execution was blocked → only 3 real executions.
    assert exec_mock.call_count == 3
    history = brain.discord_chat_histories["tester"]
    assert any("[LoopGuard] BLOCKED" in str(m.get("content", "")) for m in history)


def test_json_error_event_recorded(tmp_path):
    responses = [_tool_call("get_current_track", args_json="{bad json"), _final("Sorry, Sir.")]
    with patch("requests.post", side_effect=responses):
        brain.process_discord_chat("tester", "song?")
    events = _events(tmp_path)
    assert any(e["event"] == "json_error_retry" and "-discord-" in e["turn_id"] for e in events)


def test_exception_outcome_error(tmp_path):
    with patch("requests.post", side_effect=RuntimeError("connection exploded")):
        out = brain.process_discord_chat("tester", "hello")
    assert "internal error" in out
    e = [x for x in _events(tmp_path) if x["event"] == "turn_complete"][0]
    assert e["outcome"] == "error"
