"""Tests for the native-path rewrite of engine_testing/harness.py.

No llama-server required — requests.post is mocked throughout. This module
is not a package (no __init__.py), so we add it to sys.path the same way
run_engine_test.py does.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ENGINE_TESTING_DIR = os.path.join(os.path.dirname(__file__), "..", "engine_testing")
if ENGINE_TESTING_DIR not in sys.path:
    sys.path.insert(0, ENGINE_TESTING_DIR)

import harness  # noqa: E402


def _mock_response(message, usage=None, timings=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": message}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
        "timings": timings or {},
    }
    return resp


def _tool_call_message(name, arguments_json, call_id="call_1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}],
    }


def _final_message(text):
    return {"role": "assistant", "content": text, "tool_calls": None}


# ── llm_call() ────────────────────────────────────────────────────────────────

def test_llm_call_sends_tools_and_tool_choice():
    with patch("requests.post", return_value=_mock_response(_final_message("Hello!"))) as mock_post:
        message, metrics = harness.llm_call([{"role": "user", "content": "hi"}])
    payload = mock_post.call_args.kwargs["json"]
    assert payload["tools"] == harness.ADMIN_TOOLS
    assert payload["tool_choice"] == "auto"
    assert message["content"] == "Hello!"


def test_llm_call_returns_full_message_dict():
    tc_msg = _tool_call_message("get_current_time", "{}")
    with patch("requests.post", return_value=_mock_response(tc_msg)):
        message, metrics = harness.llm_call([{"role": "user", "content": "what time"}])
    assert message["tool_calls"][0]["function"]["name"] == "get_current_time"
    assert "gen_tok_per_s" in metrics


# ── run_scenario(): no XML reminder ever sent ───────────────────────────────

def test_run_scenario_never_sends_inst_reminder():
    sent_payloads = []

    def fake_post(url, json=None, timeout=None):
        sent_payloads.append(json)
        return _mock_response(_final_message("Final answer."))

    with patch("requests.post", side_effect=fake_post):
        result = harness.run_scenario({"id": "t1", "prompt": "hello", "mock_results": {}})

    assert result["final_response"] == "Final answer."
    for payload in sent_payloads:
        for m in payload["messages"]:
            content = m.get("content")
            text = content if isinstance(content, str) else json.dumps(content)
            assert "[INST" not in text


# ── native tool feedback: role:"tool" + matching tool_call_id ──────────────

def test_run_scenario_native_tool_feedback_role_and_id():
    tc_msg = _tool_call_message("get_current_time", "{}", call_id="call_abc123")
    final_msg = _final_message("The time is now.")
    responses = [_mock_response(tc_msg), _mock_response(final_msg)]
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        return responses.pop(0)

    with patch("requests.post", side_effect=fake_post):
        result = harness.run_scenario({
            "id": "t2", "prompt": "what time is it",
            "mock_results": {"get_current_time": "3:00 PM"},
        })

    assert result["tool_calls"] == ["get_current_time"]
    assert result["tool_call_records"] == [{"name": "get_current_time", "arguments": {}}]
    assert result["final_response"] == "The time is now."

    second_call_msgs = sent[1]["messages"]
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_abc123"
    assert tool_msgs[0]["content"] == "3:00 PM"


# ── JSON decode error → re-prompt pair ──────────────────────────────────────

def test_run_scenario_json_error_retries_with_system_note():
    bad_msg = _tool_call_message("read_local_file", "{not valid json", call_id="call_bad")
    final_msg = _final_message("Done.")
    responses = [_mock_response(bad_msg), _mock_response(final_msg)]
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        return responses.pop(0)

    with patch("requests.post", side_effect=fake_post):
        result = harness.run_scenario({"id": "t3", "prompt": "read a file", "mock_results": {}})

    assert result["json_error_count"] == 1
    second_msgs = sent[1]["messages"]
    assert any(
        m.get("role") == "user" and "JSON formatting error" in str(m.get("content"))
        for m in second_msgs
    )


# ── text-syntax leak detection (native analogue of XML mangling) ───────────

def test_run_scenario_text_leak_detected_when_tool_calls_empty():
    leak_msg = {"role": "assistant", "content": '<tool_call>{"name": "x"}</tool_call>', "tool_calls": None}
    with patch("requests.post", return_value=_mock_response(leak_msg)):
        result = harness.run_scenario({"id": "t4", "prompt": "do something", "mock_results": {}})
    assert result["text_leak_count"] == 1
    assert result["tool_calls"] == []


def test_run_scenario_no_leak_when_content_is_clean():
    with patch("requests.post", return_value=_mock_response(_final_message("All good."))):
        result = harness.run_scenario({"id": "t5", "prompt": "hi", "mock_results": {}})
    assert result["text_leak_count"] == 0


# ── empty_final apathy signal ────────────────────────────────────────────────

def test_run_scenario_empty_final_flag_after_tool_execution():
    tc_msg = _tool_call_message("get_current_time", "{}")
    empty_msg = {"role": "assistant", "content": "", "tool_calls": None}
    responses = [_mock_response(tc_msg), _mock_response(empty_msg)]
    with patch("requests.post", side_effect=responses):
        result = harness.run_scenario({
            "id": "t6", "prompt": "what time", "mock_results": {"get_current_time": "noon"},
        })
    assert result["empty_final"] is True


def test_run_scenario_empty_final_false_when_no_tool_ran():
    with patch("requests.post", return_value=_mock_response({"role": "assistant", "content": "", "tool_calls": None})):
        result = harness.run_scenario({"id": "t7", "prompt": "hi", "mock_results": {}})
    assert result["empty_final"] is False


# ── _extract_first_tool_args reads structured records ───────────────────────

def test_extract_first_tool_args_reads_structured_records():
    result = {"tool_call_records": [{"name": "smart_click", "arguments": {"goal": "Save button"}}]}
    assert harness._extract_first_tool_args(result, "smart_click") == {"goal": "Save button"}


def test_extract_first_tool_args_missing_tool_returns_empty():
    assert harness._extract_first_tool_args({"tool_call_records": []}, "smart_click") == {}


# ── check_no_hallucination passes message= through to _claims_tool_execution ─

def test_check_no_hallucination_passes_last_message_kwarg():
    result = {"tool_calls": [], "final_response": "I played the song for you.", "last_message": {"marker": True}}
    with patch("harness._claims_tool_execution", return_value=False) as mock_fn:
        harness.check_no_hallucination(result, "play a song")
    _, kwargs = mock_fn.call_args
    assert kwargs.get("message") is result["last_message"]


# ── loop_trap: LoopGuard mirrored in the harness + check_loop_discipline ────

def test_run_scenario_loop_guard_blocks_fourth_identical_call():
    tc = _tool_call_message("recall_memory", '{"query": "startup"}', call_id="call_lt")
    final = _final_message("I don't have that memory, Sir.")
    responses = [_mock_response(tc)] * 5 + [_mock_response(final)]
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        return responses.pop(0)

    with patch("requests.post", side_effect=fake_post):
        result = harness.run_scenario({
            "id": "lt1", "prompt": "what did I say about the startup?",
            "mock_results": {"recall_memory": "No relevant memories found."},
        })

    # All 5 identical calls were made by the (mocked) model, but only 3 executed.
    assert result["tool_calls"].count("recall_memory") == 5
    assert max(result["executed_call_counts"].values()) == 3
    actions = [e["action"] for e in result["loop_guard_events"]]
    assert "warn" in actions and "block" in actions
    # The blocked rounds fed the LoopGuard message back as the tool result.
    blocked_feedback = [
        m for payload in sent for m in payload["messages"]
        if m.get("role") == "tool" and "[LoopGuard] BLOCKED" in str(m.get("content"))
    ]
    assert blocked_feedback


def test_run_scenario_no_loop_guard_events_on_clean_run():
    tc = _tool_call_message("get_current_time", "{}")
    final = _final_message("It is noon, Sir.")
    with patch("requests.post", side_effect=[_mock_response(tc), _mock_response(final)]):
        result = harness.run_scenario({
            "id": "lt2", "prompt": "time?", "mock_results": {"get_current_time": "12:00"},
        })
    assert result["loop_guard_events"] == []
    assert max(result["executed_call_counts"].values()) == 1


def test_check_loop_discipline_passes_on_disciplined_run():
    verdict = harness.check_loop_discipline({
        "executed_call_counts": {"h1": 2},
        "loop_guard_events": [],
        "final_response": "Nothing found, Sir.",
    })
    assert verdict["passed"] is True


def test_check_loop_discipline_fails_on_grinding():
    verdict = harness.check_loop_discipline({
        "executed_call_counts": {"h1": 4},
        "loop_guard_events": [],
        "final_response": "Done.",
    })
    assert verdict["passed"] is False


def test_check_loop_discipline_fails_on_empty_final():
    verdict = harness.check_loop_discipline({
        "executed_call_counts": {"h1": 1},
        "loop_guard_events": [],
        "final_response": "",
    })
    assert verdict["passed"] is False


def test_loop_trap_scenarios_registered():
    import scenarios as sc
    traps = [s for s in sc.SCENARIOS if s.get("category") == "loop_trap"]
    assert len(traps) == 5
    assert all(s.get("mock_results") for s in traps)
    assert all(not s.get("manual") for s in traps)
