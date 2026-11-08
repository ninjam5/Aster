"""tests/test_console_logger.py — Unit tests for console debug logging."""

import os
import sys
import threading
import pytest
from unittest.mock import patch
import tools.console_logger as console_logger


def test_disabled_by_default_in_tests():
    """console_logger.install() must be a no-op during pytest runs unless force=True."""
    # Ensure any previous state is uninstalled
    console_logger.uninstall()

    res = console_logger.install()
    assert res is None
    assert not console_logger.is_installed()


def test_install_uninstall_lifecycle(tmp_path):
    """Test full install, write mirroring, ANSI strip, and clean uninstall."""
    console_logger.uninstall()
    logs_dir = str(tmp_path / "console-logs")

    log_path = console_logger.install(force=True, logs_dir=logs_dir)
    try:
        assert log_path is not None
        assert os.path.isfile(log_path)
        assert console_logger.is_installed()
        assert console_logger.get_current_log_path() == log_path

        # Write to stdout and stderr
        print("Test stdout line 1")
        sys.stderr.write("Test stderr line 2\n")
        sys.stdout.write("\x1b[32mColor text\x1b[0m\n")
        sys.stdout.write("Unicode text: ♡ مرحبا 🚀\n")
        sys.stdout.flush()
        sys.stderr.flush()

        # Verify content while open
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "Aster Console Log Started:" in content
        assert "Test stdout line 1" in content
        assert "Test stderr line 2" in content
        assert "Color text" in content
        assert "\x1b[32m" not in content  # ANSI stripped
        assert "Unicode text: ♡ مرحبا 🚀" in content

    finally:
        console_logger.uninstall()

    assert not console_logger.is_installed()
    assert console_logger.get_current_log_path() is None

    # Verify footer was appended upon uninstall
    with open(log_path, "r", encoding="utf-8") as f:
        final_content = f.read()

    assert "Aster Console Log Ended:" in final_content


def test_concurrent_writes(tmp_path):
    """Test thread-safety with concurrent writes to stdout and stderr."""
    console_logger.uninstall()
    logs_dir = str(tmp_path / "console-logs")
    log_path = console_logger.install(force=True, logs_dir=logs_dir)

    try:
        def worker(num):
            for i in range(20):
                print(f"[Thread-{num}] message {i}")
                sys.stderr.write(f"[Thread-{num}] err {i}\n")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()

        for t in range(5):
            assert f"[Thread-{t}] message 0" in content
            assert f"[Thread-{t}] err 0" in content

    finally:
        console_logger.uninstall()


def test_config_gating(tmp_path):
    """Test that install() honors config.CONSOLE_DEBUG_LOG_ENABLED when test suppression is bypassed."""
    console_logger.uninstall()
    logs_dir = str(tmp_path / "console-logs")

    # When False -> None
    with patch("tools.console_logger._suppressed_in_tests", return_value=False):
        with patch("config.CONSOLE_DEBUG_LOG_ENABLED", False):
            path = console_logger.install(logs_dir=logs_dir)
            assert path is None
            assert not console_logger.is_installed()

    # When True -> log created
    with patch("tools.console_logger._suppressed_in_tests", return_value=False):
        with patch("config.CONSOLE_DEBUG_LOG_ENABLED", True):
            path = console_logger.install(logs_dir=logs_dir)
            try:
                assert path is not None
                assert os.path.isfile(path)
                assert console_logger.is_installed()
            finally:
                console_logger.uninstall()
