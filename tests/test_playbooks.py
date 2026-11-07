"""Tests for the Two-Tier UI Playbook & Gemini 3.8 Flash Self-Healing System."""
import json
import os
from unittest.mock import patch, MagicMock
import pytest

import config
from tools import playbooks
from core import brain


# ============================================================================
# 1. PRE-SEEDED PLAYBOOKS VERIFICATION
# ============================================================================

EXPECTED_PRESEED_IDS = {
    # Desktop tier (12)
    "chrome",
    "discord",
    "stremio",
    "steam",
    "epic_games",
    "riot_client",
    "league_of_legends",
    "spotify",
    "telegram",
    "vscode",
    "file_explorer",
    "windows_shell",
    # Web tier (5)
    "web_youtube",
    "web_google",
    "web_github",
    "web_reddit",
    "web_gmail",
}


def test_all_preseeded_playbooks_load_and_validate():
    """All 17 pre-seeded playbooks exist in Aster_Vault/playbooks and match schema."""
    loaded = playbooks.list_playbooks()
    loaded_ids = {pb["id"] for pb in loaded}
    missing = EXPECTED_PRESEED_IDS - loaded_ids
    assert not missing, f"Missing pre-seeded playbooks: {missing}"

    for pb in loaded:
        if pb["id"] not in EXPECTED_PRESEED_IDS:
            continue
        assert pb.get("tier") in ("desktop", "web"), f"Invalid tier in {pb['id']}"
        assert pb.get("name"), f"Missing name in {pb['id']}"
        assert isinstance(pb.get("match"), dict), f"Missing match block in {pb['id']}"
        assert isinstance(pb.get("metadata"), dict), f"Missing metadata in {pb['id']}"
        assert pb.get("layout_summary"), f"Missing layout_summary in {pb['id']}"
        assert isinstance(pb.get("avoid_traps"), list) and len(pb["avoid_traps"]) >= 1, (
            f"Missing avoid_traps in {pb['id']}"
        )
        assert isinstance(pb.get("actions"), dict) and len(pb["actions"]) >= 1, (
            f"Missing actions in {pb['id']}"
        )


@pytest.mark.parametrize(
    "app_id,expected_action",
    [
        ("league_of_legends", "find_match"),
        ("riot_client", "launch_game"),
        ("steam", "launch_game"),
        ("epic_games", "open_library"),
        ("stremio", "search"),
        ("discord", "quick_switcher"),
        ("chrome", "new_tab"),
        ("web_youtube", "search"),
    ],
)
def test_requested_gaming_and_media_playbooks_have_core_actions(app_id, expected_action):
    """Verify specific pre-seeded apps requested by user have their key navigation actions."""
    pb = playbooks.load_playbook(app_id)
    assert pb is not None, f"Could not load {app_id}"
    assert expected_action in pb["actions"], f"{expected_action} missing from {app_id}"


# ============================================================================
# 2. TWO-TIER RESOLUTION (WEB DOMAIN > DESKTOP CONTAINER)
# ============================================================================

@pytest.mark.parametrize(
    "window_title,expected_id,expected_tier",
    [
        ("Lo-Fi Beats - YouTube - Google Chrome", "web_youtube", "web"),
        ("Aster-localization - GitHub - Microsoft Edge", "web_github", "web"),
        ("r/LocalLLaMA - Reddit - Google Chrome", "web_reddit", "web"),
        ("Inbox (3) - Gmail - Google Chrome", "web_gmail", "web"),
        ("New Tab - Google Chrome", "chrome", "desktop"),
        ("League of Legends", "league_of_legends", "desktop"),
        ("Riot Client", "riot_client", "desktop"),
        ("Steam", "steam", "desktop"),
        ("Epic Games Launcher", "epic_games", "desktop"),
        ("Stremio - Freedom to Stream", "stremio", "desktop"),
        ("#general | Friends - Discord", "discord", "desktop"),
    ],
)
def test_two_tier_window_title_resolution(window_title, expected_id, expected_tier):
    """Web domains inside browsers take priority over the host browser playbook."""
    resolved = playbooks.resolve_active_playbook(window_title=window_title)
    assert resolved is not None, f"Failed to resolve playbook for {window_title!r}"
    assert resolved["id"] == expected_id
    assert resolved["tier"] == expected_tier


def test_active_playbook_hint_includes_browser_host_shortcuts_for_web_tier():
    """When on YouTube in Chrome, the compact hint includes both YouTube actions and Browser Host Shortcuts."""
    hint = playbooks.get_active_playbook_hint(window_title="Watch - YouTube - Google Chrome")
    assert "[UI Playbook: YouTube (web_youtube) | Tier: web]" in hint
    assert "Browser Host Shortcuts:" in hint
    assert "ctrl+t" in hint
    assert "AVOID:" in hint


