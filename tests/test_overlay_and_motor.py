"""Tests for Clicky Companion Overlay, Humanized Motor, and Assist Mode vs Drive Mode.

Covers:
1. Humanized Motor (tools/motor.py):
   - ease_out_cubic curve math, boundary conditions, and deceleration.
   - bezier_curve_points trajectory geometry and perpendicular arc offset.
   - human_move_to with SMOOTH_MOUSE_ENABLED=True vs False.
   - should_use_cadence_typing heuristics (length, newlines, ASCII, config toggle).
   - human_type cadence vs clipboard branches, enter pressing.
2. Overlay Controller (tools/overlay.py):
   - Dynamic Island state and text updates.
   - Assist Mode badge toggling.
   - Ghost Cursor coordinate positioning.
   - Laser highlight normalization, Ghost Cursor auto-targeting, and clearing.
   - Target lock-on pulse recording.
3. Assist Mode vs Drive Mode (core/brain.py):
   - toggle_assist_mode tool execution.
   - smart_click in Assist Mode (points/highlights, NEVER clicks).
   - smart_type in Assist Mode (points/highlights, NEVER clicks or types).
   - type_text and press_key in Assist Mode (no physical keystrokes).
   - smart_click and smart_type in Drive Mode (executes via motor).
4. QML File Validation:
   - tools/overlay.qml structure, Jarvis Cyan palette (#00F0FF),
     Top Dynamic Island with auto-hide logic, Ghost Cursor, and Highlight Repeater.
"""

from __future__ import annotations

import math
import os
from typing import Any
import pytest


# ============================================================================
# 1. HUMANIZED MOTOR TESTS (tools/motor.py)
# ============================================================================

class TestHumanizedMotor:
    """Unit tests for tools/motor.py easing, trajectory generation, and typing."""

    def test_ease_out_cubic_math(self):
        """ease_out_cubic starts at 0.0, ends at 1.0, and exhibits deceleration."""
        from tools.motor import ease_out_cubic

        assert ease_out_cubic(0.0) == pytest.approx(0.0)
        assert ease_out_cubic(1.0) == pytest.approx(1.0)

        # Deceleration property: at mid-point t=0.5, ease_out is already past halfway (> 0.5)
        mid = ease_out_cubic(0.5)
        assert mid > 0.5
        assert mid == pytest.approx(0.875)

        # Monotonically increasing check across [0, 1]
        steps = [i / 20.0 for i in range(21)]
        values = [ease_out_cubic(t) for t in steps]
        for i in range(len(values) - 1):
            assert values[i + 1] > values[i]

        # Deceleration check: step deltas strictly decrease as t approaches 1.0
        deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
        for i in range(len(deltas) - 1):
            assert deltas[i + 1] < deltas[i]

    def test_bezier_curve_points_endpoints_and_arc_offset(self):
        """bezier_curve_points ends at the exact target and exhibits a non-zero perpendicular arc."""
        from tools.motor import bezier_curve_points

        start = (100, 100)
        end = (500, 400)
        points = bezier_curve_points(start, end, steps=20)

        assert len(points) >= 20
        # Must end at the exact target coordinate
        assert points[-1] == (500, 400)

        # Verify perpendicular arc offset (not colinear with the straight chord).
        # Straight chord line from (100, 100) to (500, 400):
        # Direction vector D = (400, 300).
        # Perpendicular distance/cross product: (px - 100)*300 - (py - 100)*400
        cross_products = [
            abs((px - 100) * 300 - (py - 100) * 400)
            for px, py in points[1:-1]
        ]
        assert any(cp > 50 for cp in cross_products), (
            "Trajectory points must deviate from a straight mechanical line"
        )

    def test_human_move_to_smooth_enabled(self, monkeypatch):
        """When SMOOTH_MOUSE_ENABLED=True, cursor follows a multi-step trajectory."""
        import tools.motor as motor
        import pyautogui

        moved_coords: list[tuple[int, int]] = []
        sleep_durations: list[float] = []

        monkeypatch.setattr(motor.config, "SMOOTH_MOUSE_ENABLED", True)
        monkeypatch.setattr(pyautogui, "position", lambda: (100, 100))
        monkeypatch.setattr(
            pyautogui,
            "moveTo",
            lambda x, y, **kwargs: moved_coords.append((int(x), int(y))),
        )
        monkeypatch.setattr("time.sleep", lambda s: sleep_durations.append(s))

        motor.human_move_to(300, 200)

        # Smooth motion should perform multiple intermediate waypoint moves
        assert len(moved_coords) >= 10
        assert moved_coords[-1] == (300, 200)
        assert len(sleep_durations) > 0
        assert sum(sleep_durations) > 0.0

    def test_human_move_to_smooth_disabled(self, monkeypatch):
        """When SMOOTH_MOUSE_ENABLED=False, moveTo is called directly without waypoints."""
        import tools.motor as motor
        import pyautogui

        moved_coords: list[tuple[int, int]] = []
        sleep_durations: list[float] = []

        monkeypatch.setattr(motor.config, "SMOOTH_MOUSE_ENABLED", False)
        monkeypatch.setattr(pyautogui, "position", lambda: (100, 100))
        monkeypatch.setattr(
            pyautogui,
            "moveTo",
            lambda x, y, **kwargs: moved_coords.append((int(x), int(y))),
        )
        monkeypatch.setattr("time.sleep", lambda s: sleep_durations.append(s))

        motor.human_move_to(300, 200)

        # Straight instantaneous move
        assert moved_coords == [(300, 200)]
        assert len(sleep_durations) == 0

    def test_should_use_cadence_typing_predicates(self, monkeypatch):
        """should_use_cadence_typing validates string length, newlines, ASCII, and config."""
        import tools.motor as motor

        monkeypatch.setattr(motor.config, "HUMAN_TYPING_ENABLED", True)
        monkeypatch.setattr(motor.config, "HUMAN_TYPING_MAX_CHARS", 80)

        # True for short single-line ASCII
        assert motor.should_use_cadence_typing("hello world") is True
        assert motor.should_use_cadence_typing("search query") is True
        assert motor.should_use_cadence_typing("a" * 80) is True

        # False when HUMAN_TYPING_ENABLED=False
        monkeypatch.setattr(motor.config, "HUMAN_TYPING_ENABLED", False)
        assert motor.should_use_cadence_typing("hello world") is False

        # Reset flag
        monkeypatch.setattr(motor.config, "HUMAN_TYPING_ENABLED", True)

        # False when text exceeds max chars
        assert motor.should_use_cadence_typing("a" * 81) is False

        # False when newline present
        assert motor.should_use_cadence_typing("hello\nworld") is False
        assert motor.should_use_cadence_typing("hello\rworld") is False

        # False when non-ASCII or emoji present
        assert motor.should_use_cadence_typing("Hello 👋") is False
        assert motor.should_use_cadence_typing("café") is False
        assert motor.should_use_cadence_typing("") is False

    def test_human_type_cadence_path(self, monkeypatch):
        """human_type uses character cadence for eligible ASCII strings."""
        import tools.motor as motor
        import pyautogui

        typed_chars: list[str] = []
        clipboard_copies: list[str] = []

        monkeypatch.setattr(motor.config, "HUMAN_TYPING_ENABLED", True)
        monkeypatch.setattr(pyautogui, "write", lambda ch, **kw: typed_chars.append(ch))
        monkeypatch.setattr("pyperclip.copy", lambda txt: clipboard_copies.append(txt))
        monkeypatch.setattr("time.sleep", lambda s: None)

        method = motor.human_type("hello")

        assert method == "cadence"
        assert "".join(typed_chars) == "hello"
        assert len(clipboard_copies) == 0

    def test_human_type_clipboard_path_and_enter(self, monkeypatch):
        """human_type falls back to clipboard paste for long/multiline text, and presses Enter."""
        import tools.motor as motor
        import pyautogui

        typed_chars: list[str] = []
        clipboard_copies: list[str] = []
        hotkeys: list[tuple] = []
        pressed_keys: list[str] = []

        monkeypatch.setattr(motor.config, "HUMAN_TYPING_ENABLED", True)
        monkeypatch.setattr(pyautogui, "write", lambda ch, **kw: typed_chars.append(ch))
        monkeypatch.setattr("pyperclip.copy", lambda txt: clipboard_copies.append(txt))
        monkeypatch.setattr(pyautogui, "hotkey", lambda *keys: hotkeys.append(keys))
        monkeypatch.setattr(pyautogui, "press", lambda key: pressed_keys.append(key))
        monkeypatch.setattr("time.sleep", lambda s: None)

        long_multiline = "Line 1\nLine 2 with emoji 🎉 and lots of characters " + ("x" * 100)
        method = motor.human_type(long_multiline, press_enter=True)

        assert method == "clipboard"
        assert len(typed_chars) == 0
        assert clipboard_copies == [long_multiline]
        assert ("ctrl", "v") in hotkeys
        assert "enter" in pressed_keys

    def test_human_click_triggers_pulse_and_move(self, monkeypatch):
        """human_click moves smoothly, triggers overlay pulse, and clicks."""
        import tools.motor as motor
        import pyautogui

        clicked: list[tuple[int, int, str]] = []
        pulses: list[tuple[int, int, str]] = []

        monkeypatch.setattr(motor, "human_move_to", lambda x, y: None)
        monkeypatch.setattr(
            pyautogui,
            "click",
            lambda x, y, clicks=1, button="left": clicked.append((x, y, button)),
        )

        import tools.overlay as overlay
        monkeypatch.setattr(overlay, "pulse_at", lambda x, y, label="": pulses.append((x, y, label)))

        motor.human_click(250, 350, clicks=1, button="left", label="Search Button")

        assert clicked == [(250, 350, "left")]
        assert pulses == [(250, 350, "Search Button")]


