"""Unit tests for the reactor-face sentiment classifier (ideas.md #4)."""

from tools.sentiment import classify_sentiment


def test_music():
    assert classify_sentiment("Now playing your playlist on Spotify") == "music"


def test_alert():
    assert classify_sentiment("Error: server unreachable") == "alert"


def test_success():
    assert classify_sentiment("Done, the timer is set.") == "success"


def test_calm_default():
    assert classify_sentiment("It is 3 PM.") == "calm"


def test_alert_beats_music():
    # alert has priority over music when both keywords appear
    assert classify_sentiment("Error playing the song") == "alert"


def test_empty_is_calm():
    assert classify_sentiment("") == "calm"
    assert classify_sentiment(None) == "calm"
