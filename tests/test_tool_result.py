"""Tests for the structured tool-result envelope (core/tool_result.py +
core.brain execute_tool_ex/_normalize_tool_result/execute_tool split).

No llama-server needed."""
import pytest

import config
import core.brain as brain
from core.tool_result import ToolResult, tool_ok, tool_fail


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(config, "STRUCTURED_TOOL_RESULTS", True)
    yield


# ── envelope basics ──────────────────────────────────────────────────────────

def test_str_of_toolresult_is_the_text():
    assert str(tool_fail("FAILED — nope")) == "FAILED — nope"
    assert str(tool_ok("done")) == "done"


def test_helpers_coerce_to_str():
    assert tool_ok(42).text == "42"
    assert tool_fail(None).text == "None"


# ── _normalize_tool_result ───────────────────────────────────────────────────

def test_plain_string_uses_prefix_heuristic():
    ok, payload = brain._normalize_tool_result("FAILED — nothing is playing.")
    assert ok is False and payload == "FAILED — nothing is playing."
    ok, payload = brain._normalize_tool_result("Error: goal parameter is required.")
    assert ok is False
    ok, payload = brain._normalize_tool_result("Skipped to next track.")
    assert ok is True


def test_heuristic_still_misses_non_prefixed_failure_text():
    # Documents WHY the envelope exists: this failure text never tripped the
    # regex — a plain-string tool returning it reads as success.
    ok, _ = brain._normalize_tool_result("Could not parse time '7 oclock'.")
    assert ok is True


def test_envelope_is_exact_even_without_failed_prefix():
    ok, payload = brain._normalize_tool_result(tool_fail("Could not parse time '7 oclock'."))
    assert ok is False
    assert payload == "Could not parse time '7 oclock'."


def test_envelope_success_with_scary_text_stays_ok():
    ok, _ = brain._normalize_tool_result(tool_ok("Error log downloaded successfully."))
    assert ok is True


def test_ui_dict_payload_preserved_and_heuristic_on_text():
    d = {"text": "Clicked 'Save' at (10, 20).", "ui_screenshot_b64": "abc"}
    ok, payload = brain._normalize_tool_result(d)
    assert ok is True and payload is d
    d2 = {"text": "FAILED — could not locate", "ui_screenshot_b64": "abc"}
    ok2, payload2 = brain._normalize_tool_result(d2)
    assert ok2 is False and payload2 is d2


def test_none_result_is_ok():
    ok, payload = brain._normalize_tool_result(None)
    assert ok is True and payload is None


def test_flag_off_ignores_envelope_ok(monkeypatch):
    monkeypatch.setattr(config, "STRUCTURED_TOOL_RESULTS", False)
    # Envelope says fail, but text has no FAILED/Error prefix → legacy heuristic wins.
    ok, _ = brain._normalize_tool_result(tool_fail("Could not parse time."))
    assert ok is True
    # And a FAILED-prefixed envelope still reads failed via the regex.
    ok2, _ = brain._normalize_tool_result(tool_fail("FAILED — nope"))
    assert ok2 is False


def test_loop_guard_block_message_normalizes_ok():
    from core.loop_guard import LoopGuard
    lg = LoopGuard(enforce=True)
    verdict = None
    for _ in range(4):
        verdict = lg.check("play_spotify_track", {"query": "x"})
    assert verdict.action == "block"
    ok, _ = brain._normalize_tool_result(verdict.message)
    assert ok is True  # a guard block is not a tool failure


# ── execute_tool / execute_tool_ex dispatch ──────────────────────────────────

def test_execute_tool_strips_envelope_to_plain_string(monkeypatch):
    monkeypatch.setattr(brain, "_execute_tool_impl", lambda n, a: tool_fail("FAILED — x"))
    result = brain.execute_tool("whatever", {})
    assert isinstance(result, str) and result == "FAILED — x"


def test_execute_tool_ex_returns_ok_and_payload(monkeypatch):
    monkeypatch.setattr(brain, "_execute_tool_impl", lambda n, a: tool_ok("done"))
    ok, payload = brain.execute_tool_ex("whatever", {})
    assert ok is True and payload == "done"


def test_execute_tool_passes_dict_through_unchanged(monkeypatch):
    d = {"text": "Clicked.", "ui_screenshot_b64": "zz"}
    monkeypatch.setattr(brain, "_execute_tool_impl", lambda n, a: d)
    assert brain.execute_tool("smart_click", {"goal": "x"}) is d


# ── migrated producers keep their LLM-facing text ────────────────────────────

def test_media_no_device_envelope_text_unchanged():
    import tools.media as media
    assert isinstance(media._NO_DEVICE, ToolResult)
    assert media._NO_DEVICE.ok is False
    assert media._NO_DEVICE.text.startswith("FAILED — nothing is playing.")


def test_media_not_installed_is_now_a_failure():
    import tools.media as media
    assert media._NOT_INSTALLED.ok is False
    assert media._NOT_INSTALLED.text == "spotipy not installed"


@pytest.fixture(autouse=True)
def _isolate_reliability_log(tmp_path, monkeypatch):
    """QA round 3: these files exercise process_user_input/LoopGuard without isolating
    the reliability log, so a test run appended to the real Aster_Vault/reliability_log."""
    import config
    monkeypatch.setattr(config, "RELIABILITY_LOG_PATH",
                        str(tmp_path / "rel.jsonl"), raising=False)