# ============================================================================
# 2. OVERLAY CONTROLLER TESTS (tools/overlay.py)
# ============================================================================

class TestOverlayController:
    """Unit tests for tools/overlay.py in-memory state tracking and helpers."""

    def test_set_island_state_updates_overlay_state(self):
        """Dynamic island state updates reflect accurately in get_overlay_state()."""
        import tools.overlay as overlay

        overlay.set_island_state("listening", "Listening...")
        state = overlay.get_overlay_state()
        assert state["island_state"] == "listening"
        assert state["island_text"] == "Listening..."

        overlay.set_island_state("thinking", "Thinking...")
        state = overlay.get_overlay_state()
        assert state["island_state"] == "thinking"
        assert state["island_text"] == "Thinking..."

        overlay.set_island_state("locating", "Locating 'Settings'...")
        state = overlay.get_overlay_state()
        assert state["island_state"] == "locating"
        assert state["island_text"] == "Locating 'Settings'..."

        overlay.set_island_state("idle", "Aster")
        state = overlay.get_overlay_state()
        assert state["island_state"] == "idle"
        assert state["island_text"] == "Aster"

    def test_set_assist_mode_badge(self):
        """set_assist_mode_badge updates the assist_mode flag in overlay state."""
        import tools.overlay as overlay

        overlay.set_assist_mode_badge(True)
        assert overlay.get_overlay_state()["assist_mode"] is True

        overlay.set_assist_mode_badge(False)
        assert overlay.get_overlay_state()["assist_mode"] is False

    def test_move_ghost_cursor(self):
        """move_ghost_cursor sets ghost_pos coordinates and label."""
        import tools.overlay as overlay

        overlay.move_ghost_cursor(420, 310, label="Settings")
        state = overlay.get_overlay_state()
        assert state["ghost_pos"] == (420, 310)
        assert state["ghost_label"] == "Settings"

    def test_show_highlights_populates_active_and_moves_ghost(self):
        """show_highlights normalizes bounding boxes and moves Ghost Cursor to box center."""
        import tools.overlay as overlay

        # Box format: (left, top, width, height) = (100, 200, 80, 40)
        # Center: cx = 100 + 80 // 2 = 140, cy = 200 + 40 // 2 = 220
        overlay.show_highlights(
            [{"box": (100, 200, 80, 40), "text": "Send"}],
            duration=5.0,
            move_ghost=True,
        )

        state = overlay.get_overlay_state()
        assert len(state["active_highlights"]) == 1
        hl = state["active_highlights"][0]
        assert hl["x"] == 100
        assert hl["y"] == 200
        assert hl["width"] == 80
        assert hl["height"] == 40
        assert hl["label"] == "Send"

        # Ghost Cursor should have moved to box center
        assert state["ghost_pos"] == (140, 220)

    def test_clear_highlights(self):
        """clear_highlights empties active_highlights."""
        import tools.overlay as overlay

        overlay.show_highlights([{"box": (10, 20, 30, 40), "text": "Item"}])
        assert len(overlay.get_overlay_state()["active_highlights"]) > 0

        overlay.clear_highlights()
        assert len(overlay.get_overlay_state()["active_highlights"]) == 0

    def test_pulse_at_records_last_pulse(self):
        """pulse_at records coordinate and label in last_pulse."""
        import tools.overlay as overlay

        overlay.pulse_at(250, 350, label="Submit")
        state = overlay.get_overlay_state()
        assert state["last_pulse"] == (250, 350, "Submit")


# ============================================================================
# 3. ASSIST MODE VS DRIVE MODE TESTS (core/brain.py)
# ============================================================================

