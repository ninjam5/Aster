"""Tests for core.loop_guard.LoopGuard — pure unit tests, no mocks, no llama-server.

Also disables reliability logging so these tests don't write to
Aster_Vault/reliability_log.jsonl.
"""
import pytest

import config
from core.loop_guard import LoopGuard


@pytest.fixture(autouse=True)
def _no_log(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False)


def test_allow_then_warn_then_block():
    lg = LoopGuard(enforce=True, warn_at=3, block_at=4)
    v1 = lg.check("read_local_file", {"file_path": "a.txt"})
    v2 = lg.check("read_local_file", {"file_path": "a.txt"})
    v3 = lg.check("read_local_file", {"file_path": "a.txt"})
    v4 = lg.check("read_local_file", {"file_path": "a.txt"})
    assert v1.action == "allow"
    assert v2.action == "allow"
    assert v3.action == "warn"
    assert v4.action == "block"
    assert "BLOCKED" in v4.message


def test_different_args_do_not_accumulate():
    lg = LoopGuard(enforce=True)
    v1 = lg.check("read_local_file", {"file_path": "a.txt"})
    v2 = lg.check("read_local_file", {"file_path": "b.txt"})
    assert v1.action == "allow"
    assert v2.action == "allow"
    assert v1.call_hash != v2.call_hash


def test_hash_stable_under_key_reorder():
    lg = LoopGuard()
    h1 = lg.check("open_application", {"name": "chrome", "action": "open"}).call_hash
    lg2 = LoopGuard()
    h2 = lg2.check("open_application", {"action": "open", "name": "chrome"}).call_hash
    assert h1 == h2


def test_pingpong_detection_warns_then_blocks():
    lg = LoopGuard(enforce=True, warn_at=99, block_at=99)  # disable identical-call path
    a = {"goal": "A"}
    b = {"goal": "B"}
    lg.check("smart_click_x", a)
    lg.check("smart_click_x", b)
    lg.check("smart_click_x", a)
    v = lg.check("smart_click_x", b)  # A-B-A-B complete
    assert v.action == "warn"
    lg.check("smart_click_x", a)
    v2 = lg.check("smart_click_x", b)  # continues alternating
    assert v2.action == "block"


def test_watch_screen_never_blocks():
    lg = LoopGuard(enforce=True, warn_at=2, block_at=3)
    args = {"target": "done"}
    verdicts = [lg.check("watch_screen", args) for _ in range(30)]
    assert all(v.action != "block" for v in verdicts)


def test_smart_click_warns_only_never_blocks():
    lg = LoopGuard(enforce=True, warn_at=2, block_at=3)
    args = {"goal": "Next button"}
    verdicts = [lg.check("smart_click", args) for _ in range(10)]
    assert all(v.action != "block" for v in verdicts)
    assert any(v.action == "warn" for v in verdicts)


def test_enforce_false_downgrades_to_allow_but_still_counts():
    lg = LoopGuard(enforce=False, warn_at=2, block_at=3)
    args = {"file_path": "a.txt"}
    verdicts = [lg.check("read_local_file", args) for _ in range(5)]
    assert all(v.action == "allow" for v in verdicts)
    assert verdicts[-1].count == 5


def test_instances_are_isolated():
    lg1 = LoopGuard(enforce=True, warn_at=2, block_at=3)
    lg2 = LoopGuard(enforce=True, warn_at=2, block_at=3)
    args = {"file_path": "a.txt"}
    lg1.check("read_local_file", args)
    lg1.check("read_local_file", args)
    v = lg2.check("read_local_file", args)
    assert v.action == "allow"
    assert v.count == 1


def test_block_message_does_not_look_like_a_tool_failure():
    from core.brain import _tool_result_failed
    lg = LoopGuard(enforce=True, warn_at=2, block_at=3)
    args = {"file_path": "a.txt"}
    lg.check("read_local_file", args)
    lg.check("read_local_file", args)
    v = lg.check("read_local_file", args)
    assert v.action == "block"
    assert not _tool_result_failed(v.message)
