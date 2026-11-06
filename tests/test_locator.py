"""
GUI Locator Test Suite — UIA + Direct Qwen 3.6 Vision Grounding.

Per-feature test classes (Mohamed's standing preference):
  Feature 1 — UIA Track 0 + shared goal parsing/scoring (tools/locator_common, tools/uia)
  Feature 2 — deterministic action verification (screen diff, FAILED strings, focus safety)
  Feature 3 — Qwen 3.6 native vision grounding pipeline (tools/vision)

Run: pytest tests/test_locator.py -v
No llama-server, no live screen interaction — screen capture and LLM completions are mocked.
"""
import os
import sys
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.locator_common import parse_goal, score_text, spatial_weight


# ============================================================================
# Feature 1 — UIA Track 0 + shared goal parsing/scoring
# ============================================================================

class TestGoalParsingAndUIA:
    def test_filler_words_stripped(self):
        p = parse_goal("the search bar at the top")
        assert p["tokens"] == {"search"}
        assert p["clean"] == "search"

    def test_spatial_hints_extracted(self):
        assert parse_goal("minimize button at the top right")["spatial"] == {"top", "right"}
        assert parse_goal("File menu")["spatial"] == set()

    def test_type_hints_extracted(self):
        p = parse_goal("the submit button")
        assert "ButtonControl" in p["type_hints"]
        p2 = parse_goal("username field")
        assert "EditControl" in p2["type_hints"]

    def test_all_filler_goal_falls_back_to_raw_words(self):
        p = parse_goal("the top bar")
        assert p["tokens"]  # never empty — degrades to all words
        assert p["clean"]

    def test_score_exact_and_fuzzy(self):
        p = parse_goal("the search bar at the top")
        assert score_text("Search", p) > 0.85       # label match despite filler
        assert score_text("Settings", p) < 0.5
        assert score_text("", p) == 0.0

    def test_score_substring_both_directions(self):
        p = parse_goal("File menu")
        assert score_text("file", p) >= 0.9          # candidate inside goal
        p2 = parse_goal("settings")
        assert score_text("settings gear icon", p2) >= 0.9  # goal inside candidate

    def test_spatial_weight_agrees_and_contradicts(self):
        hint = {"top", "right"}
        assert spatial_weight(1800, 20, 1920, 1080, hint) == 1.0    # top-right ✓
        assert spatial_weight(100, 1000, 1920, 1080, hint) == 0.8   # bottom-left ✗
        assert spatial_weight(100, 1000, 1920, 1080, set()) == 1.0  # no hints

    def test_uia_module_degrades_gracefully(self):
        """uia_locate/foreground helpers never raise, even on garbage input."""
        from tools.uia import uia_locate, get_foreground_window_title, EDITABLE_CONTROL_TYPES

        assert uia_locate("") is None
        assert isinstance(get_foreground_window_title(), str)
        assert "EditControl" in EDITABLE_CONTROL_TYPES
        assert "DocumentControl" not in EDITABLE_CONTROL_TYPES  # documents are NOT Ctrl+A-safe


# ============================================================================
# Feature 2 — deterministic action verification
# ============================================================================

class TestActionVerification:
    def test_screens_differ_identical_frames(self):
        from tools.vision import screens_differ

        a = np.full((135, 240), 128, dtype=np.uint8)
        assert screens_differ(a, a.copy()) is False

    def test_screens_differ_catches_tiny_change(self):
        """A toggled checkbox is a few strong pixels — blockwise max must see it."""
        from tools.vision import screens_differ

        a = np.full((135, 240), 128, dtype=np.uint8)
        b = a.copy()
        b[60:63, 100:103] = 255
        assert screens_differ(a, b) is True

    def test_screens_differ_errs_toward_changed_when_unsure(self):
        from tools.vision import screens_differ

        a = np.full((135, 240), 128, dtype=np.uint8)
        assert screens_differ(None, a) is True
        assert screens_differ(a, None) is True
        assert screens_differ(a, np.full((10, 10), 128, dtype=np.uint8)) is True

    def test_locate_miss_message_is_seen_by_failure_guard(self):
        """smart_click/smart_type misses must start with FAILED so the
        failure-blind-claim guard (core/brain.py) catches them."""
        from core.brain import _tool_result_failed

        assert _tool_result_failed('FAILED — could not locate "play button" on screen. Nothing was clicked.') is True
        assert _tool_result_failed('FAILED — clicked "search" at (5, 5) but keyboard focus landed on a PaneControl') is True
        assert _tool_result_failed("Clicked 'File menu' at (43, 12) (matched via uia: 'File', score 1.0).") is False


