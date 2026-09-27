"""Tests for the hygiene items in the reliability campaign:
  - think-tag stripping (core.brain._strip_think_tags)
  - /tokenize-calibrated token counting (core.memory)

No llama-server required — requests.post is mocked for the /tokenize calls.
"""
from unittest.mock import MagicMock, patch

import pytest

import config
import core.memory as memory
from core.brain import _strip_think_tags


# ── Think-tag stripping ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("<think>internal reasoning</think>Hello there.", "Hello there."),
    ("<thinking>plan the steps</thinking>Done.", "Done."),
    ("No tags here at all.", "No tags here at all."),
    ("<THINK>case insensitive</THINK>Result.", "Result."),
    ("Prefix <think>middle block</think> suffix.", "Prefix suffix."),
    (
        "<think>first</think>middle<think>second</think>end",
        "middleend",
    ),
])
def test_strip_paired_think_blocks(text, expected):
    assert _strip_think_tags(text) == expected


def test_unterminated_leading_think_tag_left_intact(capsys):
    text = "<think>never closed, rest of the response"
    result = _strip_think_tags(text)
    assert result == text  # left intact, not silently eaten
    captured = capsys.readouterr()
    assert "unterminated" in captured.out.lower()


def test_no_false_positive_on_word_think():
    text = "I think <b>this</b> is fine and should not be touched."
    assert _strip_think_tags(text) == text


def test_empty_and_none_input():
    assert _strip_think_tags("") == ""
    assert _strip_think_tags(None) is None


# ── /tokenize-calibrated token counting ──────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_ratio_cache():
    memory._token_ratio["value"] = 4.0
    memory._token_ratio["last_calibrated"] = 0.0
    yield
    memory._token_ratio["value"] = 4.0
    memory._token_ratio["last_calibrated"] = 0.0


def _mock_tokenize_response(n_tokens):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"tokens": list(range(n_tokens))}
    return resp


def test_tokenize_count_success():
    with patch("requests.post", return_value=_mock_tokenize_response(10)):
        assert memory._tokenize_count("some text") == 10


def test_tokenize_count_failure_returns_none():
    with patch("requests.post", side_effect=ConnectionError("down")):
        assert memory._tokenize_count("some text") is None


def test_tokenize_count_empty_text_short_circuits():
    with patch("requests.post") as mock_post:
        assert memory._tokenize_count("") == 0
        mock_post.assert_not_called()


def test_estimate_tokens_text_heuristic_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "EXACT_TOKEN_COUNT", False)
    with patch("requests.post") as mock_post:
        result = memory.estimate_tokens_text("a" * 40)
        mock_post.assert_not_called()
        assert result == 10  # 40 chars / 4.0 default ratio


def test_estimate_tokens_text_calibrates_when_near_budget(monkeypatch):
    monkeypatch.setattr(config, "EXACT_TOKEN_COUNT", True)
    text = "a" * 400
    # Heuristic estimate = 400/4.0 = 100 > budget*0.75 (75) -> near budget, calibrates.
    # Exact count says 80 tokens for 400 chars -> new ratio 5.0
    with patch("requests.post", return_value=_mock_tokenize_response(80)) as mock_post:
        result = memory.estimate_tokens_text(text, budget=100)
        mock_post.assert_called_once()
        assert result == 80
        assert memory._calibrated_ratio() == 5.0


def test_estimate_tokens_text_skips_calibration_when_far_from_budget(monkeypatch):
    monkeypatch.setattr(config, "EXACT_TOKEN_COUNT", True)
    text = "a" * 40
    with patch("requests.post") as mock_post:
        result = memory.estimate_tokens_text(text, budget=100000)  # heuristic 10, nowhere near budget
        mock_post.assert_not_called()
        assert result == 10


def test_calibration_rate_limited(monkeypatch):
    monkeypatch.setattr(config, "EXACT_TOKEN_COUNT", True)
    text = "a" * 400
    with patch("requests.post", return_value=_mock_tokenize_response(80)) as mock_post:
        memory.estimate_tokens_text(text, budget=100)
        memory.estimate_tokens_text(text, budget=100)
        memory.estimate_tokens_text(text, budget=100)
    assert mock_post.call_count == 1  # second/third calls hit the 60s cache


def test_calibration_failure_falls_back_gracefully(monkeypatch):
    monkeypatch.setattr(config, "EXACT_TOKEN_COUNT", True)
    text = "a" * 400
    with patch("requests.post", side_effect=ConnectionError("down")):
        result = memory.estimate_tokens_text(text, budget=100)
        assert result == 100  # 400/4.0 default ratio stays in effect


def test_image_parts_charged_flat_estimate_not_base64_length():
    huge_b64 = "A" * 200000
    msg_list = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{huge_b64}"}},
            {"type": "text", "text": "describe this"},
        ]},
    ]
    text, image_count = memory._extract_text_and_image_count(msg_list)
    assert image_count == 1
    assert huge_b64 not in text
    assert "describe this" in text
