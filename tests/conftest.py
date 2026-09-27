"""Shared pytest fixtures.

Forces the automation flags to their safe/default state for every test so the
suite is deterministic regardless of what the owner has set in the live
`self_config.yaml`. Without this, enabling `automation.laya_kernel` in the live
config would make unit tests load (or try to load) the Laya checkpoint.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _force_automation_defaults(monkeypatch):
    import config
    monkeypatch.setattr(config, "USE_DOM_MOTOR", False, raising=False)
    monkeypatch.setattr(config, "USE_LAYA_KERNEL", False, raising=False)
    monkeypatch.setattr(config, "USE_PIXEL_FALLBACK", True, raising=False)
    monkeypatch.setattr(config, "BROWSER_USE_REAL_PROFILE", False, raising=False)
    monkeypatch.setattr(config, "BROWSER_HUMAN_SEARCH", False, raising=False)
    # Pin every automation knob tests assert on, so the suite is independent of
    # whatever the owner has tuned in self_config.yaml.
    monkeypatch.setattr(config, "DOM_MOTOR_SEND_POLICY", "confirm", raising=False)
    monkeypatch.setattr(config, "DOM_MOTOR_SHORTLIST_K", 18, raising=False)
    monkeypatch.setattr(config, "DOM_MOTOR_MIN_SCORE", 0.55, raising=False)
    monkeypatch.setattr(config, "LAYA_MARGIN_THRESHOLD", 0.25, raising=False)
    monkeypatch.setattr(config, "LAYA_KEEP_RESIDENT", False, raising=False)
    # Never let a test reach the real Telegram alert channel. diagnostics
    # send_error/send_alert early-return when there is no bot, and the
    # PYTEST_CURRENT_TEST guard in diagnostics is a second line of defense.
    monkeypatch.setattr(config, "bot", None, raising=False)
    monkeypatch.setattr(config, "DIAGNOSTICS_MODE", False, raising=False)
