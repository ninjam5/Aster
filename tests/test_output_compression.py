"""Tests for core.output_compression.compress_tool_output — no llama-server
needed; the 'research' summarize path uses an injected fake summarizer."""
import pytest

import config
import core.output_compression as oc


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_COMPRESSION", True)


@pytest.fixture(autouse=True)
def _reset_summarizer():
    original = oc._summarizer
    yield
    oc.set_summarizer(original)


def test_noop_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(config, "TOOL_OUTPUT_COMPRESSION", False)
    text = "x" * 10000
    assert oc.compress_tool_output("read_local_file", text, {"file_path": "big.txt"}) == text


def test_short_text_untouched():
    text = "short result"
    assert oc.compress_tool_output("read_local_file", text, {"file_path": "a.txt"}) == text


def test_unknown_tool_untouched():
    text = "x" * 10000
    assert oc.compress_tool_output("some_random_tool", text) == text


def test_read_local_file_head_tail_tombstone():
    text = "A" * 4000 + "B" * 3000 + "C" * 1000  # 8000 chars, > 6000 threshold
    result = oc.compress_tool_output("read_local_file", text, {"file_path": "notes.txt"})
    assert result.startswith("A" * 4000)
    assert result.endswith("C" * 1000)
    assert "notes.txt" in result
    assert "omitted" in result
    assert len(result) < len(text)


def test_read_local_file_tombstone_defaults_path_label_when_missing():
    text = "X" * 7000
    result = oc.compress_tool_output("read_local_file", text, {})
    assert "the file" in result


def test_list_directory_tree_head_truncate():
    text = "\n".join(f"file_{i}.txt" for i in range(2000))  # well over 4000 chars
    result = oc.compress_tool_output("list_directory_tree", text, {"root_path": "."})
    assert result.startswith(text[:4000])
    assert "more characters omitted" in result
    assert len(result) < len(text)


def test_get_notes_head_truncate():
    text = "note line\n" * 1000
    result = oc.compress_tool_output("get_notes", text)
    assert len(result) < len(text)
    assert "omitted" in result


def test_list_running_processes_head_truncate():
    text = "proc_row\n" * 1000
    result = oc.compress_tool_output("list_running_processes", text)
    assert len(result) < len(text)


def test_research_summarize_uses_injected_summarizer():
    calls = []

    def fake_summarizer(messages, temperature=None, n_predict=None):
        calls.append((temperature, n_predict))
        return {"content": "A concise summary with https://example.com preserved."}

    oc.set_summarizer(fake_summarizer)
    text = "long research material. " * 500  # > 6000 chars
    result = oc.compress_tool_output("research", text, {"topic": "example"})
    assert "Summarized from" in result
    assert "concise summary" in result
    assert calls and calls[0][0] == 0.2


def test_research_summarize_falls_back_to_truncate_on_summarizer_error():
    def bad_summarizer(messages, temperature=None, n_predict=None):
        raise RuntimeError("llama-server down")

    oc.set_summarizer(bad_summarizer)
    text = "long research material. " * 500
    result = oc.compress_tool_output("research", text, {"topic": "example"})
    assert "omitted" in result
    assert len(result) < len(text)


def test_research_no_summarizer_registered_falls_back_to_truncate():
    oc.set_summarizer(None)
    text = "long research material. " * 500
    result = oc.compress_tool_output("research", text)
    assert len(result) < len(text)


def test_never_raises_on_non_string_input():
    assert oc.compress_tool_output("read_local_file", {"text": "dict result"}, {}) == {"text": "dict result"}
    assert oc.compress_tool_output("read_local_file", None, {}) is None
