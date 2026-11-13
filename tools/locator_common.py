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
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "with", "from", "by", "my",
    "button", "icon", "menu", "field", "bar", "box", "link", "tab", "option",
    "item", "element", "control", "text", "input", "area", "section",
    "screen", "window", "page", "side", "corner", "upper", "lower",
    "top", "bottom", "left", "right", "center", "middle",
    "app", "application", "shortcut", "launcher", "desktop",
    "open", "launch", "click", "find", "select", "press",
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
    "icon": {"ListItemControl", "ButtonControl"},
    "app": {"ListItemControl", "ButtonControl"},
    "application": {"ListItemControl", "ButtonControl"},
    "shortcut": {"ListItemControl", "ButtonControl"},
    "launcher": {"ListItemControl", "ButtonControl"},
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
      raw              — lowercased original
      clean            — original minus filler words (falls back to raw if empty)
      tokens           — set of non-filler tokens (falls back to all tokens)
      spatial          — set like {"top", "right"} parsed from position words
      type_hints       — set of UIA ControlTypeNames implied by kind words
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

    is_desktop = bool(re.search(r"\b(?:desktop|wallpaper)\b", raw))

    return {
        "raw": raw,
        "clean": " ".join(content),
        "tokens": set(content),
        "spatial": spatial,
        "type_hints": type_hints,
        "is_desktop": is_desktop,
    }


def score_text(candidate: str, parsed: dict, cand_type: str = "", cand_source: str = "") -> float:
    """Fuzzy-match a candidate label against a parsed goal → 0.0-1.0.

    Scores candidate text against the filler-stripped goal. Uses strict token
    coverage so generic container labels never hijack goals that name a specific subject.
    Prioritizes authentic desktop shortcuts and penalizes active window controls for desktop goals.
    """
    text = (candidate or "").lower().strip()
    if not text:
        return 0.0

    clean = parsed.get("clean", "")
    tokens = parsed.get("tokens", set())

    cand_words_list = re.findall(r"[a-z0-9']+", text)
    cand_clean = " ".join([w for w in cand_words_list if w not in FILLER_WORDS])

    if text == clean or (cand_clean and cand_clean == clean):
        score = 1.0
    else:
        cand_words = set(cand_words_list)
        overlap = len(cand_words & tokens) / max(len(tokens), 1)
        seq = difflib.SequenceMatcher(None, text, clean).ratio()

        # Case 2: Entire goal contained in candidate (e.g. "save" in "save as", "ob" in "ob the_ob")
        substr = 0.95 if (clean and (clean in text or clean in cand_clean)) else 0.0

        # Case 3: Candidate is a substring of goal (e.g. "Search" in "the search bar")
        if (text and text in clean and len(text) >= 2) or (cand_clean and cand_clean in clean and len(cand_clean) >= 2):
            if overlap >= 0.75:
                substr = max(substr, 0.90)
            else:
                substr = 0.0

        if overlap < 0.75 and not (clean and (clean in text or clean in cand_clean)):
            seq = min(seq, overlap * 0.70)
            token_score = overlap * 0.70
        else:
            token_score = overlap * 0.85

        score = max(seq, token_score, substr)

    # Penalize non-interactive container control types unless specifically asked for
    if cand_type in ("GroupControl", "WindowControl", "PaneControl") and not (
        parsed.get("type_hints") and cand_type in parsed["type_hints"]
    ):
        score = max(0.0, score - 0.30)

    # Control Type Disambiguation: when goal names an 'icon', 'app', 'application',
    # 'shortcut', 'launcher', or 'button', generic TextControl body text inside
    # chat/document windows must be penalized (-0.45 or rejected) so passive text
    # messages never outscore real launcher controls (ListItemControl on desktop,
    # ButtonControl/ToolBarControl on taskbar).
    raw_goal = parsed.get("raw", "")
    goal_words = set(re.findall(r"[a-z0-9']+", raw_goal.lower())) if raw_goal else set()
    if not goal_words:
        goal_words = set(re.findall(r"[a-z0-9']+", parsed.get("clean", "").lower())) | set(parsed.get("tokens", set()))

    disambig_words = {"icon", "app", "application", "shortcut", "launcher", "button"}
    if cand_type in ("TextControl", "DocumentControl", "Text") and (goal_words & disambig_words):
        score = max(0.0, score - 0.45)

    # Desktop Target Prioritization: when goal queries a desktop icon ('desktop',
    # 'on the desktop', 'desktop icon', 'desktop shortcut'), strictly prioritize
    # candidates from source == 'desktop', and heavily penalize or exclude internal
    # application window controls (such as Electron/Telegram chat text, buttons,
    # and listitems from Antigravity/Chrome/Telegram) so open application windows
    # cannot hijack desktop goals.
    is_desktop = parsed.get("is_desktop") or bool(re.search(r"\b(?:desktop|wallpaper)\b", raw_goal))
    if is_desktop:
        if cand_source == "desktop":
            score = min(1.0, score + 0.15)
        elif cand_source in ("window", "app", "taskbar") or (cand_source and cand_source != "desktop"):
            score = max(0.0, score - 0.60)

    return score


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