class TestAssistModeVsDriveMode:
    """Tests for Assist Mode vs Drive Mode tool dispatch in core/brain.py."""

    def test_toggle_assist_mode_tool(self):
        """execute_tool('toggle_assist_mode') toggles config.ASSIST_MODE and returns confirmation."""
        import core.brain as brain
        import config

        res_on = brain.execute_tool("toggle_assist_mode", {"state": True})
        assert config.ASSIST_MODE is True
        res_text_on = res_on["text"] if isinstance(res_on, dict) else str(res_on)
        assert "ENABLED" in res_text_on.upper() or "ACTIVE" in res_text_on.upper()

        res_off = brain.execute_tool("toggle_assist_mode", {"state": False})
        assert config.ASSIST_MODE is False
        res_text_off = res_off["text"] if isinstance(res_off, dict) else str(res_off)
        assert "DISABLED" in res_text_off.upper() or "INACTIVE" in res_text_off.upper()

    def test_assist_mode_smart_click_never_clicks(self, monkeypatch):
        """When ASSIST_MODE=True, smart_click highlights the target and NEVER clicks pyautogui or human_click."""
        import core.brain as brain
        import config
        import pyautogui
        import tools.motor as motor
        import tools.overlay as overlay

        clicked: list[Any] = []
        motor_clicked: list[Any] = []
        highlights_shown: list[Any] = []

        config.ASSIST_MODE = True

        monkeypatch.setattr(
            brain,
            "locate_ui_element_ex",
            lambda goal: {
                "source": "qwen-vision",
                "text": goal,
                "score": 0.95,
                "x": 320,
                "y": 240,
                "box": (280, 220, 80, 40),
            },
        )
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Chrome")
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "B64_MOCK")
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None)
        monkeypatch.setattr(brain, "capture_screen_small_gray", lambda: b"gray")
        monkeypatch.setattr(brain, "screens_differ", lambda a, b: False)
        monkeypatch.setattr(pyautogui, "click", lambda *a, **kw: clicked.append((a, kw)))
        monkeypatch.setattr(motor, "human_click", lambda *a, **kw: motor_clicked.append((a, kw)))
        monkeypatch.setattr(
            overlay,
            "show_highlights",
            lambda boxes, **kw: highlights_shown.append((boxes, kw)),
        )

        try:
            res = brain.execute_tool("smart_click", {"goal": "Search button"})
            res_text = res["text"] if isinstance(res, dict) else str(res)

            # CRITICAL ASSERTIONS: Neither pyautogui.click nor motor.human_click must EVER be called in Assist Mode!
            assert len(clicked) == 0, "pyautogui.click must NEVER be called in Assist Mode!"
            assert len(motor_clicked) == 0, "motor.human_click must NEVER be called in Assist Mode!"
            assert len(highlights_shown) > 0, "overlay.show_highlights must be called in Assist Mode!"
            assert highlights_shown[0][1].get("move_ghost") is True, "show_highlights must be called with move_ghost=True"
            assert "Assist Mode" in res_text or "Pointed" in res_text or "Highlighted" in res_text
        finally:
            config.ASSIST_MODE = False

    def test_assist_mode_smart_type_never_clicks_or_pastes(self, monkeypatch):
        """When ASSIST_MODE=True, smart_type highlights the target and NEVER clicks or types."""
        import core.brain as brain
        import config
        import pyautogui
        import pyperclip
        import tools.overlay as overlay

        clicked: list[Any] = []
        copied_text: list[str] = []
        hotkeys: list[tuple] = []
        pressed_keys: list[str] = []
        highlights_shown: list[Any] = []

        config.ASSIST_MODE = True

        monkeypatch.setattr(
            brain,
            "locate_ui_element_ex",
            lambda goal: {
                "source": "uia",
                "text": goal,
                "score": 0.92,
                "x": 400,
                "y": 150,
                "box": (300, 130, 200, 40),
            },
        )
        monkeypatch.setattr(brain, "focused_control_type", lambda: "EditControl")
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Browser Window")
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "B64_MOCK")
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None)
        monkeypatch.setattr(brain, "capture_screen_small_gray", lambda: b"gray")
        monkeypatch.setattr(brain, "screens_differ", lambda a, b: False)
        monkeypatch.setattr(pyautogui, "click", lambda *a, **kw: clicked.append((a, kw)))
        monkeypatch.setattr(pyperclip, "copy", lambda txt: copied_text.append(txt))
        monkeypatch.setattr(pyautogui, "hotkey", lambda *keys: hotkeys.append(keys))
        monkeypatch.setattr(pyautogui, "press", lambda key: pressed_keys.append(key))
        monkeypatch.setattr(
            overlay,
            "show_highlights",
            lambda boxes, **kw: highlights_shown.append((boxes, kw)),
        )

        try:
            res = brain.execute_tool(
                "smart_type",
                {"goal": "Search field", "text": "Jarvis query", "press_enter": True},
            )
            res_text = res["text"] if isinstance(res, dict) else str(res)

            # CRITICAL ASSERTIONS: Neither clicks, clipboard copies, nor keystrokes emitted
            assert len(clicked) == 0, "pyautogui.click must NEVER be called during smart_type in Assist Mode!"
            assert len(copied_text) == 0, "pyperclip.copy must NEVER be called during smart_type in Assist Mode!"
            assert ("ctrl", "v") not in hotkeys, "Paste hotkey must NEVER be called in Assist Mode!"
            assert "enter" not in pressed_keys, "Enter key must NEVER be pressed in Assist Mode!"
            assert len(highlights_shown) > 0, "overlay.show_highlights must be called in Assist Mode!"
            assert "Assist Mode" in res_text or "Pointed" in res_text or "Highlighted" in res_text
        finally:
            config.ASSIST_MODE = False

    def test_assist_mode_type_text_and_press_key_no_keystrokes(self, monkeypatch):
        """When ASSIST_MODE=True, type_text and press_key do not emit physical keystrokes."""
        import core.brain as brain
        import config
        import pyautogui
        import pyperclip

        copied_text: list[str] = []
        pressed_keys: list[str] = []
        hotkeys: list[tuple] = []

        config.ASSIST_MODE = True

        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Active App")
        monkeypatch.setattr(brain, "focused_control_type", lambda: "EditControl")
        monkeypatch.setattr(pyperclip, "copy", lambda txt: copied_text.append(txt))
        monkeypatch.setattr(pyautogui, "press", lambda key: pressed_keys.append(key))
        monkeypatch.setattr(pyautogui, "hotkey", lambda *keys: hotkeys.append(keys))

        try:
            res_type = brain.execute_tool("type_text", {"text": "secret password"})
            res_type_text = res_type["text"] if isinstance(res_type, dict) else str(res_type)
            assert len(copied_text) == 0
            assert ("ctrl", "v") not in hotkeys
            assert "Assist Mode" in res_type_text or "Blocked" in res_type_text or "simulated" in res_type_text.lower()

            res_press = brain.execute_tool("press_key", {"key": "enter"})
            res_press_text = res_press["text"] if isinstance(res_press, dict) else str(res_press)
            assert "enter" not in pressed_keys
            assert "Assist Mode" in res_press_text or "Blocked" in res_press_text or "simulated" in res_press_text.lower()
        finally:
            config.ASSIST_MODE = False

    def test_assist_mode_press_key_window_manipulation_blocked(self, monkeypatch):
        """When ASSIST_MODE=True, window manipulation shortcuts (win+d, win+down, alt+space) are explicitly blocked."""
        import core.brain as brain
        import config
        import pyautogui

        pressed_keys: list[str] = []
        hotkeys: list[tuple] = []
        config.ASSIST_MODE = True

        monkeypatch.setattr(pyautogui, "press", lambda key: pressed_keys.append(key))
        monkeypatch.setattr(pyautogui, "hotkey", lambda *keys: hotkeys.append(keys))

        blocked_shortcuts = ["win+d", "Win+D", "win + d", "win+down", "alt+space", "win+m"]
        expected_msg = (
            "[Assist Mode] Physical window manipulation and key presses are disabled in Assist Mode. "
            "Do NOT attempt to minimize windows or manipulate the OS. "
            "Your role is strictly to guide Mohamed with the Ghost Cursor. Conclude your turn now."
        )

        try:
            for shortcut in blocked_shortcuts:
                res = brain.execute_tool("press_key", {"key": shortcut})
                res_text = res["text"] if isinstance(res, dict) else str(res)
                assert res_text == expected_msg, f"Expected blocking instruction for {shortcut}, got {res_text}"
            assert len(pressed_keys) == 0
            assert len(hotkeys) == 0
        finally:
            config.ASSIST_MODE = False

    def test_drive_mode_smart_click_and_smart_type_execute_motor(self, monkeypatch):
        """In Drive Mode (ASSIST_MODE=False), smart_click and smart_type execute physical actions."""
        import core.brain as brain
        import config
        import tools.motor as motor

        motor_clicks: list[tuple[int, int]] = []
        motor_types: list[str] = []

        config.ASSIST_MODE = False

        monkeypatch.setattr(
            brain,
            "locate_ui_element_ex",
            lambda goal: {
                "source": "uia",
                "text": goal,
                "score": 0.95,
                "x": 200,
                "y": 300,
                "box": (150, 280, 100, 40),
            },
        )
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Target Window")
        monkeypatch.setattr(brain, "focused_control_type", lambda: "EditControl")
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "B64_MOCK")
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None)
        monkeypatch.setattr(brain, "capture_screen_small_gray", lambda: b"gray")
        monkeypatch.setattr(brain, "screens_differ", lambda a, b: True)
        monkeypatch.setattr("time.sleep", lambda s: None)

        import pyautogui
        monkeypatch.setattr(pyautogui, "hotkey", lambda *keys: None)
        monkeypatch.setattr(
            motor,
            "human_click",
            lambda x, y, clicks=1, button="left", label="": motor_clicks.append((x, y)),
        )
        monkeypatch.setattr(
            motor,
            "human_type",
            lambda txt, press_enter=False: motor_types.append(txt) or "cadence",
        )

        res_click = brain.execute_tool("smart_click", {"goal": "OK button"})
        assert motor_clicks == [(200, 300)]

        res_type = brain.execute_tool(
            "smart_type",
            {"goal": "search bar", "text": "Drive Mode text", "press_enter": True, "confirm_send": True},
        )
        assert "Drive Mode text" in motor_types

    def test_smart_click_does_not_block_on_telegram_foreground(self, monkeypatch):
        """smart_click must not abort when foreground window title is Telegram or aster_local."""
        import core.brain as brain
        import config
        import tools.motor as motor

        clicked_targets: list[tuple[int, int]] = []
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Telegram (aster_local)")
        monkeypatch.setattr(
            brain,
            "locate_ui_element_ex",
            lambda goal: {"source": "uia", "text": goal, "score": 0.9, "x": 100, "y": 200, "box": (80, 180, 40, 40)},
        )
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "MOCK_B64")
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None)
        monkeypatch.setattr(brain, "capture_screen_small_gray", lambda: b"gray")
        monkeypatch.setattr(brain, "screens_differ", lambda a, b: True)
        monkeypatch.setattr(
            motor,
            "human_click",
            lambda x, y, clicks=1, button="left", label="": clicked_targets.append((x, y)),
        )

        config.ASSIST_MODE = False
        res = brain.execute_tool("smart_click", {"goal": "Telegram icon"})
        res_text = res["text"] if isinstance(res, dict) else str(res)

        assert "FAILED — BLOCKED" not in res_text
        assert "Typing here would send a message to yourself" not in res_text
        assert clicked_targets == [(100, 200)]

    def test_smart_click_does_not_block_on_unfocused_turn_start(self, monkeypatch):
        """smart_click must not abort when _unfocused_turn_start is True."""
        import core.brain as brain
        import config
        import tools.motor as motor

        clicked_targets: list[tuple[int, int]] = []
        monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Chrome")
        monkeypatch.setattr(
            brain,
            "locate_ui_element_ex",
            lambda goal: {"source": "uia", "text": goal, "score": 0.9, "x": 150, "y": 250, "box": (130, 230, 40, 40)},
        )
        monkeypatch.setattr(brain, "capture_screen_base64", lambda: "MOCK_B64")
        monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None)
        monkeypatch.setattr(brain, "capture_screen_small_gray", lambda: b"gray")
        monkeypatch.setattr(brain, "screens_differ", lambda a, b: True)
        monkeypatch.setattr(
            motor,
            "human_click",
            lambda x, y, clicks=1, button="left", label="": clicked_targets.append((x, y)),
        )

        config.ASSIST_MODE = False
        res = brain.execute_tool("smart_click", {"goal": "Search box", "_unfocused_turn_start": True})
        res_text = res["text"] if isinstance(res, dict) else str(res)

        assert "FAILED — BLOCKED" not in res_text
        assert clicked_targets == [(150, 250)]


