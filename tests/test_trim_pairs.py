"""Tests for tool-pair-aware trimming (core.memory.trim_memory /
_pop_oldest_turn_unit): an assistant message carrying tool_calls and its
trailing role:"tool" messages must be popped as one unit so trimming never
leaves an orphaned tool message with a dangling tool_call_id.

No llama-server required — EXACT_TOKEN_COUNT is forced off so the fixed
4-chars/token heuristic makes budgets deterministic.
"""
import pytest

import config
import core.memory as memory


@pytest.fixture(autouse=True)
def _heuristic_only(monkeypatch):
    monkeypatch.setattr(config, "EXACT_TOKEN_COUNT", False)
    memory._token_ratio["value"] = 4.0
    memory._token_ratio["last_calibrated"] = 0.0
    yield


def _sys():
    return {"role": "system", "content": "S" * 40}


def _user(chars=40):
    return {"role": "user", "content": "u" * chars}


def _assistant(chars=40):
    return {"role": "assistant", "content": "a" * chars}


def _assistant_tc(call_ids, chars=40):
    return {
        "role": "assistant",
        "content": "t" * chars,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "get_current_time", "arguments": "{}"}}
            for cid in call_ids
        ],
    }


def _tool(call_id, chars=40):
    return {"role": "tool", "tool_call_id": call_id, "content": "r" * chars}


def _no_orphans(msgs):
    """No role:tool message whose tool_call_id lacks a preceding assistant tool_calls entry."""
    known_ids = set()
    for m in msgs:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            known_ids.update(tc["id"] for tc in m["tool_calls"])
        elif m.get("role") == "tool":
            if m.get("tool_call_id") not in known_ids:
                return False
    return True


# ── unit: _pop_oldest_turn_unit ──────────────────────────────────────────────

def test_pop_unit_takes_assistant_and_its_tool_messages():
    msgs = [_sys(), _assistant_tc(["c1"]), _tool("c1"), _user()]
    memory._pop_oldest_turn_unit(msgs)
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_pop_unit_takes_all_tool_siblings_of_multi_call():
    msgs = [_sys(), _assistant_tc(["c1", "c2"]), _tool("c1"), _tool("c2"), _user()]
    memory._pop_oldest_turn_unit(msgs)
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_pop_unit_plain_message_pops_alone():
    msgs = [_sys(), _user(), _assistant(), _user()]
    memory._pop_oldest_turn_unit(msgs)
    assert [m["role"] for m in msgs] == ["system", "assistant", "user"]


def test_pop_unit_orphan_tool_message_is_removed():
    msgs = [_sys(), _tool("dangling"), _user()]
    memory._pop_oldest_turn_unit(msgs)
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_pop_unit_assistant_without_tool_calls_leaves_following_user():
    # tool_calls key present but None (final-text native message shape) —
    # must NOT swallow the next message.
    msgs = [_sys(), {"role": "assistant", "content": "hi", "tool_calls": None}, _user()]
    memory._pop_oldest_turn_unit(msgs)
    assert [m["role"] for m in msgs] == ["system", "user"]


# ── integration: trim_memory ─────────────────────────────────────────────────

def test_trim_never_leaves_orphaned_tool_messages():
    msgs = [_sys()]
    for i in range(20):
        msgs.append(_user(200))
        msgs.append(_assistant_tc([f"c{i}"], 200))
        msgs.append(_tool(f"c{i}", 200))
        msgs.append(_assistant(200))
    # Budget small enough to force many pops.
    trimmed = memory.trim_memory(msgs, max_tokens=500)
    assert trimmed[0]["role"] == "system"
    assert _no_orphans(trimmed)
    assert trimmed[1].get("role") != "tool"


def test_trim_under_budget_is_untouched():
    msgs = [_sys(), _user(), _assistant_tc(["c1"]), _tool("c1"), _assistant()]
    before = [dict(m) for m in msgs]
    trimmed = memory.trim_memory(msgs, max_tokens=10_000)
    assert [m["role"] for m in trimmed] == [m["role"] for m in before]


def test_trim_terminates_and_preserves_system_prompt():
    msgs = [_sys()]
    for i in range(5):
        msgs.append(_assistant_tc([f"c{i}"], 4000))
        msgs.append(_tool(f"c{i}", 4000))
    trimmed = memory.trim_memory(msgs, max_tokens=50)  # impossible budget
    assert trimmed[0]["role"] == "system"
    assert len(trimmed) <= 2 or _no_orphans(trimmed)


def test_trim_plain_history_behaves_as_before():
    msgs = [_sys()] + [_user(400) if i % 2 == 0 else _assistant(400) for i in range(20)]
    trimmed = memory.trim_memory(msgs, max_tokens=600)
    assert trimmed[0]["role"] == "system"
    # Oldest messages dropped, newest retained.
    assert trimmed[-1]["content"] == msgs[-1]["content"] if len(trimmed) > 1 else True