def test_active_playbook_hint_for_uncached_app_prompts_tool_call():
    """When an unknown app is focused, the hint advises calling get_ui_playbook."""
    hint = playbooks.get_active_playbook_hint(window_title="Blender 4.2 - untitled.blend")
    assert "None cached for" in hint
    assert "get_ui_playbook" in hint


# ============================================================================
# 3. BRAIN INTEGRATION: press_key HOTKEYS & get_ui_playbook TOOL
# ============================================================================

def test_press_key_hotkey_combination_calls_pyautogui_hotkey(monkeypatch):
    """press_key('ctrl+t') must dispatch to pyautogui.hotkey('ctrl', 't'), not pyautogui.press."""
    hotkey_calls = []
    press_calls = []
    monkeypatch.setattr(brain.pyautogui, "hotkey", lambda *args: hotkey_calls.append(args))
    monkeypatch.setattr(brain.pyautogui, "press", lambda k: press_calls.append(k))
    monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None)
    monkeypatch.setattr(brain.time, "sleep", lambda _: None)
    monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "New Tab - Google Chrome")
    monkeypatch.setattr(brain, "_attach_action_screenshot", lambda text, action: text)

    res = brain.execute_tool("press_key", {"key": "ctrl+t"})
    assert hotkey_calls == [("ctrl", "t")]
    assert press_calls == []
    assert "ctrl+t" in res

    res2 = brain.execute_tool("press_key", {"key": "ctrl+shift+m"})
    assert hotkey_calls[-1] == ("ctrl", "shift", "m")

    res3 = brain.execute_tool("press_key", {"key": "k"})
    assert press_calls == ["k"]


def test_brain_get_ui_playbook_tool_registered_and_executes():
    """get_ui_playbook is in ADMIN_TOOLS and returns formatted playbook via execute_tool."""
    tool_names = [t["function"]["name"] for t in brain.ADMIN_TOOLS]
    assert "get_ui_playbook" in tool_names

    out = brain.execute_tool("get_ui_playbook", {"app_or_site": "league of legends", "task": "find_match"})
    assert "League of Legends" in out
    assert "find_match" in out
    assert "AVOID:" in out


# ============================================================================
# 4. HEARTBEAT METADATA TRACKING (STALENESS & FAILURE SWEEPER)
# ============================================================================

def test_heartbeat_staleness_and_failure_tracking(tmp_path, monkeypatch):
    """Verify record_playbook_outcome and get_stale_or_failing_playbooks for future Heartbeat."""
    monkeypatch.setattr(config, "PLAYBOOKS_DIR", str(tmp_path))

    sample = {
        "id": "test_app",
        "tier": "desktop",
        "name": "Test App",
        "match": {"process_names": ["test.exe"], "title_keywords": ["test app"], "domains": []},
        "metadata": {
            "version": "1.0",
            "source": "preseed",
            "created_at": "2026-11-06",
            "last_verified": "2026-11-06",
            "consecutive_failures": 0,
            "usage_count": 1,
        },
        "layout_summary": "Main window.",
        "avoid_traps": ["None"],
        "actions": {"open": {"shortcut": "ctrl+o", "primary": "press_key('ctrl+o')"}},
    }
    playbooks.save_playbook(sample)

    # Fresh and 0 failures -> not flagged
    assert playbooks.get_stale_or_failing_playbooks(max_age_days=90, min_failures=2) == []

    # 2 consecutive failures -> flagged!
    playbooks.record_playbook_outcome("test_app", success=False)
    playbooks.record_playbook_outcome("test_app", success=False)
    flagged = playbooks.get_stale_or_failing_playbooks(max_age_days=90, min_failures=2)
    assert len(flagged) == 1 and flagged[0]["id"] == "test_app"

    # Success -> resets consecutive_failures to 0
    playbooks.record_playbook_outcome("test_app", success=True)
    assert playbooks.get_stale_or_failing_playbooks(max_age_days=90, min_failures=2) == []

    # Stale (>90 days old) -> flagged!
    pb = playbooks.load_playbook("test_app")
    pb["metadata"]["last_verified"] = "2025-01-01"
    playbooks.save_playbook(pb)
    flagged_stale = playbooks.get_stale_or_failing_playbooks(max_age_days=90, min_failures=2)
    assert len(flagged_stale) == 1 and flagged_stale[0]["id"] == "test_app"


