"""Shared goal-parsing and scoring for the UI element locator tracks.

Used by tools/uia.py (Track 0, accessibility tree) and tools/vision.py
(Track 1 OCR, Track 2 YOLO+caption). Kept dependency-free (stdlib only) so
either side can import it without pulling in cv2/torch/uiautomation.
"""
import difflib
import re

# Words that describe the *kind* or *position* of an element rather than its
# label. They dilute fuzzy matching ("the search bar at the top" should match
# a box labeled "Search", not a random line containing "the" and "at").
FILLER_WORDS = {
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "with", "my",
    "button", "icon", "menu", "field", "bar", "box", "link", "tab", "option",
    "item", "element", "control", "text", "input", "area", "section",
    "screen", "window", "page", "side", "corner", "upper", "lower",
    "top", "bottom", "left", "right", "center", "middle",
}

# Element-kind hints → UIA ControlTypeName(s) they correspond to.
TYPE_HINTS = {
    "button": {"ButtonControl", "SplitButtonControl"},
    "menu": {"MenuItemControl", "MenuControl", "MenuBarControl"},
    "tab": {"TabItemControl", "TabControl"},
    "link": {"HyperlinkControl"},
    "checkbox": {"CheckBoxControl"},
    "field": {"EditControl", "ComboBoxControl"},
    "bar": {"EditControl", "ComboBoxControl"},   # "search bar"
    "box": {"EditControl", "ComboBoxControl", "CheckBoxControl"},
    "input": {"EditControl", "ComboBoxControl"},
}

_SPATIAL_WORDS = {
    "top": "top", "upper": "top",
    "bottom": "bottom", "lower": "bottom",
    "left": "left", "right": "right",
    "center": "center", "middle": "center",
}


def parse_goal(goal: str) -> dict:
    """Split a natural-language goal into matchable text + hints.

    Returns {raw, clean, tokens, spatial, type_hints}:
      raw        — lowercased original
      clean      — original minus filler words (falls back to raw if empty)
      tokens     — set of non-filler tokens (falls back to all tokens)
      spatial    — set like {"top", "right"} parsed from position words
      type_hints — set of UIA ControlTypeNames implied by kind words
    """
    raw = (goal or "").lower().strip()
    words = re.findall(r"[a-z0-9']+", raw)

    spatial = {_SPATIAL_WORDS[w] for w in words if w in _SPATIAL_WORDS}
    type_hints: set = set()
    for w in words:
        type_hints |= TYPE_HINTS.get(w, set())

    content = [w for w in words if w not in FILLER_WORDS]
    if not content:  # goal was ALL filler ("the top bar") — keep everything
        content = words

    return {
        "raw": raw,
        "clean": " ".join(content),
        "tokens": set(content),
        "spatial": spatial,
        "type_hints": type_hints,
    }


def score_text(candidate: str, parsed: dict) -> float:
    """Fuzzy-match a candidate label against a parsed goal → 0.0-1.0.

    Same recipe the OCR track has always used (max of sequence ratio,
    token overlap, substring hit) but scored against the filler-stripped
    goal so "the search bar at the top" matches a "Search" label.
    """
    text = (candidate or "").lower().strip()
    if not text:
        return 0.0
    clean = parsed["clean"]
    tokens = parsed["tokens"]

    seq = difflib.SequenceMatcher(None, text, clean).ratio()
    cand_words = set(re.findall(r"[a-z0-9']+", text))
    overlap = len(cand_words & tokens) / max(len(tokens), 1)
    substr = 0.95 if (clean and clean in text) else 0.0
    # short candidate fully contained in the goal ("File" in "file menu")
    if text and text in clean and len(text) >= 3:
        substr = max(substr, 0.9)
    return max(seq, overlap * 0.85, substr)


def spatial_weight(cx: float, cy: float, screen_w: int, screen_h: int, spatial: set) -> float:
    """Soft position prior: 1.0 when the point agrees with every spatial hint
    in the goal, 0.8 when it contradicts one. No hints → always 1.0."""
    if not spatial or screen_w <= 0 or screen_h <= 0:
        return 1.0
    checks = {
        "top": cy < screen_h / 3,
        "bottom": cy > screen_h * 2 / 3,
        "left": cx < screen_w / 3,
        "right": cx > screen_w * 2 / 3,
        "center": (screen_w / 4 < cx < screen_w * 3 / 4) and (screen_h / 4 < cy < screen_h * 3 / 4),
    }
    return 1.0 if all(checks.get(h, True) for h in spatial) else 0.8