# ============================================================================
# 4. QML FILE VALIDATION
# ============================================================================

class TestOverlayQmlValidation:
    """Verifies tools/overlay.qml structure, color palette, and visual components."""

    def test_overlay_qml_exists_and_contains_required_components(self):
        """tools/overlay.qml exists, has Jarvis Cyan (#00F0FF), Dynamic Island, Ghost Cursor, and Repeater."""
        qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "tools",
            "overlay.qml",
        )
        assert os.path.isfile(qml_path), f"tools/overlay.qml must exist at {qml_path}"

        with open(qml_path, "r", encoding="utf-8") as f:
            qml_content = f.read()

        # 1. Color palette validation
        assert "#00F0FF" in qml_content, "QML overlay must include Jarvis Neon Cyan (#00F0FF)"

        # 2. Top Dynamic Island with auto-hide logic
        assert "islandState" in qml_content
        assert "idleAutoHide" in qml_content or "idleAutoHideTimer" in qml_content
        assert "Dynamic Island" in qml_content or "island" in qml_content.lower()

        # 3. Ghost Cursor & Motion Trail
        assert "ghostX" in qml_content or "ghostTargetX" in qml_content
        assert "ghostOpacity" in qml_content
        assert "Ghost Cursor" in qml_content or "ghost" in qml_content.lower()

        # 4. Highlight Model & Repeater
        assert "highlightModel" in qml_content
        assert "Repeater" in qml_content

    def test_qml_is_idle_scoping_prevents_reference_errors(self):
        """Verifies isIdle is declared on rootOverlay and no child item uses unscoped isIdle."""
        qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "tools",
            "overlay.qml",
        )
        with open(qml_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Root overlay must declare isIdle
        root_decl = any("readonly property bool isIdle" in line for line in lines[:40])
        assert root_decl, "rootOverlay must declare 'readonly property bool isIdle' to prevent scoping errors."

        # Verify child items inside dynamicIsland do not use bare 'isIdle ?'
        for idx, line in enumerate(lines, 1):
            if "isIdle ?" in line:
                assert "rootOverlay.isIdle ?" in line or "dynamicIsland.isIdle ?" in line or "isIdle :" in line, (
                    f"Line {idx} uses bare 'isIdle ?': {line.strip()}. Must qualify as rootOverlay.isIdle or dynamicIsland.isIdle"
                )

    def test_highlight_on_screen_detailed_feedback(self, monkeypatch):
        """highlight_on_screen provides explicit coordinates, anti-hedging guidance, and never attaches screenshots."""
        from core import brain

        fake_matches = [
            {"box": (100, 200, 50, 40), "score": 0.95, "text": "Discord Icon Taskbar"},
            {"box": (500, 300, 60, 60), "score": 0.90, "text": "Discord Shortcut"},
        ]
        screenshot_calls = []
        monkeypatch.setattr(brain, "locate_ui_elements_boxed", lambda goal: fake_matches)
        monkeypatch.setattr(brain, "_attach_action_screenshot", lambda text, action=None: screenshot_calls.append(text) or f"{text} [SCREENSHOT]")

        res = brain.execute_tool("highlight_on_screen", {"goal": "Discord icon"})
        assert "Highlighted 2 match(es)" in res
        assert "(125,220)" in res  # cx = 100 + 25, cy = 200 + 20
        assert "Do NOT ask if you should highlight them" in res
        # Overlay is excluded from screen capture (WDA_EXCLUDEFROMCAPTURE), so attaching
        # a screenshot tricks the model into thinking the highlight didn't show up.
        assert len(screenshot_calls) == 0, "highlight_on_screen must NOT call _attach_action_screenshot!"
        assert "[SCREENSHOT]" not in res

    def test_qml_no_undefined_is_idle_runtime(self):
        """Instantiates overlay.qml and verifies no 'Unable to assign [undefined]' or reference errors occur."""
        import sys
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlEngine, QQmlComponent
        from PySide6.QtCore import qInstallMessageHandler

        qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "tools",
            "overlay.qml",
        )

        app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        engine = QQmlEngine()

        qml_warnings: list[str] = []
        def msg_handler(msg_type, context, msg):
            msg_str = str(msg)
            if "warning" in str(msg_type).lower() or "critical" in str(msg_type).lower() or "undefined" in msg_str.lower() or "isIdle" in msg_str:
                qml_warnings.append(msg_str)

        qInstallMessageHandler(msg_handler)
        try:
            component = QQmlComponent(engine, qml_path)
            assert component.status() == QQmlComponent.Status.Ready, f"QML errors: {component.errors()}"

            item = component.create()
            assert item is not None, f"Failed to instantiate QML component: {component.errors()}"

            # Cycle through all states
            states = ["idle", "listening", "thinking", "locating", "acting", "speaking", "assist", "idle"]
            for s in states:
                item.setProperty("islandState", s)
                item.setProperty("assistModeActive", True)
                item.setProperty("assistModeActive", False)

            # Check warnings for undefined isIdle
            undefined_warnings = [w for w in qml_warnings if "Unable to assign [undefined]" in w or "isIdle" in w]
            assert len(undefined_warnings) == 0, f"QML emitted undefined isIdle warnings: {undefined_warnings}"
        finally:
            qInstallMessageHandler(None)

    def test_qml_dynamic_island_defines_is_idle(self):
        """tools/overlay.qml must define readonly property bool isIdle in dynamicIsland."""
        qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "tools",
            "overlay.qml",
        )
        with open(qml_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check for dynamicIsland declaration
        assert "id: dynamicIsland" in content
        island_part = content.split("id: dynamicIsland")[1].split("id: contentRow")[0]
        assert "isIdle" in island_part, (
            "dynamicIsland must define 'readonly property bool isIdle: rootOverlay.isIdle' so child bindings don't evaluate to undefined"
        )

    def test_qml_no_dynamic_island_is_idle_references(self):
        """tools/overlay.qml should not use dynamicIsland.isIdle; all references must use rootOverlay.isIdle."""
        qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "tools",
            "overlay.qml",
        )
        with open(qml_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "dynamicIsland.isIdle" not in content, (
            "dynamicIsland.isIdle references should be replaced with rootOverlay.isIdle."
        )

    def test_ghost_cursor_trail_and_easing(self):
        """tools/overlay.qml must have 4-part trailing comet tail with OutCubic easing and glowing core."""
        qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "tools",
            "overlay.qml",
        )
        with open(qml_path, "r", encoding="utf-8") as f:
            content = f.read()

        for trail_id in ["trail0", "trail1", "trail2", "trail3"]:
            assert f"id: {trail_id}" in content, f"{trail_id} must be defined in overlay.qml"

        assert "ghostSonarRing" in content
        assert "ghostRippleAnim" in content
        assert "Easing.OutCubic" in content

    def test_qml_runtime_ghost_cursor_and_island_transitions(self):
        """Instantiates overlay.qml and exercises setIsland, moveGhost, displayHighlights, clearHighlights."""
        import sys
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlEngine, QQmlComponent

        qml_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "tools",
            "overlay.qml",
        )

        app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        engine = QQmlEngine()
        component = QQmlComponent(engine, qml_path)
        assert component.status() == QQmlComponent.Status.Ready, f"QML errors: {component.errors()}"

        item = component.create()
        assert item is not None

        # 1. Verify idle state properties
        item.setProperty("islandState", "idle")
        assert item.property("isIdle") is True

        # 2. Verify moveGhost function moves coordinates and sets opacity
        from PySide6.QtCore import QMetaObject, Q_ARG
        QMetaObject.invokeMethod(item, "moveGhost", Q_ARG("QVariant", 450), Q_ARG("QVariant", 620), Q_ARG("QVariant", "Save Button"), Q_ARG("QVariant", 300), Q_ARG("QVariant", True))
        assert item.property("ghostX") == 450
        assert item.property("ghostY") == 620
        assert item.property("ghostTargetX") == 450
        assert item.property("ghostTargetY") == 620
        assert item.property("ghostLabel") == "Save Button"
        assert item.property("ghostOpacity") == 1.0

        # 3. Verify hideGhost
        QMetaObject.invokeMethod(item, "hideGhost")
        assert item.property("ghostOpacity") == 0.0

        # 4. Verify displayHighlights and clearHighlights
        boxes = [{"x": 100, "y": 150, "width": 80, "height": 30, "label": "Test Box"}]
        QMetaObject.invokeMethod(item, "displayHighlights", Q_ARG("QVariant", boxes), Q_ARG("QVariant", 3.0), Q_ARG("QVariant", True))
        assert item.property("ghostX") == 140  # 100 + 40
        assert item.property("ghostY") == 165  # 150 + 15
        assert item.property("ghostOpacity") == 1.0

        QMetaObject.invokeMethod(item, "clearHighlights")