# ============================================================================
# Feature 3 — Direct Qwen 3.6 Vision Grounding
# ============================================================================

class TestQwenVisionGrounding:
    def test_parse_qwen_grounding_point(self):
        from tools import vision
        res = vision._parse_qwen_grounding('{"found": true, "target": "Start", "point": [335, 965]}')
        assert res == (335, 965, None)

    def test_parse_qwen_grounding_point_2d(self):
        from tools import vision
        res = vision._parse_qwen_grounding('[{"point_2d": [520, 965], "label": "Chrome"}]')
        assert res == (520, 965, None)

    def test_parse_qwen_grounding_computer_use(self):
        from tools import vision
        raw = '{"name": "computer_use", "arguments": {"action": "click", "coordinate": [985, 18]}}'
        assert vision._parse_qwen_grounding(raw) == (985, 18, None)

    def test_parse_qwen_grounding_box_2d(self):
        from tools import vision
        res = vision._parse_qwen_grounding('{"box_2d": [40, 100, 80, 200]}')
        assert res == (150, 60, [40, 100, 80, 200])

    def test_parse_qwen_grounding_box_tags(self):
        from tools import vision
        res = vision._parse_qwen_grounding('<|box_start|>(40, 100), (80, 200)<|box_end|>')
        assert res == (150, 60, [40, 100, 80, 200])

    def test_parse_qwen_grounding_invalid_or_not_found(self):
        from tools import vision
        assert vision._parse_qwen_grounding('{"found": false}') is None
        assert vision._parse_qwen_grounding('') is None
        assert vision._parse_qwen_grounding('{"point": [1500, 200]}') is None

    def test_qwen_vision_locate_success(self):
        from tools import vision
        import core.brain
        import pyautogui

        with patch.object(vision, "capture_screen_base64", return_value="fakeb64"), \
             patch.object(pyautogui, "size", return_value=(1920, 1080)), \
             patch.object(core.brain, "_execute_llm_completion",
                          return_value={"content": '{"found": true, "point": [500, 500]}'}):
            res = vision._qwen_vision_locate("search bar")
            assert res == {
                "x": 960,
                "y": 540,
                "source": "qwen-vision",
                "text": "search bar",
                "score": 0.9,
            }

    def test_qwen_vision_locate_reasoning_fallback(self):
        from tools import vision
        import core.brain
        import pyautogui

        with patch.object(vision, "capture_screen_base64", return_value="fakeb64"), \
             patch.object(pyautogui, "size", return_value=(1920, 1080)), \
             patch.object(core.brain, "_execute_llm_completion",
                          return_value={"content": "", "reasoning_content": '{"found": true, "point": [500, 500]}'}):
            res = vision._qwen_vision_locate("search bar")
            assert res == {
                "x": 960,
                "y": 540,
                "source": "qwen-vision",
                "text": "search bar",
                "score": 0.9,
            }

    def test_qwen_vision_locate_not_found_or_exception(self):
        from tools import vision
        import core.brain

        with patch.object(vision, "capture_screen_base64", return_value="fakeb64"), \
             patch.object(core.brain, "_execute_llm_completion",
                          return_value={"content": '{"found": false}'}):
            assert vision._qwen_vision_locate("search bar") is None

        with patch.object(vision, "capture_screen_base64", side_effect=RuntimeError("capture failed")):
            assert vision._qwen_vision_locate("search bar") is None

    def test_locate_ui_element_ex_fallthrough(self):
        from tools import vision
        import tools.uia

        mock_result = {
            "x": 960,
            "y": 540,
            "source": "qwen-vision",
            "text": "search bar",
            "score": 0.9,
        }
        with patch.object(tools.uia, "uia_locate", return_value=None), \
             patch.object(vision, "_qwen_vision_locate", return_value=mock_result):
            res = vision.locate_ui_element_ex("search bar")
            assert res == mock_result

    def test_locate_ui_elements_boxed(self):
        from tools import vision
        import core.brain
        import pyautogui

        with patch.object(vision, "capture_screen_base64", return_value="fakeb64"), \
             patch.object(pyautogui, "size", return_value=(1000, 1000)), \
             patch.object(core.brain, "_execute_llm_completion",
                          return_value={"content": '[{"label": "btn", "box_2d": [100, 200, 200, 400]}]'}):
            boxes = vision.locate_ui_elements_boxed("btn")
            assert boxes == [{"box": (200, 100, 200, 100), "score": 0.9, "text": "btn"}]

    def test_invalidate_screen_cache_no_error(self):
        from tools import vision
        vision.invalidate_screen_cache()
