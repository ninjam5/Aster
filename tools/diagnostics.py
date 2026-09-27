import html
import os
import queue
import sys
import threading
import time

_send_queue: queue.Queue = queue.Queue()
_reentrancy = threading.local()   # per-thread guard — prevents send_message print from re-queuing

# send_error is unconditional (by design), so a crash loop can spam Telegram with
# the identical traceback. Suppress a repeated (context, error) within this window
# and never fire from inside a test run.
_ERROR_DEDUPE_SECONDS = 300.0
_recent_errors: dict = {}
_error_lock = threading.Lock()


def _suppressed_in_tests() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


class DiagnosticsStream:
    """sys.stdout shim: always mirrors to the real terminal; when DIAGNOSTICS_MODE is on, queues lines for Telegram."""

    def __init__(self, original):
        self._original = original
        self._pending = ""
        self._lock = threading.Lock()

    def write(self, text):
        self._original.write(text)
        if getattr(_reentrancy, "active", False):
            return
        import config  # lazy — avoids circular import at module load time
        with self._lock:
            self._pending += text
            while "\n" in self._pending:
                line, self._pending = self._pending.split("\n", 1)
                if line.strip():
                    if config.DIAGNOSTICS_MODE:
                        _send_queue.put(line)
                    # Always feed the desktop UI log strip, independent of DIAGNOSTICS_MODE.
                    try:
                        import tools.realtime_stream as _rt
                        _rt.publish_terminal(line)
                    except Exception:
                        pass

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _sender_loop():
    batch: list[str] = []
    last_flush = time.time()

    while True:
        try:
            line = _send_queue.get(timeout=1.5)
            batch.append(line)
        except queue.Empty:
            pass

        now = time.time()
        if batch and (now - last_flush >= 2.0 or len(batch) >= 25):
            import config
            if config.bot and config.DIAGNOSTICS_MODE:
                text = "\n".join(batch)
                if len(text) > 3800:
                    text = "...(truncated)\n" + text[-3800:]
                msg = f"<pre>{html.escape(text)}</pre>"
                _reentrancy.active = True
                try:
                    config.bot.send_message(
                        config.AUTHORIZED_CHAT_ID,
                        msg,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                finally:
                    _reentrancy.active = False
            batch.clear()
            last_flush = now


def send_error(context: str, exc: Exception) -> None:
    """Forward a caught exception to Telegram unconditionally — not gated on DIAGNOSTICS_MODE.

    Call this from top-level exception handlers so critical failures always surface
    in the Telegram chat even when diagnostics is off. Identical errors are
    deduplicated within _ERROR_DEDUPE_SECONDS (a crash loop must not spam), and
    this is a hard no-op during a pytest run.
    """
    import config
    if not config.bot or _suppressed_in_tests():
        return
    import traceback as _tb
    tb = _tb.format_exc()
    if len(tb) > 2000:
        tb = "...(truncated)\n" + tb[-2000:]

    stripped = tb.strip().splitlines()
    signature = (context, stripped[-1] if stripped else "")
    now = time.time()
    with _error_lock:
        last = _recent_errors.get(signature, 0.0)
        if now - last < _ERROR_DEDUPE_SECONDS:
            return
        _recent_errors[signature] = now
        if len(_recent_errors) > 200:  # prune ancient entries
            for key in [k for k, ts in _recent_errors.items()
                        if now - ts >= _ERROR_DEDUPE_SECONDS]:
                _recent_errors.pop(key, None)

    msg = f"<b>[Aster Error — {html.escape(context)}]</b>\n<pre>{html.escape(tb)}</pre>"
    _reentrancy.active = True
    try:
        config.bot.send_message(config.AUTHORIZED_CHAT_ID, msg, parse_mode="HTML")
    except Exception:
        pass
    finally:
        _reentrancy.active = False


def send_alert(context: str, text: str) -> None:
    """Forward a plain-text alert to Telegram unconditionally — not gated on
    DIAGNOSTICS_MODE. The message-only sibling of send_error for events that
    are not exceptions (e.g. the llama-server health watchdog)."""
    import config
    if not config.bot or _suppressed_in_tests():
        return
    body = str(text)
    if len(body) > 2000:
        body = "...(truncated)\n" + body[-2000:]
    msg = f"<b>[Aster Alert — {html.escape(context)}]</b>\n{html.escape(body)}"
    _reentrancy.active = True
    try:
        config.bot.send_message(config.AUTHORIZED_CHAT_ID, msg, parse_mode="HTML")
    except Exception:
        pass
    finally:
        _reentrancy.active = False


def install():
    """Replace sys.stdout with DiagnosticsStream and start the background sender thread."""
    sys.stdout = DiagnosticsStream(sys.stdout)
    threading.Thread(target=_sender_loop, daemon=True, name="DiagnosticsSender").start()
