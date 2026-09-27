"""llama-server health watchdog (reliability campaign round 2).

A daemon thread pings http://localhost:8080/health every CHECK_INTERVAL
seconds. Three consecutive failures declare the server down: a Telegram alert
goes out (tools.diagnostics.send_alert) and — when
config.HEALTH_WATCHDOG_AUTORESTART is on — start.bat is relaunched in a new
console, reusing the exact user-maintained launch flags with zero duplication.

Guardrails:
  - HTTP 503 means the model is still loading — "starting, not dead". The
    failure counter is left alone and no restart fires.
  - Restart cap: MAX_RESTARTS_PER_HOUR per rolling hour. Beyond the cap the
    watchdog goes alert-only ("crash-looping") and backs off to one alert per
    hour so a genuinely broken install doesn't spam Telegram or spawn consoles.
  - First check is delayed STARTUP_GRACE_SECONDS so main.py's own engine
    warmup finishes before the watchdog starts judging.

Start via main.py:  threading.Thread(target=health_watchdog_daemon,
                                     daemon=True, name="health-watchdog").start()
"""
import os
import subprocess
import time

import requests

import config
from core import instrumentation

HEALTH_URL = "http://localhost:8080/health"
CHECK_INTERVAL = 30.0          # seconds between polls
PROBE_TIMEOUT = 5.0            # per-request timeout
FAILURES_TO_DECLARE_DOWN = 3   # consecutive failures before acting
STARTUP_GRACE_SECONDS = 60.0   # let main.py's warmup finish first
MAX_RESTARTS_PER_HOUR = 2      # rolling-hour crash-loop cap
RESTART_WAIT_SECONDS = 300.0   # max wait for the model to come back up
ALERT_BACKOFF_SECONDS = 3600.0  # alert-only cadence once the cap is hit

START_BAT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "start.bat")

# Module state (single daemon thread — no locking needed).
_consecutive_failures = 0
_restart_times: list[float] = []   # monotonic timestamps of restart attempts
_last_capped_alert = 0.0


def _probe() -> str:
    """One /health probe → 'ok' | 'loading' | 'down'."""
    try:
        resp = requests.get(HEALTH_URL, timeout=PROBE_TIMEOUT)
        if resp.status_code == 200:
            return "ok"
        if resp.status_code == 503:
            return "loading"
        return "down"
    except Exception:
        return "down"


def _alert(text: str) -> None:
    print(f"[Aster Watchdog] {text}")
    try:
        from tools.diagnostics import send_alert
        send_alert("llama-server watchdog", text)
    except Exception:
        pass


def _restart_allowed(now: float) -> bool:
    """Prune the rolling window and check the crash-loop cap."""
    _restart_times[:] = [t for t in _restart_times if now - t < 3600.0]
    return len(_restart_times) < MAX_RESTARTS_PER_HOUR


def _launch_server() -> bool:
    """Relaunch start.bat in a new console. Returns False if the spawn failed."""
    if not os.path.exists(START_BAT):
        _alert(f"Cannot auto-restart: {START_BAT} not found.")
        return False
    try:
        # "start" gives the server its own console window, exactly like a
        # manual launch — the flags live only in start.bat, never duplicated.
        subprocess.Popen(["cmd", "/c", "start", "", START_BAT], cwd=os.path.dirname(START_BAT))
        return True
    except Exception as e:
        _alert(f"Auto-restart spawn failed: {e}")
        return False


def _wait_for_recovery(deadline_s: float = RESTART_WAIT_SECONDS) -> bool:
    """Poll /health until 200 or the deadline passes (model load takes a while)."""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if _probe() == "ok":
            return True
        time.sleep(5.0)
    return False


def _handle_down() -> None:
    """Declared down: alert, then optionally restart (cap-gated)."""
    global _last_capped_alert
    now = time.monotonic()

    instrumentation.record_event("llm_server_down", consecutive_failures=_consecutive_failures)

    if not config.HEALTH_WATCHDOG_AUTORESTART:
        _alert("llama-server is DOWN (3 consecutive /health failures). "
               "Auto-restart is disabled — please restart it manually (start.bat).")
        return

    if not _restart_allowed(now):
        # `_last_capped_alert <= 0` means "never alerted": the sentinel is 0.0
        # but `now` is time.monotonic(), which is < ALERT_BACKOFF_SECONDS on a
        # freshly booted machine — without this guard the very first crash-loop
        # alert would be silently suppressed for the first hour of uptime.
        if _last_capped_alert <= 0.0 or now - _last_capped_alert >= ALERT_BACKOFF_SECONDS:
            _last_capped_alert = now
            _alert(f"llama-server is DOWN and crash-looping ({MAX_RESTARTS_PER_HOUR} "
                   f"restarts in the last hour already). Backing off — manual intervention needed.")
        return

    _alert("llama-server is DOWN (3 consecutive /health failures). Attempting auto-restart...")
    _restart_times.append(now)
    instrumentation.record_event("llm_server_restart_attempt", attempt_in_window=len(_restart_times))

    if not _launch_server():
        return

    if _wait_for_recovery():
        _alert("llama-server is back up. Auto-restart succeeded.")
        instrumentation.record_event("llm_server_restart_ok")
    else:
        _alert(f"Auto-restart launched but the server did not report healthy within "
               f"{int(RESTART_WAIT_SECONDS)}s. Check the llama-server console.")
        instrumentation.record_event("llm_server_restart_timeout")


def check_once() -> str:
    """One watchdog tick (probe + state machine). Returns the probe state —
    split out from the sleep loop so tests can drive it directly."""
    global _consecutive_failures
    state = _probe()
    if state == "ok":
        _consecutive_failures = 0
    elif state == "loading":
        # Model loading: not dead. Hold the counter, never restart into it.
        pass
    else:
        _consecutive_failures += 1
        if _consecutive_failures == FAILURES_TO_DECLARE_DOWN:
            _handle_down()
        elif _consecutive_failures > FAILURES_TO_DECLARE_DOWN:
            # Still down after acting — re-run the (cap-gated) handler each
            # further FAILURES_TO_DECLARE_DOWN misses so recovery is retried
            # without alert-spamming every 30s.
            if _consecutive_failures % FAILURES_TO_DECLARE_DOWN == 0:
                _handle_down()
    return state


def health_watchdog_daemon() -> None:
    """Daemon entry point — start with threading.Thread(daemon=True)."""
    if not config.HEALTH_WATCHDOG_ENABLED:
        print("[Aster Watchdog] Disabled via daemons.health_watchdog — thread exiting.")
        return
    print(f"[Aster Watchdog] llama-server health watchdog active "
          f"(every {int(CHECK_INTERVAL)}s, autorestart={'on' if config.HEALTH_WATCHDOG_AUTORESTART else 'off'}).")
    time.sleep(STARTUP_GRACE_SECONDS)
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"[Aster Watchdog] tick error (ignored): {e}")
        time.sleep(CHECK_INTERVAL)