# ============================================================================
# 5. DETERMINISTIC ASSIST MODE TOGGLE TESTS (core/brain.py)
# ============================================================================

class TestDeterministicAssistMode:
    """Verifies deterministic fast-path / interception for Assist Mode toggling in process_user_input."""

    @pytest.mark.parametrize("phrase", [
        "turn on assist mode",
        "Turn on assist mode",
        "enable assist mode",
        "assist mode on",
        "/assist on",
        "TURN ON ASSIST MODE",
    ])
    def test_deterministic_enable_assist_mode(self, phrase, monkeypatch):
        import core.brain as brain
        import config
        import tools.overlay as overlay

        config.ASSIST_MODE = False
        overlay.set_assist_mode_badge(False)

        llm_called: list[bool] = []
        monkeypatch.setattr(brain, "_execute_llm_completion", lambda *a, **kw: llm_called.append(True))

        res = brain.process_user_input(phrase)
        res_str = str(res)

        assert config.ASSIST_MODE is True
        assert overlay.get_overlay_state()["assist_mode"] is True
        assert len(llm_called) == 0, f"Deterministic fast-path must not invoke LLM for '{phrase}'!"
        assert any(kw in res_str.lower() for kw in ("assist mode", "enabled", "on", "active"))

    @pytest.mark.parametrize("phrase", [
        "turn off assist mode",
        "Turn off assist mode",
        "disable assist mode",
        "assist mode off",
        "/assist off",
        "TURN OFF ASSIST MODE",
    ])
    def test_deterministic_disable_assist_mode(self, phrase, monkeypatch):
        import core.brain as brain
        import config
        import tools.overlay as overlay

        config.ASSIST_MODE = True
        overlay.set_assist_mode_badge(True)

        llm_called: list[bool] = []
        monkeypatch.setattr(brain, "_execute_llm_completion", lambda *a, **kw: llm_called.append(True))

        res = brain.process_user_input(phrase)
        res_str = str(res)

        assert config.ASSIST_MODE is False
        assert overlay.get_overlay_state()["assist_mode"] is False
        assert len(llm_called) == 0, f"Deterministic fast-path must not invoke LLM for '{phrase}'!"
        assert any(kw in res_str.lower() for kw in ("assist mode", "disabled", "off", "inactive", "drive mode"))

    def test_deterministic_slash_assist_toggle(self, monkeypatch):
        import core.brain as brain
        import config
        import tools.overlay as overlay

        monkeypatch.setattr(brain, "_execute_llm_completion", lambda *a, **kw: pytest.fail("LLM should not be called"))

        # When OFF -> toggles ON
        config.ASSIST_MODE = False
        overlay.set_assist_mode_badge(False)
        brain.process_user_input("/assist")
        assert config.ASSIST_MODE is True
        assert overlay.get_overlay_state()["assist_mode"] is True

        # When ON -> toggles OFF
        brain.process_user_input("/assist")
        assert config.ASSIST_MODE is False
        assert overlay.get_overlay_state()["assist_mode"] is False

    @pytest.mark.parametrize("phrase", [
        "enable assist mide",
        "turn on assist",
        "turn off assist",
        "assist on",
        "assist off",
    ])
    def test_deterministic_assist_mode_typos_and_short_forms(self, phrase, monkeypatch):
        import core.brain as brain
        import config

        monkeypatch.setattr(brain, "_execute_llm_completion", lambda *a, **kw: pytest.fail("LLM should not be called"))

        config.ASSIST_MODE = False
        res = brain.process_user_input(phrase)
        res_str = str(res)
        if "off" in phrase:
            assert config.ASSIST_MODE is False
            assert "disabled" in res_str.lower() or "drive mode" in res_str.lower()
        else:
            assert config.ASSIST_MODE is True
            assert "enabled" in res_str.lower() or "assist mode" in res_str.lower()


