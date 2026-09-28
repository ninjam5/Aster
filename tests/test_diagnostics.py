"""
Diagnostics alert-channel Test Suite.

Regression guard for the 2026-09-25 incident: the streaming-TTS test's fake
"boom" reached `diagnostics.send_error`, which is unconditional by design and
pinged the owner's real Telegram. These tests verify the channel is isolated
from test runs, deduplicated, and never raises.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import tools.diagnostics as diag


@pytest.fixture(autouse=True)
def _clear_dedupe():
    diag._recent_errors.clear()
    yield
    diag._recent_errors.clear()


def _fake_bot(monkeypatch):
    bot = MagicMock()
    monkeypatch.setattr(config, "bot", bot, raising=False)
    monkeypatch.setattr(config, "AUTHORIZED_CHAT_ID", 1, raising=False)
    return bot


class TestErrorChannel:
    def test_no_bot_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(config, "bot", None, raising=False)
        diag.send_error("ctx", RuntimeError("x"))  # must not raise

    def test_suppressed_under_pytest(self, monkeypatch):
        """The PYTEST_CURRENT_TEST guard means even a configured bot gets nothing."""
        bot = _fake_bot(monkeypatch)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_x (call)")
        diag.send_error("ctx", RuntimeError("boom"))
        bot.send_message.assert_not_called()

    def test_dedupes_identical_errors(self, monkeypatch):
        bot = _fake_bot(monkeypatch)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        for _ in range(3):
            try:
                raise RuntimeError("boom")
            except Exception as e:
                diag.send_error("Kokoro TTS synthesis (call)", e)
        assert bot.send_message.call_count == 1

    def test_distinct_contexts_both_send(self, monkeypatch):
        bot = _fake_bot(monkeypatch)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        try:
            raise RuntimeError("boom")
        except Exception as e:
            diag.send_error("A", e)
        try:
            raise RuntimeError("boom")
        except Exception as e:
            diag.send_error("B", e)
        assert bot.send_message.call_count == 2

    def test_alert_suppressed_under_pytest(self, monkeypatch):
        bot = _fake_bot(monkeypatch)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_x (call)")
        diag.send_alert("watchdog", "server down")
        bot.send_message.assert_not_called()


class TestEncodingSafety:
    """Regression: a non-cp1252 print (e.g. a model reply containing "♡" from an
    image) must not raise inside the tool loop and kill the turn."""

    def test_write_falls_back_to_ascii_when_console_cannot_encode(self):
        from tools.diagnostics import DiagnosticsStream

        class Cp1252Stream:
            def __init__(self):
                self.written = []

            def write(self, text):
                text.encode("cp1252")          # raises UnicodeEncodeError on "♡"
                self.written.append(text)

            def flush(self):
                pass

        original = Cp1252Stream()
        stream = DiagnosticsStream(original)
        stream.write("names: \u2661 Emmizzz \u2661\n")   # must not raise

        assert original.written, "fallback never wrote anything"
        assert "Emmizzz" in "".join(original.written)
        assert "?" in "".join(original.written)            # ♡ replaced

    def test_write_survives_any_original_failure(self):
        from tools.diagnostics import DiagnosticsStream

        class Broken:
            def write(self, text):
                raise RuntimeError("boom")

            def flush(self):
                pass

        DiagnosticsStream(Broken()).write("anything\n")    # must not raise
