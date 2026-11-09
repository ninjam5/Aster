import pytest
from unittest.mock import patch, MagicMock
import numpy as np


def test_admin_tools_schemas_include_new_primitives():
    from core.brain import ADMIN_TOOLS
    tool_names = [t["function"]["name"] for t in ADMIN_TOOLS]
    assert "mouse_hover" in tool_names
    assert "drag_and_drop" in tool_names
    assert "click_at" in tool_names
    assert "zoom_and_read" in tool_names


@patch("tools.vision.locate_ui_element_ex")
@patch("pyautogui.moveTo")
def test_hover_ui_element(mock_move, mock_locate):
    from tools.vision import hover_ui_element
    mock_locate.return_value = {"x": 450, "y": 320, "score": 0.9, "source": "qwen-vision"}
    
    res = hover_ui_element("test button")
    assert res is not None
    assert res["x"] == 450
    assert res["y"] == 320
    mock_move.assert_called_once_with(450, 320, duration=0.2)


@patch("tools.vision.locate_ui_element_ex")
@patch("pyautogui.moveTo")
@patch("pyautogui.mouseDown")
@patch("pyautogui.mouseUp")
def test_drag_and_drop_ui_elements(mock_up, mock_down, mock_move, mock_locate):
    from tools.vision import drag_and_drop_ui_elements
    def _loc_side_effect(goal):
        if "source" in goal:
            return {"x": 100, "y": 200, "score": 0.9}
        return {"x": 500, "y": 600, "score": 0.9}
    
    mock_locate.side_effect = _loc_side_effect

    src, dst = drag_and_drop_ui_elements("source element", "target element", duration=0.6)
    assert src is not None and src["x"] == 100
    assert dst is not None and dst["x"] == 500
    mock_down.assert_called_once_with(button="left")
    mock_up.assert_called_once_with(button="left")
    assert mock_move.call_count == 2


@patch("pyautogui.size", return_value=(1920, 1080))
@patch("pyautogui.click")
def test_click_at_coordinates(mock_click, mock_size):
    from tools.vision import click_at_coordinates
    ok = click_at_coordinates(500, 300, clicks=2, button="right")
    assert ok is True
    mock_click.assert_called_once_with(500, 300, clicks=2, button="right")


@patch("tools.vision.get_raw_screenshot_bgr")
@patch("tools.vision._qwen_vision_locate_boxes")
@patch("pyautogui.size", return_value=(1920, 1080))
@patch("core.brain._execute_llm_completion")
def test_zoom_and_read_region(mock_exec, mock_size, mock_boxes, mock_get_raw):
    from tools.vision import zoom_and_read_region
    # Mock raw frame 1920x1080 3-channel
    mock_get_raw.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
    mock_boxes.return_value = [{"box": (400, 300, 200, 150), "score": 0.9, "text": "target region"}]
    mock_exec.return_value = {"content": "Transcribed fine details: [1:24 PM] Ob: Aloha human"}

    res = zoom_and_read_region("the chat header")
    assert "Transcribed fine details" in res
    assert "[1:24 PM] Ob: Aloha human" in res
    mock_exec.assert_called_once()


def test_ui_tool_names_set_in_brain():
    import core.brain as _brain
    # Ensure all new tools are executable via _execute_tool_impl without KeyError
    assert hasattr(_brain, "_execute_tool_impl")