class TestQmlSignalBridgeIntegration:
    """Validate that Python OverlayBridge signals reach QML Connections without silent drops."""

    def test_qml_signal_names_and_aliases_match_qml_connections(self):
        """OverlayBridge must define CamelCase and snake_case signals matching QML connections."""
        import os
        from PySide6.QtCore import QObject, Signal, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine

        app = QGuiApplication.instance() or QGuiApplication([])

        qml_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools",
            "overlay.qml",
        )

        class OverlayBridge(QObject):
            sigIslandState = Signal(str, str, str, float)
            sigAssistBadge = Signal(bool)
            sigGhostMove = Signal(int, int, str, int, bool)
            sigGhostHide = Signal()
            sigShowHighlights = Signal(list, float, bool)
            sigClearHighlights = Signal()
            sigPulse = Signal(int, int, str)
            sigQuit = Signal()

            sig_island_state = sigIslandState
            sig_assist_badge = sigAssistBadge
            sig_ghost_move = sigGhostMove
            sig_ghost_hide = sigGhostHide
            sig_show_highlights = sigShowHighlights
            sig_clear_highlights = sigClearHighlights
            sig_pulse = sigPulse
            sig_quit = sigQuit

        bridge = OverlayBridge()
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("overlayBridge", bridge)
        engine.load(QUrl.fromLocalFile(qml_path))

        root = engine.rootObjects()[0]
        assert root is not None

        # Verify initial states
        assert root.property("assistModeActive") is False
        assert root.property("islandState") == "idle"

        # Test sigAssistBadge
        bridge.sigAssistBadge.emit(True)
        app.processEvents()
        assert root.property("assistModeActive") is True

        # Test sigIslandState
        bridge.sigIslandState.emit("assist", "Guidance Active", "Subtext", 0)
        app.processEvents()
        assert root.property("islandState") == "assist"
        assert root.property("statusText") == "Guidance Active"

        # Test sigGhostMove
        bridge.sigGhostMove.emit(650, 450, "Test Button", 300, True)
        app.processEvents()
        assert root.property("ghostOpacity") == 1.0
        assert root.property("ghostX") == 650.0
        assert root.property("ghostY") == 450.0

        # Test snake_case alias invocation
        bridge.sig_assist_badge.emit(False)
        app.processEvents()
        assert root.property("assistModeActive") is False


# ============================================================================
# 5. DESKTOP LAUNCHER, UIA & VISION GROUNDING REGRESSION TESTS
# ============================================================================

class TestDesktopLauncherAndLocatorRegression:
    """Regression tests for control type disambiguation, desktop icon collection,
    Telegram chat message suppression, and Qwen Vision Grounding fallback.
    """

    def test_control_type_disambiguation_penalizes_body_text_for_launcher_goals(self):
        """TextControl body text is penalized when goal asks for 'icon', 'app', 'shortcut', etc.,
        while ListItemControl and ButtonControl retain high scores.
        """
        from tools.locator_common import parse_goal, score_text

        goal = parse_goal("open League of Legends desktop app")
        score_list_item = score_text("League of Legends", goal, cand_type="ListItemControl")
        score_button = score_text("League of Legends", goal, cand_type="ButtonControl")
        score_text_ctrl = score_text("League of Legends", goal, cand_type="TextControl")
        score_doc_ctrl = score_text("League of Legends", goal, cand_type="DocumentControl")

        # Launcher controls score high (>= 0.85)
        assert score_list_item >= 0.85
        assert score_button >= 0.85

        # Static text body controls are penalized by at least 0.45
        assert score_text_ctrl <= 0.55
        assert score_doc_ctrl <= 0.55
        assert score_list_item - score_text_ctrl >= 0.40

    def test_control_type_disambiguation_taskbar_button_and_icon_goal(self):
        """ButtonControl and ListItemControl score high for icon/launcher goals."""
        from tools.locator_common import parse_goal, score_text

        goal = parse_goal("click Discord launcher icon")
        btn_score = score_text("Discord", goal, cand_type="ButtonControl")
        txt_score = score_text("Discord", goal, cand_type="TextControl")

        assert btn_score >= 0.85
        assert txt_score <= 0.55

    def test_control_type_no_penalty_when_not_launcher_goal(self):
        """TextControl is NOT penalized when the goal does NOT ask for an icon or launcher."""
        from tools.locator_common import parse_goal, score_text

        goal = parse_goal("League of Legends")
        txt_score = score_text("League of Legends", goal, cand_type="TextControl")
        list_score = score_text("League of Legends", goal, cand_type="ListItemControl")
        assert txt_score == 1.0
        assert list_score == 1.0

    def test_telegram_window_detection(self, monkeypatch):
        """_is_telegram_window correctly detects Telegram by class, process, or title."""
        import tools.uia as uia

        class DummyTop:
            def __init__(self, name="", class_name="", pid=0):
                self.Name = name
                self.ClassName = class_name
                self.ProcessId = pid

        # Telegram window class
        assert uia._is_telegram_window(DummyTop(class_name="TelegramDesktop")) is True
        # Aster control title
        assert uia._is_telegram_window(DummyTop(name="Mohamed - Aster's Chat")) is True
        assert uia._is_telegram_window(DummyTop(name="aster_local")) is True
        # Foreground window title fallback
        monkeypatch.setattr(uia, "get_foreground_window_title", lambda: "aster_local - Telegram")
        assert uia._is_telegram_window(DummyTop(name="Chat")) is True

        # Non-Telegram window
        monkeypatch.setattr(uia, "get_foreground_window_title", lambda: "Chrome")
        assert uia._is_telegram_window(DummyTop(name="Google Chrome", class_name="Chrome_WidgetWin_1", pid=99999)) is False

    def test_telegram_foreground_suppresses_chat_text_controls(self):
        """_walk_named_controls with exclude_types={'TextControl'} suppresses chat body text."""
        import tools.uia as uia

        class MockRect:
            def __init__(self, l, t, r, b):
                self.left = l
                self.top = t
                self.right = r
                self.bottom = b

        class MockNode:
            def __init__(self, name, type_name, rect, children=None):
                self.Name = name
                self.ControlTypeName = type_name
                self.BoundingRectangle = rect
                self.IsOffscreen = False
                self._children = children or []

            def GetChildren(self):
                return self._children

        text_msg = MockNode("League of Legends is great", "TextControl", MockRect(10, 10, 200, 40))
        btn = MockNode("Send", "ButtonControl", MockRect(210, 10, 260, 40))
        top = MockNode("Telegram", "WindowControl", MockRect(0, 0, 800, 600), children=[text_msg, btn])

        # Without exclusion, both appear
        all_cands = uia._walk_named_controls(top, budget_s=1.0)
        types_all = [c["type"] for c in all_cands]
        assert "TextControl" in types_all
        assert "ButtonControl" in types_all

        # With exclude_types={'TextControl'}, TextControl is suppressed
        suppressed_cands = uia._walk_named_controls(top, budget_s=1.0, exclude_types={"TextControl"})
        types_suppressed = [c["type"] for c in suppressed_cands]
        assert "TextControl" not in types_suppressed
        assert "ButtonControl" in types_suppressed

    def test_desktop_icon_candidate_collection(self, monkeypatch):
        """_collect_desktop_candidates walks Progman/WorkerW -> SHELLDLL_DefView -> SysListView32."""
        import tools.uia as uia

        class MockRect:
            def __init__(self, l, t, r, b):
                self.left = l
                self.top = t
                self.right = r
                self.bottom = b

        class MockNode:
            def __init__(self, name, type_name, class_name, rect, children=None):
                self.Name = name
                self.ControlTypeName = type_name
                self.ClassName = class_name
                self.BoundingRectangle = rect
                self.IsOffscreen = False
                self._children = children or []

            def GetChildren(self):
                return self._children

        shortcut1 = MockNode("League of Legends", "ListItemControl", "", MockRect(20, 20, 80, 80))
        shortcut2 = MockNode("Visual Studio Code", "ListItemControl", "", MockRect(20, 100, 80, 160))
        sys_list = MockNode("FolderView", "ListControl", "SysListView32", MockRect(0, 0, 1920, 1080), children=[shortcut1, shortcut2])
        def_view = MockNode("", "PaneControl", "SHELLDLL_DefView", MockRect(0, 0, 1920, 1080), children=[sys_list])
        worker_w = MockNode("", "WindowControl", "WorkerW", MockRect(0, 0, 1920, 1080), children=[def_view])
        root = MockNode("Desktop", "PaneControl", "", MockRect(0, 0, 1920, 1080), children=[worker_w])

        desktop_icons = uia._collect_desktop_candidates(root, budget_s=1.0)
        assert len(desktop_icons) == 2
        names = [icon["name"] for icon in desktop_icons]
        assert "League of Legends" in names
        assert "Visual Studio Code" in names
        assert all(icon["type"] == "ListItemControl" for icon in desktop_icons)

    def test_collect_candidates_integrates_desktop_icons_and_suppresses_telegram_chat(self, monkeypatch):
        """_collect_candidates integrates desktop icons and suppresses Telegram chat bubbles."""
        import tools.uia as uia

        class MockRect:
            def __init__(self, l, t, r, b):
                self.left = l
                self.top = t
                self.right = r
                self.bottom = b

        class MockNode:
            def __init__(self, name, type_name, class_name, rect, children=None):
                self.Name = name
                self.ControlTypeName = type_name
                self.ClassName = class_name
                self.BoundingRectangle = rect
                self.IsOffscreen = False
                self._children = children or []

            def GetChildren(self):
                return self._children

            def GetTopLevelControl(self):
                return self

        chat_msg = MockNode("League of Legends", "TextControl", "", MockRect(100, 100, 300, 130))
        send_btn = MockNode("Send", "ButtonControl", "", MockRect(310, 100, 360, 130))
        telegram_win = MockNode("aster_local - Telegram", "WindowControl", "TelegramDesktop", MockRect(50, 50, 600, 500), children=[chat_msg, send_btn])

        shortcut = MockNode("League of Legends", "ListItemControl", "", MockRect(20, 20, 80, 80))
        sys_list = MockNode("FolderView", "ListControl", "SysListView32", MockRect(0, 0, 1920, 1080), children=[shortcut])
        def_view = MockNode("", "PaneControl", "SHELLDLL_DefView", MockRect(0, 0, 1920, 1080), children=[sys_list])
        progman = MockNode("", "WindowControl", "Progman", MockRect(0, 0, 1920, 1080), children=[def_view])
        root = MockNode("Desktop", "PaneControl", "", MockRect(0, 0, 1920, 1080), children=[progman])

        monkeypatch.setattr(uia.auto, "GetRootControl", lambda: root)
        monkeypatch.setattr(uia.auto, "GetForegroundControl", lambda: telegram_win)
        monkeypatch.setattr(uia, "get_foreground_window_title", lambda: "aster_local - Telegram")

        cands, desk_size = uia._collect_candidates()
        # Chat text must be suppressed, desktop shortcut must be present
        cand_names_and_types = [(c["name"], c["type"]) for c in cands]
        assert ("League of Legends", "ListItemControl") in cand_names_and_types
        assert ("League of Legends", "TextControl") not in cand_names_and_types

    def test_locate_ui_elements_boxed_falls_back_to_vision_when_no_authentic_launcher(self, monkeypatch):
        """locate_ui_elements_boxed falls back to Qwen vision when UIA returns only static text for an icon target."""
        import tools.vision as vision
        import pyautogui

        # Mock UIA candidates returning only static TextControl
        uia_fake_nodes = [
            {"name": "League of Legends", "type": "TextControl", "rect": (100, 100, 300, 150)},
        ]
        monkeypatch.setattr("tools.uia.uia_nodes", lambda: (uia_fake_nodes, (1920, 1080)))
        monkeypatch.setattr(pyautogui, "size", lambda: (1920, 1080))

        vision_called = []
        vision_fake_result = [
            {"box": (20, 20, 64, 64), "score": 0.9, "text": "League of Legends icon", "source": "qwen-vision"}
        ]
        monkeypatch.setattr(vision, "_qwen_vision_locate_boxes", lambda goal: vision_called.append(goal) or vision_fake_result)

        res = vision.locate_ui_elements_boxed("League of Legends icon")
        assert len(vision_called) == 1
        assert res == vision_fake_result
        assert res[0]["source"] == "qwen-vision"

    def test_locate_ui_elements_boxed_accepts_authentic_launcher(self, monkeypatch):
        """locate_ui_elements_boxed returns UIA match directly when authentic launcher control exists."""
        import tools.vision as vision
        import pyautogui

        uia_fake_nodes = [
            {"name": "League of Legends", "type": "ListItemControl", "rect": (20, 20, 80, 80)},
        ]
        monkeypatch.setattr("tools.uia.uia_nodes", lambda: (uia_fake_nodes, (1920, 1080)))
        monkeypatch.setattr(pyautogui, "size", lambda: (1920, 1080))

        vision_called = []
        monkeypatch.setattr(vision, "_qwen_vision_locate_boxes", lambda goal: vision_called.append(goal) or [])

        res = vision.locate_ui_elements_boxed("open League of Legends desktop app")
        assert len(vision_called) == 0
        assert len(res) == 1
        assert res[0]["source"] == "uia"
        assert res[0]["type"] == "ListItemControl"

    def test_locate_ui_element_ex_falls_back_to_vision_on_static_text(self, monkeypatch):
        """locate_ui_element_ex rejects static TextControl for launcher target and falls through to vision."""
        import tools.vision as vision
        import config

        monkeypatch.setattr(config, "USE_DOM_MOTOR", False)
        # UIA returns a TextControl match
        monkeypatch.setattr("tools.uia.uia_locate", lambda goal: {
            "x": 200, "y": 125, "text": "League of Legends", "type": "TextControl", "score": 0.95, "source": "uia"
        })

        vision_called = []
        monkeypatch.setattr(vision, "_qwen_vision_locate", lambda goal: vision_called.append(goal) or {
            "x": 50, "y": 50, "text": goal, "source": "qwen-vision", "score": 0.9
        })

        res = vision.locate_ui_element_ex("League of Legends icon")
        assert len(vision_called) == 1
        assert res is not None
        assert res["source"] == "qwen-vision"
        assert res["x"] == 50
        assert res["y"] == 50


