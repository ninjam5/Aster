"""tools/console_logger.py — Console debug logger for Aster.

Mirrors all terminal stdout and stderr to date-stamped log files in
Aster_Vault/console-logs/ when runtime.debug is true in self_config.yaml.
Thread-safe, non-blocking, UTF-8 clean, strips ANSI colors for pristine file logs.
"""

import atexit
import datetime
import os
import re
import sys
import threading

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

_lock = threading.RLock()
_installed = False
_log_file = None
_current_log_path = None
_original_stdout = None
_original_stderr = None


def _suppressed_in_tests() -> bool:
    """Return True if running inside pytest."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


class ConsoleTee:
    """Stream wrapper that writes to both the original stream and a log file."""

    def __init__(self, stream, log_file, lock: threading.RLock):
        self._stream = stream
        self._file = log_file
        self._lock = lock

    def write(self, text: str) -> int:
        if not text:
            return 0

        # 1. Forward to original terminal stream
        try:
            self._stream.write(text)
        except UnicodeEncodeError:
            try:
                self._stream.write(text.encode("ascii", "replace").decode("ascii"))
            except Exception:
                pass
        except Exception:
            pass

        # 2. Mirror to log file (UTF-8, ANSI stripped)
        try:
            clean_text = _ANSI_ESCAPE_RE.sub("", text)
            with self._lock:
                if self._file and not self._file.closed:
                    self._file.write(clean_text)
                    self._file.flush()
        except Exception:
            pass

        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            with self._lock:
                if self._file and not self._file.closed:
                    self._file.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False

    def fileno(self):
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")

    @property
    def errors(self) -> str:
        return getattr(self._stream, "errors", "replace")

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def install(force: bool = False, logs_dir: str | None = None) -> str | None:
    """Install the console tee logger if enabled in config or forced.

    Returns the path to the active log file, or None if logging was not enabled.
    """
    global _installed, _log_file, _current_log_path, _original_stdout, _original_stderr

    with _lock:
        if _installed:
            return _current_log_path

        if not force and _suppressed_in_tests():
            return None

        import config

        enabled = bool(getattr(config, "CONSOLE_DEBUG_LOG_ENABLED", getattr(config, "DEBUG_MODE", False)))
        if not enabled and not force:
            return None

        target_dir = logs_dir or getattr(
            config, "CONSOLE_LOGS_DIR", os.path.join(getattr(config, "VAULT_DIR", "Aster_Vault"), "console-logs")
        )

        try:
            os.makedirs(target_dir, exist_ok=True)
            now = datetime.datetime.now()
            timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"console_{timestamp_str}.log"
            log_path = os.path.join(target_dir, filename)

            _log_file = open(log_path, "a", encoding="utf-8", errors="replace")

            # Write header
            header = (
                f"================================================================================\n"
                f"Aster Console Log Started: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Session PID: {os.getpid()}\n"
                f"================================================================================\n"
            )
            _log_file.write(header)
            _log_file.flush()

            _original_stdout = sys.stdout
            _original_stderr = sys.stderr

            sys.stdout = ConsoleTee(_original_stdout, _log_file, _lock)
            sys.stderr = ConsoleTee(_original_stderr, _log_file, _lock)

            _installed = True
            _current_log_path = log_path

            atexit.register(uninstall)
            return log_path
        except Exception as e:
            # Never crash Aster on logger initialization failure
            try:
                sys.stderr.write(f"[Aster Core] WARNING: Failed to initialize console logger ({e}).\n")
            except Exception:
                pass
            return None


def uninstall() -> None:
    """Restore original stdout and stderr streams and close the log file."""
    global _installed, _log_file, _current_log_path, _original_stdout, _original_stderr

    with _lock:
        if not _installed:
            return

        if _original_stdout is not None:
            sys.stdout = _original_stdout
            _original_stdout = None

        if _original_stderr is not None:
            sys.stderr = _original_stderr
            _original_stderr = None

        if _log_file is not None and not _log_file.closed:
            try:
                now = datetime.datetime.now()
                footer = (
                    f"\n================================================================================\n"
                    f"Aster Console Log Ended: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"================================================================================\n"
                )
                _log_file.write(footer)
                _log_file.flush()
                _log_file.close()
            except Exception:
                pass
            _log_file = None

        _installed = False
        _current_log_path = None


def get_current_log_path() -> str | None:
    """Return the absolute path of the active log file, or None if not installed."""
    with _lock:
        return _current_log_path


def is_installed() -> bool:
    """Return True if the console tee logger is active."""
    with _lock:
        return _installed
