"""
Automation flag Test Suite — Stage 2 (pixel fallback demotion + Florence removal).

Run: pytest tests/test_automation_flags.py -v
No models, no browser: the screen-OCR cache and the YOLO track are mocked.
"""
import os
import sys
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.vision as vision


def _fake_cap():
    return (np.zeros((12, 12, 3), dtype=np.uint8), 1920, 1080, [])


class TestPixelFallbackGate:
    def _locate(self, enabled):
        with patch("tools.uia.uia_locate", return_value=None), \
             patch("config.USE_DOM_MOTOR", False), \
             patch.object(vision, "_get_screen_ocr", return_value=_fake_cap()), \
             patch.object(vision, "_yolo_track", return_value=None) as yolo, \
             patch.object(vision, "_release_omniparser"), \
             patch("config.USE_PIXEL_FALLBACK", enabled):
            result = vision.locate_ui_element_ex("zorblatt frobnicator")
        return result, yolo

    def test_track2_runs_when_enabled(self):
        result, yolo = self._locate(True)
        assert result is None
        assert yolo.called, "default behavior must still run the pixel fallback"

    def test_track2_skipped_when_demoted(self):
        result, yolo = self._locate(False)
        assert result is None
        assert not yolo.called, "USE_PIXEL_FALLBACK=false must not load the YOLO model"

    def test_boxed_track2_gated_off(self):
        with patch.object(vision, "_get_screen_ocr", return_value=_fake_cap()), \
             patch.object(vision, "_yolo_candidates") as yc, \
             patch("config.USE_PIXEL_FALLBACK", False):
            assert vision.locate_ui_elements_boxed("zorblatt frobnicator") == []
            assert not yc.called

    def test_boxed_track2_runs_when_enabled(self):
        with patch.object(vision, "_get_screen_ocr", return_value=_fake_cap()), \
             patch.object(vision, "_yolo_candidates", return_value=[]) as yc, \
             patch.object(vision, "_release_omniparser"), \
             patch("config.USE_PIXEL_FALLBACK", True):
            assert vision.locate_ui_elements_boxed("zorblatt frobnicator") == []
            assert yc.called


class TestFlorenceRemoval:
    def test_captioner_off(self):
        with patch("config.ICON_CAPTIONER", "off"):
            assert vision._caption_crop(np.zeros((32, 32, 3), dtype=np.uint8)) == ""

    def test_florence_value_falls_back_to_gemma_without_crash(self):
        """The reserved 'florence' value is gone; a stale config must not crash."""
        import core.brain

        crop = np.zeros((32, 32, 3), dtype=np.uint8)
        with patch("config.ICON_CAPTIONER", "florence"), \
             patch.object(core.brain, "_execute_gemma_completion",
                          return_value={"role": "assistant", "content": "Settings Gear"}) as llm:
            assert vision._caption_crop(crop) == "settings gear"
        assert llm.called