class TestOverlayCoordinateNormalizationAndClipping:
    """Verify coordinate normalization and desktop/taskbar filtering."""

    def test_normalize_box_all_formats(self):
        import tools.overlay as overlay

        # 1. Box format
        b1 = overlay._normalize_box({"box": (10, 20, 30, 40), "text": "Box Item"})
        assert b1 == {"x": 10, "y": 20, "left": 10, "top": 20, "width": 30, "height": 40, "label": "Box Item"}

        # 2. Rect format (x1, y1, x2, y2)
        b2 = overlay._normalize_box({"rect": (100, 200, 160, 250), "name": "Rect Item"})
        assert b2 == {"x": 100, "y": 200, "left": 100, "top": 200, "width": 60, "height": 50, "label": "Rect Item"}

        # 3. Explicit coordinates (left, top, width, height)
        b3 = overlay._normalize_box({"left": 15, "top": 25, "width": 35, "height": 45, "label": "Explicit Item"})
        assert b3 == {"x": 15, "y": 25, "left": 15, "top": 25, "width": 35, "height": 45, "label": "Explicit Item"}

        # 4. Tuple format
        b4 = overlay._normalize_box((50, 60, 70, 80, "Tuple Item"))
        assert b4 == {"x": 50, "y": 60, "left": 50, "top": 60, "width": 70, "height": 80, "label": "Tuple Item"}

    def test_show_highlights_accepts_single_dict_or_tuple(self):
        import tools.overlay as overlay

        overlay.show_highlights({"box": (10, 20, 30, 40), "text": "Single Dict"})
        hl = overlay.get_overlay_state()["active_highlights"]
        assert len(hl) == 1
        assert hl[0]["x"] == 10
        assert hl[0]["left"] == 10
        assert hl[0]["width"] == 30
        assert hl[0]["height"] == 40

        overlay.show_highlights((20, 30, 40, 50, "Single Tuple"))
        hl2 = overlay.get_overlay_state()["active_highlights"]
        assert len(hl2) == 1
        assert hl2[0]["x"] == 20
        assert hl2[0]["left"] == 20
        assert hl2[0]["label"] == "Single Tuple"

    def test_locate_ui_elements_boxed_desktop_goal_filters_desktop_source(self, monkeypatch):
        """When goal queries desktop, active window controls are pruned in favor of source == 'desktop'."""
        import tools.vision as vision
        import pyautogui

        fake_nodes = [
            {"name": "League of Legends", "type": "ButtonControl", "rect": (50, 50, 120, 120), "source": "window"},
            {"name": "League of Legends", "type": "ListItemControl", "rect": (10, 10, 60, 60), "source": "desktop"},
        ]
        monkeypatch.setattr("tools.uia.uia_nodes", lambda: (fake_nodes, (1920, 1080)))
        monkeypatch.setattr(pyautogui, "size", lambda: (1920, 1080))

        res = vision.locate_ui_elements_boxed("League of Legends on desktop")
        assert len(res) == 1
        assert res[0]["text"] == "League of Legends"
        assert res[0]["control_source"] == "desktop"
        assert res[0]["box"] == (10, 10, 50, 50)

    def test_locate_ui_elements_boxed_taskbar_goal_prioritizes_taskbar(self, monkeypatch):
        """When goal queries taskbar/start, Shell_TrayWnd taskbar controls are prioritized."""
        import tools.vision as vision
        import pyautogui

        fake_nodes = [
            {"name": "Start", "type": "ButtonControl", "rect": (200, 200, 280, 240), "source": "window"},
            {"name": "Start", "type": "ButtonControl", "rect": (0, 1040, 48, 1080), "source": "taskbar"},
        ]
        monkeypatch.setattr("tools.uia.uia_nodes", lambda: (fake_nodes, (1920, 1080)))
        monkeypatch.setattr(pyautogui, "size", lambda: (1920, 1080))

        res = vision.locate_ui_elements_boxed("start button")
        assert len(res) >= 1
        assert res[0]["is_taskbar"] is True
        assert res[0]["control_source"] == "taskbar"


