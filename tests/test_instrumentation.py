"""Tests for the reliability-campaign instrumentation logger (core.instrumentation).

No llama-server required: this exercises the JSONL writer/reader in isolation.
"""
import json
import threading

import pytest

import config
import core.instrumentation as inst


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    path = str(tmp_path / "reliability_log.jsonl")
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", True)
    monkeypatch.setattr(config, "RELIABILITY_LOG_PATH", path)
    return path


def test_record_event_writes_jsonl_line(log_path):
    inst.record_event("hallucination_retry", turn_id="t1", round=2)
    entries = inst.read_log()
    assert len(entries) == 1
    assert entries[0]["event"] == "hallucination_retry"
    assert entries[0]["turn_id"] == "t1"
    assert entries[0]["round"] == 2
    assert "ts" in entries[0]


def test_record_event_appends_multiple_lines(log_path):
    inst.record_event("apathy_nudge", turn_id="t1")
    inst.record_event("cutoff_retry", turn_id="t2")
    entries = inst.read_log()
    assert [e["event"] for e in entries] == ["apathy_nudge", "cutoff_retry"]


def test_no_op_when_disabled(log_path, monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", False)
    inst.record_event("failure_retry", turn_id="t1")
    assert inst.read_log() == []


def test_read_log_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_PATH", str(tmp_path / "does_not_exist.jsonl"))
    assert inst.read_log() == []


def test_read_log_skips_malformed_lines(log_path):
    inst.record_event("turn_complete", turn_id="t1", outcome="final_text")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("not valid json\n")
    entries = inst.read_log()
    assert len(entries) == 1
    assert entries[0]["event"] == "turn_complete"


def test_record_event_never_raises_on_bad_path(monkeypatch):
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", True)
    monkeypatch.setattr(config, "RELIABILITY_LOG_PATH", "Z:\\definitely\\not\\a\\real\\path.jsonl")
    inst.record_event("json_error_retry", turn_id="t1")  # must not raise


def test_thread_safety_smoke(log_path):
    def _writer(n):
        for i in range(n):
            inst.record_event("repeat_call", tool="x", count=i)

    threads = [threading.Thread(target=_writer, args=(25,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = inst.read_log()
    assert len(entries) == 100


def test_new_turn_id_format():
    tid = inst.new_turn_id("admin")
    assert "admin" in tid
    assert isinstance(tid, str) and len(tid) > 0
