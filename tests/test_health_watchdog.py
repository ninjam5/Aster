"""Tests for the llama-server health watchdog (tools/health_watchdog.py).

check_once() is driven directly (no sleep loop); requests.get, subprocess
launch, recovery wait, and the Telegram alert are all mocked.
"""
from unittest.mock import MagicMock, patch

import pytest

import config
import tools.health_watchdog as wd


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HEALTH_WATCHDOG_ENABLED", True)
    monkeypatch.setattr(config, "HEALTH_WATCHDOG_AUTORESTART", True)
    monkeypatch.setattr(config, "RELIABILITY_LOG_ENABLED", True)
    monkeypatch.setattr(config, "RELIABILITY_LOG_PATH", str(tmp_path / "rel.jsonl"))
    wd._consecutive_failures = 0
    wd._restart_times.clear()
    wd._last_capped_alert = 0.0
    yield
    wd._consecutive_failures = 0
    wd._restart_times.clear()
    wd._last_capped_alert = 0.0


def _resp(status):
    r = MagicMock()
    r.status_code = status
    return r


def _tick(status):
    """Run one check_once with /health returning the given HTTP status
    (or raising on 'conn_error')."""
    if status == "conn_error":
        cm = patch("tools.health_watchdog.requests.get", side_effect=ConnectionError("refused"))
    else:
        cm = patch("tools.health_watchdog.requests.get", return_value=_resp(status))
    with cm:
        return wd.check_once()


# ── probe states ─────────────────────────────────────────────────────────────

def test_healthy_resets_counter():
    wd._consecutive_failures = 2
    assert _tick(200) == "ok"
    assert wd._consecutive_failures == 0


def test_503_is_loading_not_down():
    with patch.object(wd, "_handle_down") as handler:
        for _ in range(10):
            assert _tick(503) == "loading"
    handler.assert_not_called()
    assert wd._consecutive_failures == 0


def test_two_failures_do_not_declare_down():
    with patch.object(wd, "_handle_down") as handler:
        _tick("conn_error")
        _tick("conn_error")
    handler.assert_not_called()


def test_third_consecutive_failure_declares_down():
    with patch.object(wd, "_handle_down") as handler:
        _tick("conn_error")
        _tick("conn_error")
        _tick("conn_error")
    handler.assert_called_once()


def test_recovery_between_failures_resets():
    with patch.object(wd, "_handle_down") as handler:
        _tick("conn_error")
        _tick("conn_error")
        _tick(200)
        _tick("conn_error")
        _tick("conn_error")
    handler.assert_not_called()


# ── down handling: restart + cap ─────────────────────────────────────────────

def test_down_alerts_and_restarts():
    with patch.object(wd, "_alert") as alert, \
         patch.object(wd, "_launch_server", return_value=True) as launch, \
         patch.object(wd, "_wait_for_recovery", return_value=True):
        wd._consecutive_failures = wd.FAILURES_TO_DECLARE_DOWN
        wd._handle_down()
    launch.assert_called_once()
    assert any("DOWN" in str(c.args[0]) for c in alert.call_args_list)
    assert any("back up" in str(c.args[0]) for c in alert.call_args_list)
    assert len(wd._restart_times) == 1


def test_restart_timeout_alerts_without_success_message():
    with patch.object(wd, "_alert") as alert, \
         patch.object(wd, "_launch_server", return_value=True), \
         patch.object(wd, "_wait_for_recovery", return_value=False):
        wd._handle_down()
    assert any("did not report healthy" in str(c.args[0]) for c in alert.call_args_list)
    assert not any("back up" in str(c.args[0]) for c in alert.call_args_list)


def test_hourly_cap_blocks_third_restart():
    with patch.object(wd, "_alert") as alert, \
         patch.object(wd, "_launch_server", return_value=True) as launch, \
         patch.object(wd, "_wait_for_recovery", return_value=False):
        wd._handle_down()
        wd._handle_down()
        wd._handle_down()  # third within the hour → capped
    assert launch.call_count == 2
    assert any("crash-looping" in str(c.args[0]) for c in alert.call_args_list)


def test_capped_alert_backs_off():
    with patch.object(wd, "_alert") as alert, \
         patch.object(wd, "_launch_server", return_value=True), \
         patch.object(wd, "_wait_for_recovery", return_value=False):
        wd._handle_down()
        wd._handle_down()
        alert.reset_mock()
        wd._handle_down()   # capped → one crash-loop alert
        wd._handle_down()   # still within backoff → silent
        wd._handle_down()
    crashloop_alerts = [c for c in alert.call_args_list if "crash-looping" in str(c.args[0])]
    assert len(crashloop_alerts) == 1


def test_autorestart_off_is_alert_only(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_WATCHDOG_AUTORESTART", False)
    with patch.object(wd, "_alert") as alert, \
         patch.object(wd, "_launch_server") as launch:
        wd._handle_down()
    launch.assert_not_called()
    assert any("restart it manually" in str(c.args[0]) for c in alert.call_args_list)


def test_daemon_exits_immediately_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_WATCHDOG_ENABLED", False)
    with patch("tools.health_watchdog.time.sleep") as sleeper:
        wd.health_watchdog_daemon()   # must return, not loop
    sleeper.assert_not_called()


def test_start_bat_path_points_at_repo_root():
    import os
    assert os.path.basename(wd.START_BAT) == "start.bat"
    assert os.path.exists(wd.START_BAT)