class TestAssistModeLoopEliminationAndDesktopIsolationRegression:
    """Regression tests for Assist Mode one-shot highlight/blocking and desktop SysListView32 isolation."""

    def test_highlight_on_screen_returns_direct_message_without_screenshot(self, monkeypatch):
        """verify highlight_on_screen returns result_msg directly without calling _attach_action_screenshot."""
        import core.brain as brain

        fake_matches = [
            {"box": (100, 200, 50, 40), "score": 0.95, "text": "League of Legends"},
        ]
        monkeypatch.setattr(brain, "locate_ui_elements_boxed", lambda goal: fake_matches)

        screenshot_calls = []
        monkeypatch.setattr(
            brain,
            "_attach_action_screenshot",
            lambda text, action=None: screenshot_calls.append(text) or f"{text} [SCREENSHOT]",
        )

        res = brain.execute_tool("highlight_on_screen", {"goal": "League of Legends"})
        assert len(screenshot_calls) == 0, "_attach_action_screenshot must NEVER be called by highlight_on_screen!"
        assert "[SCREENSHOT]" not in res
        assert "[Assist Mode] Highlighted 1 match(es) for 'League of Legends'" in res
        assert "(125,220)" in res
        assert "Do NOT ask if you should highlight them — they are ALREADY highlighted." in res

    @pytest.mark.parametrize("key_combination", ["win+d", "win+down", "alt+space"])
    def test_assist_mode_blocks_win_d_and_window_minimization(self, monkeypatch, key_combination):
        """verify press_key with 'win+d', 'win+down', 'alt+space' in Assist Mode returns the explicit blocking message."""
        import core.brain as brain
        import config
        import pyautogui

        hotkeys_called = []
        presses_called = []
        monkeypatch.setattr(pyautogui, "hotkey", lambda *a, **k: hotkeys_called.append(a))
        monkeypatch.setattr(pyautogui, "press", lambda *a, **k: presses_called.append(a))

        config.ASSIST_MODE = True
        try:
            res = brain.execute_tool("press_key", {"key": key_combination})
            expected_msg = (
                "[Assist Mode] Physical window manipulation and key presses are disabled in Assist Mode. "
                "Do NOT attempt to minimize windows or manipulate the OS. "
                "Your role is strictly to guide Mohamed with the Ghost Cursor. Conclude your turn now."
            )
            assert res == expected_msg
            assert len(hotkeys_called) == 0, "pyautogui.hotkey must not be called when window manipulation is blocked"
            assert len(presses_called) == 0, "pyautogui.press must not be called when window manipulation is blocked"
        finally:
            config.ASSIST_MODE = False

    def test_desktop_goal_rejects_active_window_controls(self, monkeypatch):
        """verify that goals with 'desktop' prioritize source == 'desktop' candidates and ignore active window controls."""
        import tools.uia as uia
        import tools.vision as vision
        import pyautogui

        fake_candidates = [
            {
                "name": "League of Legends",
                "type": "ListItemControl",
                "rect": (300, 300, 500, 350),
                "source": "window",
            },
            {
                "name": "League of Legends",
                "type": "ListItemControl",
                "rect": (40, 40, 88, 88),
                "full_rect": (24, 20, 104, 110),
                "source": "desktop",
            },
        ]
        desktop_size = (1920, 1080)
        monkeypatch.setattr(pyautogui, "size", lambda: (1920, 1080))

        # 1. Test uia_locate_candidates
        loc_res = uia.uia_locate_candidates("League of Legends desktop shortcut", fake_candidates, desktop_size)
        assert loc_res is not None
        assert loc_res["node_source"] == "desktop"
        assert loc_res["x"] == 64
        assert loc_res["y"] == 64

        # 2. Test locate_ui_elements_boxed
        monkeypatch.setattr("tools.uia.uia_nodes", lambda: (fake_candidates, desktop_size))
        boxed_res = vision.locate_ui_elements_boxed("League of Legends on desktop")
        assert len(boxed_res) == 1
        assert boxed_res[0]["control_source"] == "desktop"
        assert boxed_res[0]["text"] == "League of Legends"
        assert boxed_res[0]["box"] == (40, 40, 48, 48)

    def test_desktop_icon_box_refinement(self):
        """verify desktop ListItemControl rect is refined to tightly frame the upper icon area."""
        import tools.uia as uia

        class MockRect:
            def __init__(self, l, t, r, b):
                self.left = l
                self.top = t
                self.right = r
                self.bottom = b

        class MockNode:
            def __init__(self, name, type_name, class_name, rect, children=None):
                self.Name = name
                self.ControlTypeName = type_name
                self.ClassName = class_name
                self.BoundingRectangle = rect
                self.IsOffscreen = False
                self._children = children or []

            def GetChildren(self):
                return self._children

        # Full icon cell: width 80 (100 to 180), height 90 (200 to 290)
        shortcut_node = MockNode(
            "League of Legends",
            "ListItemControl",
            "",
            MockRect(100, 200, 180, 290),
        )
        sys_list = MockNode("FolderView", "ListControl", "SysListView32", MockRect(0, 0, 1920, 1080), children=[shortcut_node])
        def_view = MockNode("", "PaneControl", "SHELLDLL_DefView", MockRect(0, 0, 1920, 1080), children=[sys_list])
        worker_w = MockNode("", "WindowControl", "WorkerW", MockRect(0, 0, 1920, 1080), children=[def_view])
        root = MockNode("Desktop", "PaneControl", "", MockRect(0, 0, 1920, 1080), children=[worker_w])

        desktop_icons = uia._collect_desktop_candidates(root, budget_s=1.0)
        assert len(desktop_icons) == 1
        icon = desktop_icons[0]
        assert icon["name"] == "League of Legends"
        assert icon["type"] == "ListItemControl"
        assert icon["source"] == "desktop"
        assert icon["full_rect"] == (100, 200, 180, 290)

        # Refined rect calculation:
        # tight_w = min(48, 80) = 48 -> x1 + (80 - 48)//2 = 116, x1 + (80 + 48)//2 = 164
        # tight_h = min(48, 90) = 48 -> y1 + 2 = 202, y1 + 48 = 248
        assert icon["rect"] == (116, 202, 164, 248)
        assert (icon["rect"][2] - icon["rect"][0]) == 48
        assert (icon["rect"][3] - icon["rect"][1]) == 46