# ============================================================================
# 5. LIVE GEMINI 3.8 FLASH DYNAMIC LEARNING & SELF-HEALING
# ============================================================================

def test_gemini_dynamic_learning_and_self_healing(tmp_path, monkeypatch):
    """Offline unit test: verify learning, caching, and self-healing logic without blocking on live API."""
    monkeypatch.setattr(config, "PLAYBOOKS_DIR", str(tmp_path))

    mock_generated = {
        "id": "obs_studio",
        "tier": "desktop",
        "name": "OBS Studio",
        "match": {"process_names": ["obs64.exe"], "title_keywords": ["obs"], "domains": []},
        "layout_summary": "Controls dock on bottom-right.",
        "avoid_traps": ["Do not click Start Streaming instead of Start Recording."],
        "actions": {
            "start_recording": {
                "shortcut": "",
                "primary": "smart_click('Start Recording button in Controls dock')",
                "visual_fallback": "",
                "verify": "Button changes to Stop Recording"
            }
        }
    }

    monkeypatch.setattr(playbooks, "_query_gemini_playbook", lambda *args, **kwargs: (mock_generated, ""))
    monkeypatch.setattr("tools.vision.capture_screen_base64", lambda: None)

    # 1. Mode C: Dynamic Learning
    result_learn = playbooks.get_ui_playbook(app_or_site="OBS Studio", task="start recording")
    assert "[UI Playbook Learned & Cached" in result_learn

    learned = playbooks.load_playbook("obs_studio")
    assert learned is not None
    assert learned["metadata"]["version"] == "1.0"
    assert "start_recording" in learned["actions"]

    # 2. Mode A: Self-Healing
    result_heal = playbooks.get_ui_playbook(app_or_site="obs_studio", task="start recording", failed_step="Button missed")
    assert "[UI Playbook Self-Healed" in result_heal

    healed = playbooks.load_playbook("obs_studio")
    assert healed["metadata"]["version"] == "1.1"
    assert healed["metadata"]["source"] == "self-healed"


def test_locator_anti_container_scoring():
    """Verify that locator_common uses order-independent Strict Token Coverage to reject skeleton containers."""
    from tools.locator_common import parse_goal, score_text

    # 1. For "The first Ob entry in the search results list":
    # "Search" scores < 0.55 (rejected by UIA so Qwen Vision handles it).
    p1 = parse_goal("The first Ob entry in the search results list")
    score_search = score_text("Search", p1, "EditControl")
    assert score_search < 0.55

    # Target "Ob" against candidate "Ob the_ob" (full target contained in candidate) scores >= 0.90
    p_ob = parse_goal("Ob")
    score_ob_cand = score_text("Ob the_ob", p_ob, "ListItemControl")
    assert score_ob_cand >= 0.90

    # 2. Order-independence: container header "Direct Messages" ALWAYS scores < 0.55 (rejected by UIA)
    goals = [
        "Ob in Direct Messages",
        "Direct Messages: Ob",
        "Click the Direct Messages conversation with Ob",
    ]
    for g in goals:
        p = parse_goal(g)
        score_dm = score_text("Direct Messages", p, "TextControl")
        assert score_dm < 0.55, f"Direct Messages on {g!r} scored {score_dm} >= 0.55"

    # 3. Whereas "Search" for "the search bar at the top" scores >= 0.90
    p_search = parse_goal("the search bar at the top")
    assert score_text("Search", p_search, "EditControl") >= 0.90

    # 4. And "File" for "File menu" scores >= 0.90
    p_file = parse_goal("File menu")
    assert score_text("File", p_file, "MenuItemControl") >= 0.90


def test_type_text_press_enter_support(monkeypatch):
    """Verify that type_text supports press_enter=True and triggers Enter keypress."""
    import core.brain as brain
    pressed = []
    copied = []
    monkeypatch.setattr(brain.pyautogui, "press", lambda k: pressed.append(k))
    monkeypatch.setattr(brain.pyautogui, "hotkey", lambda *a: None)
    monkeypatch.setattr(brain.pyperclip, "copy", lambda t: copied.append(t))
    monkeypatch.setattr(brain, "invalidate_screen_cache", lambda: None)
    monkeypatch.setattr(brain, "_attach_action_screenshot", lambda text, action: text)
    monkeypatch.setattr(brain, "get_foreground_window_title", lambda: "Discord")

    res = brain.execute_tool("type_text", {"text": "Ob", "press_enter": True})
    assert copied == ["Ob"]
    assert pressed == ["enter"]
    assert "pressed Enter" in res

