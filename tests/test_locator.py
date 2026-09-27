"""
GUI Locator Test Suite — the three-track locate_ui_element rework.

Per-feature test classes (Mohamed's standing preference):
  Feature 1 — UIA Track 0 + shared goal parsing/scoring (tools/locator_common, tools/uia)
  Feature 2 — deterministic action verification (screen diff, FAILED strings, focus safety)
  Feature 3 — Track 2 rework (cached-OCR box labels, pluggable icon captioner)
  Feature 4 — locator quality (filler stripping, spatial hints, OCR cache invalidation)
  Feature 5 — regression guards (false-positive threshold)

Run: pytest tests/test_locator.py -v
No llama-server, no live screen interaction — captioner and capture are mocked
or exercised on synthetic data. tools/uia live-tree functions are only smoke-
tested for graceful behavior, not asserted against desktop state (flaky).
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

class TestGoalParsing:
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
# Feature 3 — Track 2: cached-OCR box labels + pluggable captioner
# ============================================================================

class TestTrack2:
    def test_texts_in_box_intersection(self):
        """Line centers (2x-upscaled px) inside a physical-px YOLO box."""
        from tools.vision import _texts_in_box

        lines = [
            {"text": "play", "box": (200, 200, 240, 220)},    # center (110,105) physical
            {"text": "elsewhere", "box": (1000, 1000, 1100, 1020)},
        ]
        assert _texts_in_box(lines, (100, 95, 130, 120)) == "play"
        assert _texts_in_box(lines, (0, 0, 50, 50)) == ""
        assert _texts_in_box(None, (0, 0, 50, 50)) == ""

    def test_caption_crop_off_switch(self):
        import config
        from tools.vision import _caption_crop

        crop = np.zeros((32, 32, 3), dtype=np.uint8)
        with patch.object(config, "ICON_CAPTIONER", "off"):
            assert _caption_crop(crop) == ""

    def test_caption_crop_uses_llm_and_lowercases(self):
        import config
        import core.brain
        from tools.vision import _caption_crop

        crop = np.zeros((32, 32, 3), dtype=np.uint8)
        with patch.object(config, "ICON_CAPTIONER", "llm"), \
             patch.object(core.brain, "_execute_llm_completion",
                          return_value={"role": "assistant", "content": "  Settings Gear \n"}) as mock_llm:
            assert _caption_crop(crop) == "settings gear"
        sent = mock_llm.call_args.kwargs["messages"][0]["content"]
        assert any(blk.get("type") == "image_url" for blk in sent)

    def test_caption_crop_survives_llm_failure(self):
        import config
        import core.brain
        from tools.vision import _caption_crop

        crop = np.zeros((32, 32, 3), dtype=np.uint8)
        with patch.object(config, "ICON_CAPTIONER", "llm"), \
             patch.object(core.brain, "_execute_llm_completion", side_effect=RuntimeError("server down")):
            assert _caption_crop(crop) == ""


# ============================================================================
# Feature 4 — locator quality: OCR cache
# ============================================================================

class TestOcrCache:
    def test_invalidate_clears_cache(self):
        from tools import vision

        with vision._SCREEN_CACHE_LOCK:
            vision._SCREEN_CACHE["ts"] = 9e12
            vision._SCREEN_CACHE["frame"] = np.zeros((4, 4, 3), dtype=np.uint8)
            vision._SCREEN_CACHE["lines"] = [{"text": "x", "box": (0, 0, 1, 1)}]
        vision.invalidate_screen_cache()
        with vision._SCREEN_CACHE_LOCK:
            assert vision._SCREEN_CACHE["frame"] is None
            assert vision._SCREEN_CACHE["lines"] is None
            assert vision._SCREEN_CACHE["ts"] == 0.0

    def test_ocr_best_line_threshold_and_coords(self):
        from tools.vision import _ocr_best_line

        parsed = parse_goal("File menu")
        lines = [{"text": "file", "box": (80, 40, 120, 60)}]  # 2x-upscaled px
        r = _ocr_best_line(lines, parsed, 1920, 1080, 1.0, 1.0)
        assert r is not None and r["source"] == "ocr"
        assert (r["x"], r["y"]) == (50, 25)  # /2 upscale correction

        assert _ocr_best_line([{"text": "zzz", "box": (0, 0, 10, 10)}],
                              parsed, 1920, 1080, 1.0, 1.0) is None


# ============================================================================
# Feature 5 — regression guards
# ============================================================================

class TestRegressionGuards:
    def test_nonsense_goal_stays_below_yolo_threshold(self):
        """Live-observed false positive (2026-07-11): goal 'zorblatt
        frobnicator' fuzzy-matched '© agent router' at 0.41 under the old
        0.3 threshold. The threshold is now 0.45 and must stay above this
        pairing's score."""
        from tools.vision import _YOLO_SCORE_THRESHOLD

        score = score_text("© agent router", parse_goal("zorblatt frobnicator"))
        assert score < _YOLO_SCORE_THRESHOLD

    def test_legit_icon_captions_clear_the_threshold(self):
        from tools.vision import _YOLO_SCORE_THRESHOLD

        pairs = [
            ("settings gear icon", "settings"),
            ("close window", "close button"),
            ("play button", "play"),
            ("volume control", "the volume icon"),
        ]
        for caption, goal in pairs:
            assert score_text(caption, parse_goal(goal)) >= _YOLO_SCORE_THRESHOLD, (caption, goal)
