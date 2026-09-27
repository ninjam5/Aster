"""Tests for auto-compaction (core.brain._compact_context_locked + the
auto-trigger inside process_user_input). Mocked LLM — no llama-server.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import config
import core.brain as brain
import core.memory as memory


@pytest.fixture(autouse=True)
def _deterministic_tokens(monkeypatch):
    monkeypatch.setattr(config, "EXACT_TOKEN_COUNT", False)
    memory._token_ratio["value"] = 4.0
    memory._token_ratio["last_calibrated"] = 0.0
    yield


@pytest.fixture
def _isolated_messages(monkeypatch):
    """Give brain a private messages list and restore the real one after."""
    original = brain.messages
    fresh = [{"role": "system", "content": "SYSTEM PROMPT"}]
    monkeypatch.setattr(brain, "messages", fresh)
    yield fresh
    brain.messages = original


def _mock_completion(content="dense summary of everything"):
    return {"role": "assistant", "content": content, "tool_calls": None}


def _fill_history(n_msgs=40, chars=1000):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * chars}
            for i in range(n_msgs)]


# ── _compact_context_locked ──────────────────────────────────────────────────

def test_compact_replaces_history_with_restored_block(_isolated_messages):
    brain.messages.extend(_fill_history())  # ~10k tokens at 4 chars/token
    with patch.object(brain, "_execute_llm_completion", return_value=_mock_completion()) as mock_llm:
        result = brain._compact_context_locked("manual")
    assert "Compaction complete" in result
    assert len(brain.messages) == 2
    assert brain.messages[0]["content"] == "SYSTEM PROMPT"
    assert brain.messages[1]["role"] == "assistant"
    assert brain.messages[1]["content"].startswith("[System Memory Restored:")
    assert "dense summary of everything" in brain.messages[1]["content"]
    # summarization ran at temp 0.3 over the old history + override prompt
    called_msgs = mock_llm.call_args.kwargs["messages"]
    assert called_msgs[-1]["role"] == "user"
    assert "[SYSTEM OVERRIDE]" in called_msgs[-1]["content"]


def test_compact_safety_lock_under_2000_tokens(_isolated_messages):
    brain.messages.append({"role": "user", "content": "short chat"})
    with patch.object(brain, "_execute_llm_completion") as mock_llm:
        result = brain._compact_context_locked("manual")
    mock_llm.assert_not_called()
    assert "wait till we hit 2000" in result
    assert len(brain.messages) == 2  # untouched


def test_compact_estimator_ignores_image_payloads(_isolated_messages):
    # One huge base64 image must not fake the token count past the safety lock.
    huge_b64 = "A" * 500_000
    brain.messages.append({
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{huge_b64}"}},
            {"type": "text", "text": "look at this"},
        ],
    })
    with patch.object(brain, "_execute_llm_completion") as mock_llm:
        result = brain._compact_context_locked("manual")
    mock_llm.assert_not_called()  # ~256 image tokens + a few text tokens < 2000
    assert "wait till we hit 2000" in result


# ── auto trigger in process_user_input ───────────────────────────────────────

def _run_turn(user_text="hello there"):
    """Drive one process_user_input turn with a mocked final-text completion."""
    with patch.object(brain, "_execute_llm_completion", return_value=_mock_completion("Hi.")):
        return brain.process_user_input(user_text)


def test_auto_compact_fires_over_threshold(_isolated_messages, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AUTO_COMPACT_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_COMPACT_THRESHOLD", 0.75)
    monkeypatch.setattr(config, "N_CTX", 4000)  # 0.75*4000 = 3000-token trigger
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", True)
    monkeypatch.setattr(config, "RELIABILITY_LOG_PATH", str(tmp_path / "rel.jsonl"))
    brain.messages.extend(_fill_history(n_msgs=20, chars=1000))  # ~5k tokens > 3000

    _run_turn()

    restored = [m for m in brain.messages if str(m.get("content", "")).startswith("[System Memory Restored:")]
    assert restored, "auto-compact did not replace history"
    events = [json.loads(l) for l in open(tmp_path / "rel.jsonl", encoding="utf-8")]
    assert any(e["event"] == "auto_compact" for e in events)


def test_auto_compact_skips_under_threshold(_isolated_messages, monkeypatch):
    monkeypatch.setattr(config, "AUTO_COMPACT_ENABLED", True)
    monkeypatch.setattr(config, "AUTO_COMPACT_THRESHOLD", 0.75)
    monkeypatch.setattr(config, "N_CTX", 60000)
    brain.messages.extend(_fill_history(n_msgs=6, chars=400))

    _run_turn()

    assert not any(str(m.get("content", "")).startswith("[System Memory Restored:") for m in brain.messages)


def test_auto_compact_flag_off_never_fires(_isolated_messages, monkeypatch):
    monkeypatch.setattr(config, "AUTO_COMPACT_ENABLED", False)
    monkeypatch.setattr(config, "N_CTX", 4000)
    brain.messages.extend(_fill_history(n_msgs=20, chars=1000))

    _run_turn()

    assert not any(str(m.get("content", "")).startswith("[System Memory Restored:") for m in brain.messages)


def test_manual_compact_command_uses_shared_helper(_isolated_messages):
    brain.messages.extend(_fill_history())
    with patch.object(brain, "_execute_llm_completion", return_value=_mock_completion()):
        result = brain.process_user_input("/compact")
    assert "Compaction complete" in result
    assert len(brain.messages) == 2
    assert brain.messages[1]["content"].startswith("[System Memory Restored:")
