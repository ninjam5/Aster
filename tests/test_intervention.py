"""Unit tests for the Intervention Mode window classifier (ideas.md #3)."""

from tools.intervention import classify_window


def test_reddit_browser_title():
    assert classify_window("Reddit - dive into anything — Mozilla Firefox") == "Reddit"


def test_youtube_title():
    assert classify_window("(3) Some Video - YouTube - Google Chrome") == "YouTube"


def test_non_distraction_is_none():
    assert classify_window("main.py - Aster - Visual Studio Code") is None


def test_case_insensitive():
    assert classify_window("TWITCH.TV - the front page") == "Twitch"


def test_empty_and_none():
    assert classify_window("") is None
    assert classify_window(None) is None
