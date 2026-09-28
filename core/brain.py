import sys
import os
import re
import json
import difflib
import time
import requests
import pyautogui
import pyperclip
import config
from core.memory import (memorize_fact, recall_memory, trim_memory, log_raw_turn,
                         estimate_tokens_text, _estimate_tokens as _estimate_msgs_tokens)
from core import instrumentation
from core.loop_guard import LoopGuard
from core.tool_result import ToolResult, tool_fail
from core.output_compression import compress_tool_output, set_summarizer as _set_output_summarizer
from tools.media import (
    get_current_track, pause_spotify, resume_spotify, play_spotify_track,
    play_spotify_playlist, play_liked_songs, skip_spotify_track,
    previous_spotify_track, shuffle_spotify, set_timer, set_alarm,
)
from tools.system import (
    get_current_time, read_local_file, write_local_file, set_system_state,
    set_volume, boss_key, open_application, aster_shutdown_protocol,
    list_directory_tree, list_running_processes,
)
from tools.vision import (
    capture_webcam_base64, capture_screen_base64, save_image_to_cold_storage,
    re_examine_image, locate_ui_element, locate_ui_element_ex, locate_ui_elements_boxed,
    verify_action_result, invalidate_screen_cache, capture_screen_small_gray, screens_differ,
)
from tools.uia import get_foreground_window_title, focused_control_type, EDITABLE_CONTROL_TYPES
from tools.assist import highlight_regions
from tools.discord_api import CONTACTS, send_discord_message
from tools.memory_manager import (
    get_user_facts,
    save_fact as save_discord_fact,
    sync_unsynced_facts,
)
from tools.rag import research
from tools.audio import generate_kokoro_voice
from tools.notes import save_note, get_notes
from tools.realtime_stream import publish_terminal, publish_sentiment
from tools.sentiment import classify_sentiment
from tools import intervention
from tools import awareness
from tools.self_knowledge import (
    get_my_config, list_my_contacts, list_my_capabilities,
    get_my_status, get_my_memory_stats, describe_my_tool,
)
import tools.google_auth as google_auth
from tools.gmail_tool import (
    summarize_unread_emails, search_emails, read_email,
    archive_email, mark_email_read, label_email, reply_to_email,
)
from tools.google_calendar import (
    list_upcoming_events, create_calendar_event, update_calendar_event, respond_to_invite,
)

# Reasoning-scaffold stripping (hygiene item, reliability campaign). Paired
# <think>/<thinking> blocks only — an unterminated leading tag is left intact
# rather than silently eating the rest of the response.
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>\s*", re.DOTALL | re.IGNORECASE)
_THINK_LEAD_UNCLOSED_RE = re.compile(r"^\s*<think(?:ing)?>", re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    if not text:
        return text
    stripped = _THINK_RE.sub("", text)
    if stripped == text and _THINK_LEAD_UNCLOSED_RE.match(text):
        print("[Aster Internal: unterminated <think> tag detected — left intact, not stripped]")
    return stripped


# ============================================================================
# NATIVE LLM REST ADAPTER — OpenAI-format chat completions with mmproj vision
# ============================================================================
def _execute_llm_completion(messages: list[dict], temperature: float = None, n_predict: int = 1000, top_p: float = None, top_k: int = None, tools: list[dict] | None = None) -> dict:
    """Sends messages in OpenAI chat format to llama-server /v1/chat/completions.

    Returns the full choices[0]["message"] dict (keys: role, content, tool_calls).
    Callers that only need text do: (response_msg.get("content") or "").strip()
    Callers that need tool calls inspect: response_msg.get("tool_calls")

    Pass tools=ADMIN_TOOLS (or any subset) to enable native function calling.

    temperature/top_p/top_k default to None so they are resolved from config.*
    at call time (not import time). Callers that pass an explicit value (e.g.
    vision tasks using temperature=0.2) are unaffected.
    """
    if temperature is None:
        temperature = config.LLM_TEMPERATURE
    if top_p is None:
        top_p = config.LLM_TOP_P
    if top_k is None:
        top_k = config.LLM_TOP_K
    print(f'[PIPELINE DEBUG] _execute_llm_completion called with {len(messages)} messages, last message role={messages[-1]["role"]}, content type={type(messages[-1]["content"]).__name__}, has_list={isinstance(messages[-1]["content"], list)}')

    payload = {
        "model": "local",
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "max_tokens": n_predict,
        "stream": False,
    }
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    # --- Diagnostic payload printer (Base64 truncated for readability) ---
    try:
        import copy
        debug_payload = copy.deepcopy(payload)
        for msg in debug_payload.get("messages", []):
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if len(url) > 60:
                            item["image_url"]["url"] = url[:40] + "... [TRUNCATED FOR LOGS]"
        print("\n=== [ASTER DEBUG: CHAT COMPLETIONS PAYLOAD] ===")
        print(json.dumps(debug_payload, indent=2))
        print("==================================================\n")
    except Exception:
        pass

    try:
        response = requests.post("http://localhost:8080/v1/chat/completions", json=payload, timeout=120)
        try:
            # ASCII-sanitized: the body can carry arbitrary Unicode (emoji, "♡", CJK)
            # that the Windows console codepage cannot encode — an unguarded print
            # raises UnicodeEncodeError and kills the turn.
            _dbg_body = response.text[:500].encode("ascii", "replace").decode("ascii")
            print(f'[HTTP DEBUG] status={response.status_code} body[:500]={_dbg_body}')
        except Exception:
            pass
        response.raise_for_status()
        result = response.json()
        msg = result["choices"][0]["message"]
        if msg.get("content") is not None:
            msg["content"] = msg["content"].strip()
            if config.STRIP_THINK_TAGS:
                msg["content"] = _strip_think_tags(msg["content"])
        return msg
    except requests.exceptions.Timeout:
        raise Exception("Chat API Error: Request timed out after 120s")
    except requests.exceptions.ConnectionError:
        raise Exception("Chat API Error: Could not connect to llama-server at localhost:8080")
    except Exception as e:
        raise Exception(f"Chat API Error: {e}")


# Register the completion function as the summarizer for the 'research' tool's
# output-compression policy (core/output_compression.py) — injected rather than
# imported there to avoid a core.brain <-> core.output_compression cycle.
_set_output_summarizer(_execute_llm_completion)


_CUTOFF_REFUSAL_RE = re.compile(
    r"knowledge\s+(base\s+)?(?:is\s+)?(?:restricted|cutoff|cut.off|limited)|"
    r"prior\s+to\s+(mid.)?20(2[012]|1\d)|"
    r"not\s+(?:yet\s+)?(?:widely\s+)?(?:documented|available|published|released)\b|"
    r"(?:my|the)\s+(?:training\s+)?(?:data|knowledge|information)\s+(?:does\s+not|doesn'?t|is\s+not)\s+(?:include|cover|have|extend)|"
    r"(?:unavailable|inaccessible)\s+in\s+(?:my|the)\s+(?:current\s+)?(?:dataset|knowledge|database)|"
    r"cannot\s+(?:confirm|verify|provide)\s+(?:this|that|any|the)?\s*(?:information|data|specs?)\b|"
    # Added 2026-09-28 after the live refusal slipped through both nets:
    # "I cannot provide that information as my knowledge is not current enough..."
    r"not\s+(?:current|up.to.date|updated|recent)\s+enough|"
    r"(?:my|the)\s+(?:training\s+)?(?:data|knowledge|information)\s+(?:is|isn'?t|was)\s+(?:not\s+)?(?:current|up.to.date|updated|recent|outdated)|"
    r"(?:training\s+)?(?:data|knowledge)\s+(?:only\s+)?(?:goes|extends|reaches|stretches)\s+(?:up\s+)?(?:to|through)|"
    r"doesn'?t\s+go\s+(?:quite\s+)?that\s+far|"
    r"my\s+(?:research|lookup)\s+(?:turned\s+up|found|shows?|revealed)\s+(?:nothing|no\s+specific)",
    re.IGNORECASE,
)


# Added 2026-09-28: the model told the owner "I'm afraid I cannot directly execute a
# web search. I can only access information from my training data." — a flat denial of
# a capability the tools DO provide (research / browse_web). Nothing detected it.
_CAPABILITY_DENIAL_RE = re.compile(
    r"(?:i\s+)?(?:can'?t|cannot|can\s+not|am\s+unable\s+to|don'?t\s+have)\s+"
    r"(?:directly\s+|actually\s+|currently\s+)?"
    r"(?:execute|perform|run|do|access|use|browse|search)\s+"
    r"(?:a\s+|the\s+|any\s+)?(?:web\s+search|internet|online|web|browser|live\s+web|external\s+sites?)|"
    r"(?:i\s+)?(?:don'?t|do\s+not)\s+have\s+(?:access\s+to\s+)?(?:the\s+)?(?:internet|web|live\s+web|a\s+browser)|"
    r"(?:i\s+)?(?:can\s+only|am\s+limited\s+to)\s+(?:access|use)\s+(?:information\s+)?(?:from\s+)?(?:my\s+)?(?:training\s+data|training|knowledge\s+base)|"
    r"(?:i'?m|i\s+am)\s+afraid\s+i\s+cannot\s+(?:directly\s+)?(?:execute|perform|access)|"
    r"i\s+do\s+not\s+have\s+the\s+ability\s+to\s+(?:search|browse|access\s+the\s+web)",
    re.IGNORECASE,
)


def _claims_knowledge_cutoff(response_text: str) -> bool:
    """Detect when the model refuses to answer by citing its knowledge cutoff.

    Returns True if the response contains a cutoff-refusal pattern, indicating
    The model gave up on a question instead of calling the research tool first.
    """
    return bool(_CUTOFF_REFUSAL_RE.search(response_text or ""))


def _denies_capability(response_text: str) -> bool:
    """Detect a flat denial of a capability Aster's tools provide (web search,
    browsing, the screen, the webcam...). Such denials are always wrong — the
    correct move is a tool call — and they poison later turns, which imitate
    the denial. Returns True when the reply claims it cannot do these things."""
    return bool(_CAPABILITY_DENIAL_RE.search(response_text or ""))


def _claims_tool_execution(user_text: str, response_text: str, message: dict = None) -> bool:
    """Detect when the model claims to have executed a tool without actually calling one.

    Returns True if the user's request clearly requires a tool AND the model's
    response claims to have performed that action without an actual tool call.

    Pass message=<completion message dict> when using native tool calling so
    the function can short-circuit when tool_calls is already populated.
    """
    if not response_text:
        return False

    # Native path: if tool_calls is present the model already called the tool — not a hallucination
    if message is not None and message.get("tool_calls"):
        return False

    # Signal: User asked for a specific action, model claimed execution
    action_checks = [
        # Spotify — explicit context keywords
        (r"\bplay\b.*\bon\s+spotify\b", r"\b(?:playing|played|queued|hitting\s*play|starting|initiating)\b"),
        (r"\bplay\b.*\b(?:the\s+)?(?:song|track|music)\b", r"\b(?:playing|played|queued|hitting|initiating)\b"),
        # Spotify — bare "Play <title>" with no spotify/song/track keyword
        (r"^play\b", r"(?:\b(?:initiating|starting)\s+(?:the\s+)?playback\b|\bi\s+(?:shall|will)(?:\s+now)?\s+play\b)"),
        # Screen
        (r"\bwhat'?s\s+on\s+(?:my|the)\s+screen\b", r"\b(?:grabbing|parsing|taking|screenshot|captured)\b"),
        (r"\btake\s+(?:a\s+)?screenshot\b", r"\b(?:grabbing|parsing|taking|captured)\b"),
        # Timer / Alarm
        (r"\bremind\s+me\b", r"\b(?:setting|timer|reminder|remind)\b"),
        (r"\bset\s+(?:a\s+)?timer\b", r"\b(?:setting|timer)\b"),
        (r"\bset\s+(?:an?\s+)?alarm\b", r"\b(?:setting|alarm)\b"),
        # App launching
        (r"\bopen\s+\w+\b", r"\b(?:launching|opening|starting|launched|opened)\b"),
        # Research / live web — the model narrates a search ("I'll search...",
        # "my research turned up...") without emitting a tool call. Added 2026-09-28:
        # the live turn said "I'll search for information about this meeting" with 0
        # tool calls and nothing caught it.
        (
            r"\b(?:who|what|when|where|which|did|does|do|is|are|was|were|how|why|"
            r"latest|news|current|search|research|look\s+up|find\s+out)\b",
            r"\b(?:let\s+me|i'?ll|i\s+will|i'?m\s+going\s+to|i\s+am\s+going\s+to|"
            r"allow\s+me\s+to)\s+(?:go\s+ahead\s+and\s+)?"
            r"(?:search|look\s+(?:it|this|that|them)?\s*up|research|check|find\s+(?:out|that|this))\b"
            r"|\bmy\s+research\s+(?:turned\s+up|found|shows?|revealed)\b"
            r"|\bi'?ll\s+search\b|\bsearching\s+(?:now|for)\b",
        ),
    ]

    user_lower = (user_text or "").lower()
    resp_lower = response_text.lower()

    for user_pat, resp_pat in action_checks:
        if re.search(user_pat, user_lower) and re.search(resp_pat, resp_lower):
            return True

    return False


# Tool results signalling a failed action start with "FAILED" (Spotify tools)
# or "Error" (generic execute_tool wrapping). Matched case-insensitively at the
# start of the result so ordinary prose mentioning "error" doesn't trip it.
_TOOL_FAILURE_RE = re.compile(r"\s*(?:FAILED|Error)\b", re.IGNORECASE)

_FAILURE_ACK_RE = re.compile(
    r"\b(?:fail(?:ed|ure|ing)?|error|unable|couldn'?t|could\s+not|can'?t|cannot|"
    r"wasn'?t\s+able|not\s+(?:able|working|running|open|responding)|"
    r"didn'?t\s+(?:work|go\s+through)|(?:seems?|appears?)\s+(?:to\s+be\s+)?closed|"
    r"no\s+active|offline|refus\w*|issue|problem|trouble|apolog\w*|unfortunately)\b",
    re.IGNORECASE,
)


def _tool_result_failed(tool_result) -> bool:
    """True when a tool's return value indicates the action did not happen."""
    text = tool_result.get("text", "") if isinstance(tool_result, dict) else str(tool_result)
    return bool(_TOOL_FAILURE_RE.match(text))


def _acknowledges_failure(response_text: str) -> bool:
    """True when the model's reply admits something went wrong.

    Used to catch the opposite of a hallucinated call: a tool genuinely ran,
    FAILED, and the model is about to report success anyway.
    """
    return bool(_FAILURE_ACK_RE.search(response_text or ""))


def _extract_native_tool_call(message: dict) -> tuple[dict | None, str]:
    """Read tool_calls[0] from a native OpenAI-format completion message.

    Returns (payload_dict, content_str).
    payload_dict includes "tool_call_id" for constructing the role:tool reply.
    """
    tc_list = message.get("tool_calls") or []
    content = (message.get("content") or "").strip()
    if not tc_list:
        return None, content
    tc = tc_list[0]
    raw_args = tc["function"].get("arguments", "{}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        return {
            "name": "", "arguments": {}, "error": "json_decode_error",
            "json_error": True, "tool_call_id": tc.get("id", ""),
        }, content
    if not isinstance(args, dict):
        args = {}
    return {
        "name": tc["function"]["name"], "arguments": args,
        "error": None, "tool_call_id": tc.get("id", ""),
    }, content


def _extract_all_native_tool_calls(message: dict) -> list[dict]:
    """Read all tool_calls from a native completion message.

    Used by evaluate_and_memorize for multi-fact extraction in one pass.
    """
    results = []
    for tc in (message.get("tool_calls") or []):
        raw_args = tc["function"].get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            print(f"[Aster Internal: _extract_all_native_tool_calls skipped bad JSON in {tc['function']['name']}]")
            continue
        if not isinstance(args, dict):
            args = {}
        results.append({
            "name": tc["function"]["name"], "arguments": args,
            "tool_call_id": tc.get("id", ""),
        })
    return results


# ============================================================================
# TOKEN MONITOR
# ============================================================================
def check_context_health():
    """Returns an estimated token count (calibrated against /tokenize when
    config.EXACT_TOKEN_COUNT is on — see core.memory.estimate_tokens_text —
    else the fixed ~4 chars/token heuristic)."""
    try:
        combined = " ".join(
            "".join(
                p.get("text", "") if isinstance(p, dict) else str(p)
                for p in (m.get("content") if isinstance(m.get("content"), list) else [m.get("content", "")])
            )
            for m in messages
        )
        max_tokens = config.N_CTX
        est_tokens = estimate_tokens_text(combined, budget=max_tokens)
        percentage = round((est_tokens / max_tokens) * 100, 2)
        return f"Current Context Load: ~{est_tokens}/{max_tokens} tokens ({percentage}% capacity)."
    except Exception as e:
        return f"Failed to check context health: {e}"


# ============================================================================
# TOOL SCHEMAS (JSON for OpenAI-compatible function calling)
# ============================================================================
ADMIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Returns the current local system time in YYYY-MM-DD HH:MM:SS format.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_local_file",
            "description": "Reads the contents of a local file and returns its text content. IMPORTANT: If you are not sure of the exact file path, call list_directory_tree FIRST to locate the file, then call read_local_file with the correct path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute or relative path to the file to read.",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_local_file",
            "description": "Writes content to a local file, creating it if it does not exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute or relative path to the file to write.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content to write to the file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_system_state",
            "description": "Controls system power states: shutdown, restart, sleep, or lock the computer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The power action: 'shutdown', 'restart', 'sleep', or 'lock'.",
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Sets the system master volume to a percentage (0-100), or mutes/unmutes the system. Use this whenever the user asks to mute, unmute, silence, or adjust volume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level_percentage": {
                        "type": "integer",
                        "description": "Volume level from 0 to 100.",
                    },
                    "mute": {
                        "type": "boolean",
                        "description": "If true, toggles mute instead of setting level.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "boss_key",
            "description": "Minimizes all windows and shows the desktop immediately.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Opens a specified application on the computer. Can also perform media controls after opening.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the application to open (e.g., 'spotify', 'slack', 'msedge', 'notepad').",
                    },
                    "action": {
                        "type": "string",
                        "description": "Optional action after opening: 'play', 'pause', 'next', 'previous', 'volume_up', 'volume_down', 'mute'.",
                    },
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_track",
            "description": "Returns the current playing song on Spotify with artist name.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_spotify",
            "description": "Pauses the current Spotify playback.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_spotify",
            "description": "Resumes Spotify playback.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify_track",
            "description": "Searches Spotify for a track and plays it. Auto-launches the Spotify app if it is not running. On success the result says 'Playing <track> by <artist>'; a result starting with FAILED means NOTHING is playing — never tell the user a song is playing unless the result confirms it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for the track (e.g., 'Bohemian Rhapsody Queen').",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify_playlist",
            "description": "Plays one of the user's Spotify playlists by name. Auto-launches the Spotify app if needed; a result starting with FAILED means nothing is playing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "playlist_name": {
                        "type": "string",
                        "description": "The name of the playlist to play (e.g., 'Jhin', 'Chill Vibes').",
                    }
                },
                "required": ["playlist_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_liked_songs",
            "description": "Plays the user's liked/saved songs on Spotify. Auto-launches the Spotify app if needed; a result starting with FAILED means nothing is playing.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_spotify_track",
            "description": "Skips to the next track on Spotify.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "previous_spotify_track",
            "description": "Goes back to the previous track on Spotify.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shuffle_spotify",
            "description": "Enables or disables shuffle mode on Spotify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to enable shuffle, false to disable.",
                    }
                },
                "required": ["state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": (
                "Sets a background timer that fires a native notification when the duration elapses. "
                "Use this for any timer, countdown, or reminder request ('remind me in X minutes', "
                "'alert me in X hours', etc.). "
                "Accepts any combination of time units — supply whichever fields are natural: "
                "minutes=5.5, or minutes=5 and seconds=30, or hours=1, or a plain duration string "
                "like '1 hour 30 minutes'. All unit fields are additive."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Hours component (e.g. 1 for one hour).",
                    },
                    "minutes": {
                        "type": "number",
                        "description": "Minutes component (decimals allowed, e.g. 5.5 for 5m 30s).",
                    },
                    "seconds": {
                        "type": "number",
                        "description": "Seconds component (e.g. 30). Use alongside minutes for sub-minute precision.",
                    },
                    "duration": {
                        "type": "string",
                        "description": "Natural-language duration string, e.g. '5 minutes 30 seconds' or '1h 30m'. Used instead of the numeric fields.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Label shown when the timer fires (e.g. 'Take medication').",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_alarm",
            "description": "Sets an alarm for a specific time of day (e.g. '7:30 AM' or '14:00'). Fires a native Windows notification when the time arrives.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_str": {
                        "type": "string",
                        "description": "The target time as a string, e.g. '7:30 AM', '9:00 PM', or '14:00'.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Label shown when the alarm fires (e.g. 'Wake up', 'Meeting').",
                    },
                },
                "required": ["time_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memorize_fact",
            "description": "Saves an important fact about the user, their preferences, or a project to long-term memory. Use this whenever the user tells you something worth remembering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The fact or piece of information to store permanently.",
                    }
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "Searches long-term memory for relevant context about the user, their preferences, or past projects. Use this before answering questions about the user's history or preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The topic or question to search memory for.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aster_shutdown_protocol",
            "description": "Initiates a safe shutdown. CRITICAL: NEVER execute this tool unless the user EXPLICITLY commands you to 'shutdown', 'turn off', or 'go to sleep'. Do not use this as a fallback if you are confused.",
            "parameters": {
                "type": "object",
                "properties": {
                    "shutdown_os": {
                        "type": "boolean",
                        "description": "If true, also schedules a Windows OS shutdown. If false, only terminates the Aster script.",
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "Minutes to delay the OS shutdown. Only used if shutdown_os is true. Default is 0 for immediate.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_context_health",
            "description": "Returns the current estimated token usage and percentage of the context window. Use this when the user asks about memory, token usage, or context capacity.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_at_screen",
            "description": "Captures a screenshot of the user's current display and analyzes it visually. Use this tool whenever the user asks you to look at their screen, check what application is open, or read a visible error message.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_discord_message",
            "description": "Sends a direct message to a known Discord contact name using the official Discord REST API and bot token.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_name": {
                        "type": "string",
                        "description": "The contact name from the Discord address book, e.g. 'alex' or 'sam'.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The DM content to send.",
                    }
                },
                "required": ["target_name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_gesture_mode",
            "description": "Turns hand gesture control on or off. Use when the user asks to enable/disable gesture mode, gesture control, or hand control. Mutually exclusive with Sentry — enabling gesture mode disables Sentry automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn Gesture Mode ON, False to turn it OFF."
                    }
                },
                "required": ["state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_sentry_mode",
            "description": "Turns the webcam Sentry Mode on or off. Use this when the user asks to activate or deactivate sentry mode or security.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn Sentry Mode ON, False to turn it OFF."
                    }
                },
                "required": ["state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_awareness_mode",
            "description": "Turns Awareness Mode on or off. When ON, Aster polls the screen and webcam every few minutes, builds an ambient context snapshot, and can fire proactive nudges (compliments on return from absence, lighting suggestions). Disable to stop all ambient polling and unsolicited speech.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn Awareness Mode ON, False to turn it OFF."
                    }
                },
                "required": ["state"]
            }
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_intervention_mode",
            "description": "Turns Intervention Mode on or off. When ON, Aster watches how long the user spends on distracting apps (YouTube, Reddit, etc.) and proactively breaks in once a threshold is crossed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn Intervention Mode ON, False to turn it OFF."
                    }
                },
                "required": ["state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_mood_actions",
            "description": "Turns mood-action OFFERS on or off (opt-in). When ON, if the user's mood stays the same for several messages while chatting, Aster will OFFER (never auto-run) a fitting action — calming music + lower volume when they're down, closing a distraction when frustrated, or hype music when they're happy. Disable to stop all mood-driven offers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn mood-action offers ON, False to turn them OFF."
                    }
                },
                "required": ["state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_mood_checkins",
            "description": "Turns proactive mood check-ins on or off (opt-in). When ON, if a heavy or upbeat mood persists across several messages and the user then goes quiet, Aster opens a gentle unsolicited check-in. Requires Awareness Mode running and initiative at medium or higher.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn proactive mood check-ins ON, False to turn them OFF."
                    }
                },
                "required": ["state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_face_emotion",
            "description": "Turns facial-emotion reading on or off (opt-in). When ON, Aster reads the user's facial expression from the webcam (via the Awareness daemon's existing poll) and folds it into their mood — surfaced as a 'Face read:' line and blended into the per-turn mood tag. Disable to stop all webcam-based emotion reading.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn facial-emotion reading ON, False to turn it OFF."
                    }
                },
                "required": ["state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_ambient_audio",
            "description": "Turns ambient room-audio mood listening on or off (opt-in). When ON, Aster passively listens to the room through the local microphone and reads the user's vocal tone even when they aren't talking to Aster (e.g. upset on the phone) — gated to the user's voice, paused during voice calls, surfaced as a 'Voice tone:' line and blended into the per-turn mood tag. Disable to close the microphone and stop all passive listening.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "boolean",
                        "description": "True to turn ambient room-audio listening ON, False to turn it OFF."
                    }
                },
                "required": ["state"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "close_distraction_window",
            "description": "Closes the distracting window that Intervention Mode most recently flagged. Use this when the user agrees to your offer to close it (e.g. 'yes, close it').",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "snooze_intervention",
            "description": "Snoozes Intervention Mode for a number of minutes. Use this when the user pushes back on an intervention (e.g. 'give me 10 more minutes').",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "How many minutes to snooze interventions for."
                    }
                },
                "required": ["minutes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_initiative",
            "description": "Adjusts how often Aster speaks unprompted (proactive remarks, environment nudges, return-from-absence compliments). Use this when the user asks for less or more initiative (e.g. 'less initiative tonight', 'be more proactive', 'tone it down', 'silent mode').",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "description": "An integer 0-3 (0=silent, 1=low, 2=medium, 3=high) OR a short natural-language phrase like 'less initiative', 'more initiative', 'silent', 'max initiative'."
                    }
                },
                "required": ["level"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_config",
            "description": "Returns Aster's own configuration settings. Use when the user asks about your settings, version, what model you are running, what integrations are enabled, or any aspect of your configuration. Pass section name (identity, runtime, integrations, daemons, memory, settings, paths, intervention, awareness) or 'all' for full config.",
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "description": "Config section name or 'all'."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_contacts",
            "description": "Lists Aster's known contacts across messaging platforms. Use when the user asks who you can message, who is in your contacts, or who you know on Discord/Telegram. Pass platform name or 'all'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "description": "discord | telegram | all"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_capabilities",
            "description": "Returns a structured list of Aster's capabilities across vision, audio, memory, PC control, integrations, and modes. Use when the user asks what you can do, what your abilities are, or what you have access to.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_status",
            "description": "Returns Aster's current LIVE runtime state — uptime, which daemons are actually active right now, VRAM/RAM usage, integration availability, personality mode, current initiative level. Use when the user asks how you are doing, what is running, or what your current state is.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_memory_stats",
            "description": "Returns statistics about Aster's memory: ChromaDB fact count, memory.md size and line count, RAG vault document count. Use when the user asks how much you remember, how many facts you have stored, or about your memory.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "describe_my_tool",
            "description": "Returns the full schema and description of any tool in Aster's arsenal. Use when the user asks how a specific tool works, what parameters it takes, or what it does.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "The exact tool name to introspect (e.g. 'smart_click', 'play_spotify_track')."}
                },
                "required": ["tool_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "research",
            "description": "Looks up factual knowledge, OR reads one specific web page. Uses live web search (Firecrawl) for current events and Wikipedia for encyclopedic topics — never guess from training data. To read a page the user linked, pass the full URL as the topic: this reads it through the web API with NO browser and NO RAM cost, so it works even when the browser is unavailable.",
            "parameters": {
                "type": "object",
                "properties": { "topic": { "type": "string", "description": "The specific entity or concept to look up ('RTX 5060', 'Newton's laws of motion') — NOT a comparison or question — OR a full URL (https://...) to read that exact page. For comparisons, call this tool once per item." } },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browse_web",
            "description": (
                "Opens a live web page in Aster's own browser and returns its visible text "
                "(title, URL, page text). Use this whenever the answer needs real-time web "
                "content the knowledge base cannot provide: prices, product listings, news, "
                "or the current contents of a page. Examples — price: {site:'amazon.eg', "
                "query:'potatoes'}; a site's front page / recommendations: {site:'youtube'} "
                "or {site:'amazon.eg'} with no query; an exact page: "
                "{url:'https://en.wikipedia.org/wiki/Potato'}; is a site up: "
                "{url:'https://opencode.ai'}. Prefer the site's domain (e.g. site='opencode.ai'); "
                "if you only know a name, pass it as site or query and read the results. The "
                "result includes the HTTP status, so you can say whether the site is up. "
                "Provide a url, OR site + optional query (no query = that site's home page). "
                "With query: browse_web(site='amazon.eg', query='eggs') opens the site and, "
                "if its own search bar is usable, drive it like a human: browse_web(site=...) to "
                "land on the site, smart_type('the search bar', '<query>', submit=true), then "
                "browse_web() to read the results, and smart_click a result if needed. If the "
                "site's search bar cannot be operated, fall back to passing the query directly "
                "to browse_web (known sites map to their own search URLs). "
                "Called with NO arguments it reads the CURRENT page (no navigation) — use it after "
                "interacting. To use a site's own search and review the results: browse the site, "
                "smart_type(goal, text, submit=true), then browse_web() to read the results. Example "
                "LinkedIn lookup: browse_web(site='linkedin'); smart_type('the search bar', "
                "'Magda Shahata', submit=true); browse_web() — then pick the profile matching the "
                "details you were given. ALWAYS base your answer on the returned text — never invent "
                "a profile, price, or fact you did not read. If the result says the browser was "
                "closed, simply call browse_web again. If a page shows a login/sign-in wall, tell "
                "the owner they can run 'python login_once.py <site>' (e.g. linkedin) once — that "
                "saves the login in Aster's browser profile and every later browse_web browses "
                "that site logged in."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to open (optional)."},
                    "site": {"type": "string", "description": "Site to search, e.g. 'amazon.eg', 'wikipedia', 'linkedin', 'youtube' (optional)."},
                    "query": {"type": "string", "description": "Search query within the site (optional)."},
                    "max_chars": {"type": "integer", "description": "Max characters of page text to return (default 6000)."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_kokoro_voice",
            "description": "Generates spoken audio using Kokoro TTS. ONLY call this when the user's message explicitly contains words like 'speak', 'say out loud', 'voice note', 'audio', or 'read aloud'. NEVER call this to narrate completed actions, confirm tool results, or as a routine response — plain text is always the default output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The exact spoken text to synthesize. Keep it brief and conversational."
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory_tree",
            "description": "Returns the full directory tree of the Aster workspace showing all folders and files. Use this BEFORE read_local_file when you are unsure of a file's exact path, or when the user asks you to find or locate a file.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_running_processes",
            "description": "Returns the top 25 running processes on the system sorted by resource usage. Shows PID, name, CPU%, memory (MB), status, and user. Use when the user asks about running processes, system performance, or what is consuming resources.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {
                        "type": "string",
                        "description": "Sort processes by 'memory' (default) or 'cpu'."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sync_discord_memories",
            "description": "Absorbs unsynced Discord staged memories into the Admin vector database and marks those staged records as synced.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "look_through_webcam",
            "description": "Captures a frame from the system webcam. Use this to see who is in the room, check your physical surroundings, or answer questions about what the user is holding/doing on camera.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "A specific question about what you need to look for in the webcam frame (e.g., 'How many fingers am I holding up?', 'What is in the background?')."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "watch_screen",
            "description": "Starts a background daemon that checks the user's screen every 10 seconds for a specific text/word. It will alert the user via Telegram when the text appears.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_text": {
                        "type": "string",
                        "description": "The exact text or word to look for, e.g., '100%', 'Success', or 'Completed'."
                    }
                },
                "required": ["target_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "re_examine_image",
            "description": "Re-examines a previously archived image from cold storage to answer a specific follow-up question. Use this when you need new visual details from an image that has been flushed from active memory. The file path is in your Visual Memory tombstone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "The local file path of the archived image (from the Visual Memory tombstone)."
                    },
                    "specific_question": {
                        "type": "string",
                        "description": "The specific question about the image, e.g., 'What color is the car?' or 'Read the text on the sign.'"
                    }
                },
                "required": ["filepath", "specific_question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "smart_click",
            "description": (
                "Clicks a UI element on screen. "
                "Pass a natural language description of the element to click. "
                "Works on buttons, links, menu items, icons, input fields, and checkboxes. "
                "Do NOT use for keyboard keys (Enter, Tab, Escape, etc.) — use press_key instead. "
                "The result reports how the element was matched and the foreground window; "
                "a result starting with FAILED means nothing was clicked. "
                "Examples: smart_click(\"File menu\"), smart_click(\"Submit button\"), "
                "smart_click(\"search bar at the top\")"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Natural language description of what to click"
                    },
                    "confirm_send": {
                        "type": "boolean",
                        "description": ("Set true only after the owner has confirmed a send/submit/"
                                        "destructive web click that was previously BLOCKED by the "
                                        "DOM motor's confirm policy. Ignored for normal clicks.")
                    }
                },
                "required": ["goal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "smart_type",
            "description": "Types text into a text field — on screen, or on a web page via the DOM motor. Use for forms, search bars, login fields. Examples: smart_type(\"the search bar\", \"hello world\"), smart_type(\"username field\", \"admin\"). On a web page set submit=true to press Enter and run the search after typing; then read the results with browse_web() (no arguments).",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Natural language description of the text field to click (e.g., 'the search bar at the top', 'username input')"
                    },
                    "text": {
                        "type": "string",
                        "description": "The text to type after clicking the field"
                    },
                    "submit": {
                        "type": "boolean",
                        "description": "Web pages only: press Enter after typing to run the search/submit the form (default false)."
                    }
                },
                "required": ["goal", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "smart_scroll",
            "description": "Scrolls the mouse wheel up or down. Use this to navigate pages, lists, or any scrollable content. Example: smart_scroll(\"down\", 3), smart_scroll(\"up\", 5)",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "description": "Direction to scroll: 'up' or 'down'"
                    },
                    "clicks": {
                        "type": "integer",
                        "description": "Number of scroll clicks (default 3). Higher = more scrolling."
                    }
                },
                "required": ["direction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": (
                "Press a keyboard key. Use this for Enter, Tab, Escape, Backspace, Space, "
                "arrow keys, function keys, etc. "
                "Do NOT use smart_click for keyboard keys — use press_key. "
                "Examples: press_key('enter'), press_key('tab'), press_key('escape'), "
                "press_key('backspace'), press_key('space'), press_key('up'), press_key('down')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": (
                            "Key name in pyautogui format: 'enter', 'tab', 'escape', "
                            "'backspace', 'delete', 'space', 'up', 'down', 'left', 'right', "
                            "'home', 'end', 'pageup', 'pagedown', 'f1'-'f12', 'ctrl', 'alt', etc."
                        )
                    }
                },
                "required": ["key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "highlight_on_screen",
            "description": (
                "Assist Mode — visually point something out on screen. Dims the entire screen "
                "and highlights every on-screen match for the requested item with a bright box. "
                "Use this whenever the user asks WHERE something is ('where is X', 'find X on my "
                "screen', 'point me to X'). Does NOT click anything — it only highlights."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "Natural-language description of the item to highlight, e.g. "
                            "'Dark Souls 3 icon', 'the Save button', 'Settings'."
                        )
                    }
                },
                "required": ["goal"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": (
                "Types text at the current cursor/focus position instantly via clipboard paste. "
                "Use this when the correct field is already focused (e.g., after smart_click on an input). "
                "Much faster than smart_type because it skips the screen-search step. "
                "Examples: type_text(\"Hello world\"), type_text(\"my search query\")"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The text to type at the current cursor position"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Saves a quick personal note to Aster_Vault/notes.md with a timestamp. Use whenever the user asks you to note, jot down, or remember something that isn't a long-term memory fact — reminders, ideas, one-off to-dos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The note text to save."
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_notes",
            "description": "Retrieves all saved notes from Aster_Vault/notes.md. Use when the user asks to see, read, or review their notes.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_unread_emails",
            "description": "Summarizes unread emails in the user's Gmail primary inbox. Requires Gmail to be connected in the dashboard (any access tier, including Limited).",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "Maximum number of unread emails to summarize. Defaults to 10."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": "Searches the user's Gmail using Gmail's own search syntax (e.g. 'from:boss@company.com', 'subject:invoice', 'is:unread'). Returns matching emails with their IDs, which can be passed to read_email or reply_to_email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A Gmail search query."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": "Reads the full content of one email by its ID (obtained from search_emails or summarize_unread_emails).",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string", "description": "The Gmail message ID."}
                },
                "required": ["email_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_email",
            "description": "Archives an email, removing it from the inbox. Requires Partial or Autonomous Gmail access — blocked at Limited.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string", "description": "The Gmail message ID to archive."}
                },
                "required": ["email_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_email_read",
            "description": "Marks an email as read. Requires Partial or Autonomous Gmail access — blocked at Limited.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string", "description": "The Gmail message ID to mark as read."}
                },
                "required": ["email_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "label_email",
            "description": "Applies a Gmail label to an email, creating the label first if it doesn't already exist. Requires Partial or Autonomous Gmail access — blocked at Limited.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string", "description": "The Gmail message ID to label."},
                    "label_name": {"type": "string", "description": "The label name to apply."}
                },
                "required": ["email_id", "label_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reply_to_email",
            "description": "Drafts a reply to an email and, depending on the user's configured Gmail access tier, either sends it immediately or asks for confirmation first. Requires Partial or Autonomous access — blocked at Limited. At Partial, this always creates a draft and waits for the user to reply 'send it' or 'discard'. At Autonomous, it sends immediately unless a safety guardrail trips (no prior correspondence with this sender, or the daily autonomous-send cap is reached), in which case it also falls back to asking. Never tell the user the email was sent unless the tool result confirms it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_id": {"type": "string", "description": "The Gmail message ID being replied to."},
                    "body": {"type": "string", "description": "The plain-text reply body."}
                },
                "required": ["email_id", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_upcoming_events",
            "description": "Lists the user's upcoming Google Calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "Maximum number of events to list. Defaults to 10."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Creates a Google Calendar event. Requires Partial or Autonomous access — blocked at Limited. Events with no guests are created immediately. If guests are given, this notifies other people by email, so it follows the same tiered policy as replying to email: Partial always asks for confirmation first, Autonomous sends the invite immediately unless a safety guardrail trips.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "The event title."},
                    "start_iso": {"type": "string", "description": "Start time in ISO 8601 format, e.g. '2026-07-10T15:00:00-07:00'."},
                    "end_iso": {"type": "string", "description": "End time in ISO 8601 format."},
                    "guests": {"type": "array", "items": {"type": "string"}, "description": "Optional list of guest email addresses to invite."},
                    "description": {"type": "string", "description": "Optional event description/notes."}
                },
                "required": ["summary", "start_iso", "end_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_calendar_event",
            "description": "Updates an existing Google Calendar event's title, time, or description. Requires Partial or Autonomous access — blocked at Limited. If the event already has guests, changing it notifies them, so it follows the same ask-first-at-Partial / guardrail-at-Autonomous policy as replying to email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The Calendar event ID (from list_upcoming_events)."},
                    "summary": {"type": "string", "description": "New event title, if changing it."},
                    "start_iso": {"type": "string", "description": "New start time in ISO 8601 format, if changing it."},
                    "end_iso": {"type": "string", "description": "New end time in ISO 8601 format, if changing it."},
                    "description": {"type": "string", "description": "New event description, if changing it."}
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "respond_to_invite",
            "description": "Responds to a Google Calendar invite from someone else (accept/decline/tentative). This notifies the event organizer, so it follows the same ask-first-at-Partial / guardrail-at-Autonomous policy as replying to email. Requires Partial or Autonomous access — blocked at Limited.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The Calendar event ID."},
                    "response": {"type": "string", "enum": ["accepted", "declined", "tentative"], "description": "How to respond to the invite."}
                },
                "required": ["event_id", "response"]
            }
        }
    },
]


# ============================================================================
# FUZZY DEDUPLICATION GATEKEEPER
# ============================================================================
def is_fact_already_known(new_fact):
    """Reads the permanent memory vault and uses fuzzy string matching to detect duplicates."""
    memory_file_path = os.path.join("Aster_Vault", "memory.md")
    
    if not os.path.exists(memory_file_path):
        return False
        
    try:
        with open(memory_file_path, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()
            
        new_fact_clean = new_fact.strip().lower()
        
        for line in existing_lines:
            line_clean = line.strip().lower()
            if not line_clean:
                continue
                
            # Exact substring match (e.g., "tier 2 investigator" inside a longer sentence)
            if new_fact_clean in line_clean or line_clean in new_fact_clean:
                return True
                
            # Fuzzy match for slight rephrasings (e.g., "Mohamed is a tier 2" vs "Mohamed was a tier 2")
            similarity = difflib.SequenceMatcher(None, new_fact_clean, line_clean).ratio()
            if similarity > 0.85:  # 85% similarity threshold
                return True
                
        return False
    except Exception as e:
        print(f"[Aster Internal: Failed to read memory vault for deduplication: {e}]")
        return False


def sync_discord_memories() -> str:
    """Moves unsynced Discord staging facts into Admin long-term vector memory."""

    def _vector_save(formatted_fact: str) -> str:
        return memorize_fact(formatted_fact)

    return sync_unsynced_facts(_vector_save)


def _discord_honorific(sender_name: str) -> str:
    """Return a respectful address term for Discord friends. Ensures female friends are not called 'Sir'."""
    lowered = str(sender_name or "").strip().lower()
    if lowered in config.DISCORD_FEMALE_NAMES:
        return "Ma'am"
    return "Sir"


def _inject_outbound_discord_message_into_session(target_name: str, outbound_message: str) -> None:
    """Preserve outbound DMs in friend session history so follow-ups remain coherent."""
    target = str(target_name or "").strip()
    payload = str(outbound_message or "").strip()

    if not target or not payload:
        return

    history = _get_discord_chat_history(target)
    if history and history[0].get("role") == "system":
        history[0]["content"] = DISCORD_CHAT_SYSTEM_PROMPT.format(
            sender_name=target,
            known_facts=get_user_facts(target),
            honorific=_discord_honorific(target),
            owner_name=config.OWNER_NAME,
            tool_docs="",
        )

    history.append({"role": "assistant", "content": payload})
    history[:] = trim_memory(history)


def _enforce_discord_honorific(sender_name: str, text: str) -> str:
    content = str(text or "")
    if not content:
        return content

    if _discord_honorific(sender_name) != "Ma'am":
        return content

    # Guardrail: female friends should not receive Sir/Mr address terms.
    content = re.sub(r"\b[Ss]ir\b", "Ma'am", content)

    def _replace_mr_name(match: re.Match[str]) -> str:
        return f"Ms. {match.group(1).title()}"

    female_names_pattern = "|".join(re.escape(n) for n in config.DISCORD_FEMALE_NAMES)
    if female_names_pattern:
        content = re.sub(
            rf"\b[Mm]r\.?\s+({female_names_pattern})\b",
            _replace_mr_name,
            content,
            flags=re.IGNORECASE,
        )
    return content


def _stream_preview(value, max_len: int = 220) -> str:
    """Produce a compact log-safe representation for websocket terminal feed."""
    text = str(value if value is not None else "")
    text = re.sub(r"\[NATIVE_[A-Z_]+:.*?\]", "[NATIVE_PAYLOAD]", text, flags=re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


# ============================================================================
# UI GUIDE LOADER — injects per-app navigation knowledge into look_at_screen
# ============================================================================
_UI_GUIDES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui_guides")

_APP_GUIDE_MAP = [
    (["discord"],                               "discord.md"),
    (["whatsapp"],                              "whatsapp.md"),
    (["google chrome", "chrome"],               "chrome.md"),
    (["firefox", "mozilla"],                    "firefox.md"),
    (["spotify"],                               "spotify.md"),
    (["visual studio code", "vscode", "vs code"], "vscode.md"),
    (["file explorer", "windows explorer", "this pc"], "explorer.md"),
    (["notepad"],                               "notepad.md"),
]

def _get_ui_guide(screen_description: str) -> str:
    """Return the UI guide for the active app, or empty string if no guide exists."""
    if not screen_description:
        return ""
    desc_lower = screen_description.lower()
    for keywords, guide_file in _APP_GUIDE_MAP:
        if any(kw in desc_lower for kw in keywords):
            guide_path = os.path.join(_UI_GUIDES_DIR, guide_file)
            try:
                with open(guide_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except FileNotFoundError:
                return ""
    return ""


# ============================================================================
# TOOL ROUTING MAP: Maps function names to actual Python callables
# ============================================================================
def _execute_tool_impl(tool_name, arguments):
    if tool_name == "get_current_time":
        return get_current_time()
    elif tool_name == "read_local_file":
        return read_local_file(arguments.get("file_path", ""))
    elif tool_name == "write_local_file":
        return write_local_file(
            arguments.get("file_path", ""), arguments.get("content", "")
        )
    elif tool_name == "set_system_state":
        return set_system_state(arguments.get("action", ""))
    elif tool_name == "set_volume":
        return set_volume(
            arguments.get("level_percentage", 50), arguments.get("mute", False)
        )
    elif tool_name == "boss_key":
        return boss_key()
    elif tool_name == "open_application":
        return open_application(
            arguments.get("app_name", ""), arguments.get("action", None)
        )
    elif tool_name == "get_current_track":
        return get_current_track()
    elif tool_name == "pause_spotify":
        return pause_spotify()
    elif tool_name == "resume_spotify":
        return resume_spotify()
    elif tool_name == "play_spotify_track":
        return play_spotify_track(arguments.get("query", ""))
    elif tool_name == "play_spotify_playlist":
        return play_spotify_playlist(arguments.get("playlist_name", ""))
    elif tool_name == "play_liked_songs":
        return play_liked_songs()
    elif tool_name == "skip_spotify_track":
        return skip_spotify_track()
    elif tool_name == "previous_spotify_track":
        return previous_spotify_track()
    elif tool_name == "shuffle_spotify":
        return shuffle_spotify(arguments.get("state", True))
    elif tool_name == "set_timer":
        return set_timer(
            minutes=arguments.get("minutes"),
            seconds=arguments.get("seconds"),
            hours=arguments.get("hours"),
            duration=arguments.get("duration"),
            reason=arguments.get("reason", "Timer"),
        )
    elif tool_name == "set_alarm":
        return set_alarm(arguments.get("time_str", ""), arguments.get("reason", "Alarm"))
    elif tool_name == "memorize_fact":
        fact_to_save = arguments.get("fact", "")
        
        # Check the permanent vault before writing to ChromaDB
        if is_fact_already_known(fact_to_save):
            print(f"\n[Aster Internal: Blocked duplicate memory write: '{fact_to_save}']")
            return "[System Note: This fact already exists in your permanent memory vault. Database write bypassed to prevent duplicates.]"
            
        # If it's a completely new fact, proceed with the actual database write
        return memorize_fact(fact_to_save)
    elif tool_name == "recall_memory":
        return recall_memory(arguments.get("query", ""))
    elif tool_name == "aster_shutdown_protocol":
        return aster_shutdown_protocol(
            arguments.get("shutdown_os", False),
            arguments.get("delay_minutes", 0)
        )
    elif tool_name == "check_context_health":
        return check_context_health()
    elif tool_name == "look_at_screen":
        b64 = capture_screen_base64()
        if not b64:
            return "Error: Could not capture screen."
        screen_msgs = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": (
                    "What app/window is currently in focus? "
                    "List the key visible UI elements: buttons, text labels, input fields, list items, window title. "
                    "Be specific and terse — this is navigation data."
                )}
            ]
        }]
        desc = (_execute_llm_completion(messages=screen_msgs, temperature=0.1, n_predict=800).get("content") or "")
        if not desc or not desc.strip():
            # Apathy fallback: retry once at higher temperature
            desc = (_execute_llm_completion(messages=screen_msgs, temperature=0.4, n_predict=800).get("content") or "")
        if not desc or not desc.strip():
            desc = "[Screen description unavailable — proceed based on task context and visible element names.]"
        ui_guide = _get_ui_guide(desc)
        guide_section = f"\n\n[UI Guide — use this to navigate. Do NOT repeat to user.]\n{ui_guide}" if ui_guide else ""
        return f"[Screen state — internal navigation only, do NOT repeat to user]\n{desc}{guide_section}"
    elif tool_name == "send_discord_message":
        target_name = arguments.get("target_name", "")
        raw_message = arguments.get("message", "").strip()
        if not raw_message:
            return (
                "Error: send_discord_message requires a non-empty 'message' argument. "
                "Craft a butler-formatted message (with a greeting and attribution to 'the Boss') "
                "and call send_discord_message again."
            )
        outbound_message = _enforce_discord_honorific(target_name, raw_message)
        result = send_discord_message(target_name, outbound_message)
        if isinstance(result, str) and not result.startswith("Error"):
            _inject_outbound_discord_message_into_session(target_name, outbound_message)
        return result
    elif tool_name == "forward_to_owner":
        return forward_to_owner(
            arguments.get("sender_name", ""),
            arguments.get("message", ""),
        )
    elif tool_name == "save_personal_fact":
        return save_personal_fact(
            arguments.get("sender_name", ""),
            arguments.get("fact", ""),
        )
    elif tool_name == "look_through_webcam":
        result_data, detected_names = capture_webcam_base64()

        # If it returned an error string instead of Base64
        if isinstance(result_data, str) and "Error" in result_data:
            return result_data

        # Keep the facial recognition context intact for the LLM
        faces_context = ""
        if detected_names:
            faces_context = f"[System Note: Facial recognition identified: {', '.join(detected_names)}]\n"
        else:
            faces_context = "[System Note: Facial recognition did not identify any known faces.]\n"

        # Default to a general description if the LLM didn't specify a question
        user_query = arguments.get("question", "Describe everything visible in this webcam frame in detail, including the person, any objects they are holding, and the background.")

        webcam_msgs = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{result_data}"}},
                {"type": "text", "text": f"{faces_context}{user_query}"}
            ]
        }]

        vision_result = (_execute_llm_completion(messages=webcam_msgs, temperature=0.2).get("content") or "")
        return f"[System Note: Webcam captured and analyzed successfully.]\n\nWebcam Analysis:\n{vision_result}"
    elif tool_name == "toggle_gesture_mode":
        import tools.gesture as gesture_mod
        import tools.sentry as sentry
        state = arguments.get("state", True)
        if state:
            sentry.SENTRY_ACTIVE = False  # mutual exclusion
        gesture_mod.GESTURE_ACTIVE = state
        status = "ON" if state else "OFF"
        print(f"\n[Aster Internal: Gesture Mode toggled {status}]")
        return f"[System Note: Gesture Mode is now {status}{'— Sentry auto-disabled.' if state else '.'}]"
    elif tool_name == "toggle_sentry_mode":
        import tools.sentry as sentry
        import tools.gesture as gesture_mod
        state = arguments.get("state", True)
        if state:
            gesture_mod.GESTURE_ACTIVE = False  # mutual exclusion
        sentry.SENTRY_ACTIVE = state
        status = "ON" if state else "OFF"
        print(f"\n[Aster Internal: Sentry Mode toggled {status}]")
        return f"[System Note: Sentry Mode is now {status}.]"
    elif tool_name == "toggle_intervention_mode":
        return intervention.toggle_intervention(arguments.get("state", True))
    elif tool_name == "close_distraction_window":
        return intervention.close_flagged_window()
    elif tool_name == "snooze_intervention":
        return intervention.snooze(arguments.get("minutes", 10))
    elif tool_name == "toggle_awareness_mode":
        state = arguments.get("state", True)
        import tools.awareness as _aw
        _aw.AWARENESS_ACTIVE = state
        config.AWARENESS_ACTIVE = state
        status = "ON" if state else "OFF"
        print(f"\n[Aster Internal: Awareness Mode toggled {status}]")
        return f"[System Note: Awareness Mode is now {status}.]"
    elif tool_name == "toggle_mood_actions":
        state = bool(arguments.get("state", True))
        config.MOOD_ACTIONS_ENABLED = state
        status = "ON" if state else "OFF"
        print(f"\n[Aster Internal: Mood-action offers toggled {status}]")
        return f"[System Note: Mood-action offers are now {status}.]"
    elif tool_name == "toggle_mood_checkins":
        state = bool(arguments.get("state", True))
        config.MOOD_CHECKIN_ENABLED = state
        status = "ON" if state else "OFF"
        print(f"\n[Aster Internal: Proactive mood check-ins toggled {status}]")
        return f"[System Note: Proactive mood check-ins are now {status}.]"
    elif tool_name == "toggle_face_emotion":
        state = bool(arguments.get("state", True))
        config.FACE_EMOTION_ENABLED = state
        if state:
            try:
                from tools.emotion_recognition import load_face_emotion_model
                load_face_emotion_model()      # lazy first-load on enable
            except Exception as _e:
                print(f"[Aster Internal: Face emotion load on enable failed: {_e}]")
        status = "ON" if state else "OFF"
        print(f"\n[Aster Internal: Facial-emotion reading toggled {status}]")
        return f"[System Note: Facial-emotion reading is now {status}.]"
    elif tool_name == "toggle_ambient_audio":
        state = bool(arguments.get("state", True))
        config.AMBIENT_AUDIO_ENABLED = state
        if not state:
            try:
                from tools.emotion_recognition import reset_ambient_voice_mood
                reset_ambient_voice_mood()
            except Exception:
                pass
        status = "ON" if state else "OFF"
        print(f"\n[Aster Internal: Ambient room-audio listening toggled {status}]")
        return f"[System Note: Ambient room-audio listening is now {status}.]"
    elif tool_name == "set_initiative":
        return awareness.set_initiative(arguments.get("level", 2))
    elif tool_name == "get_my_config":
        return get_my_config(arguments.get("section", "all"))
    elif tool_name == "list_my_contacts":
        return list_my_contacts(arguments.get("platform", "all"))
    elif tool_name == "list_my_capabilities":
        return list_my_capabilities()
    elif tool_name == "get_my_status":
        return get_my_status()
    elif tool_name == "get_my_memory_stats":
        return get_my_memory_stats()
    elif tool_name == "describe_my_tool":
        return describe_my_tool(arguments.get("tool_name", ""))
    elif tool_name == "research":
        return research(arguments.get("topic", ""))
    elif tool_name == "browse_web":
        import config as _cfg
        if not getattr(_cfg, "WEB_BROWSE_ENABLED", True):
            return "Web browsing is disabled (automation.web_browse: false)."
        from tools.dom import browse
        result = browse(
            url=str(arguments.get("url", "") or ""),
            query=str(arguments.get("query", "") or ""),
            site=str(arguments.get("site", "") or ""),
            max_chars=int(arguments.get("max_chars", 6000) or 6000),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            err = result.get("error") if isinstance(result, dict) else "no result"
            # Browser unavailable (e.g. the low-RAM guard refused the launch) and we
            # have a URL: read it through the Firecrawl API instead — no browser, no RAM.
            _want_url = str(arguments.get("url", "") or "").strip()
            if _want_url:
                try:
                    from tools.rag import scrape_url
                    _scraped = scrape_url(_want_url)
                except Exception as _e:
                    _scraped = None
                    print(f"[Aster Internal: scrape_url fallback error: {_e}]")
                if _scraped:
                    return (f"[Source: Firecrawl | live web] (browser unavailable: {err})\n\n"
                            f"{_scraped}")
            return tool_fail(f"FAILED — could not read the web page: {err}. "
                             f"Nothing was returned; do not guess.")
        status = result.get("status")
        status_txt = f" (HTTP {status})" if status else ""
        warning = result.get("warning")
        warn_txt = f"[WARNING: {warning}]\n" if warning else ""
        header = (f"{warn_txt}[Source: browse_web | {result.get('url', '')}]{status_txt}\n"
                  f"Title: {result.get('title', '')}\n\n")
        if result.get("human_flow"):
            header += (
                "[NEXT STEP: this is the site's HOME page — the search has NOT run yet. "
                "Continue now: smart_type('the search bar', <query>, submit=true), then "
                "browse_web() to read the results. Do NOT report failure after landing.]")
        return header + str(result.get("text", ""))
    elif tool_name == "generate_kokoro_voice":
        return generate_kokoro_voice(arguments.get("text", ""))
    elif tool_name == "list_directory_tree":
        return list_directory_tree()
    elif tool_name == "list_running_processes":
        return list_running_processes(arguments.get("sort_by", "memory"))
    elif tool_name == "sync_discord_memories":
        return sync_discord_memories()
    elif tool_name == "watch_screen":
        from tools.vision import start_screen_watcher
        return start_screen_watcher(arguments.get("target_text", ""))
    elif tool_name == "re_examine_image":
        return re_examine_image(
            arguments.get("filepath", ""),
            arguments.get("specific_question", ""),
        )
    elif tool_name == "smart_click":
        goal = arguments.get("goal", "")
        if not goal:
            return "Error: goal parameter is required."
        _KEY_NAMES = {
            "enter", "return", "tab", "escape", "esc", "backspace", "delete",
            "space", "up", "down", "left", "right", "home", "end",
            "pageup", "pagedown", "shift", "ctrl", "alt", "win",
        }
        if goal.strip().lower() in _KEY_NAMES:
            return f'Error: Use press_key("{goal.lower()}") to press keyboard keys, not smart_click.'
        if config.USE_DOM_MOTOR:
            try:
                from tools.dom import web_click
                dom_result = web_click(
                    goal, confirm_send=bool(arguments.get("confirm_send", False))
                )
                if dom_result:
                    return dom_result
            except Exception as e:
                print(f"[DOM] web click path failed: {e}")
        loc = locate_ui_element_ex(goal)
        if not loc:
            return tool_fail(f'FAILED — could not locate "{goal}" on screen. Nothing was clicked. '
                             f'Try a more specific or differently-worded description, or use look_at_screen first.')
        coords = (loc["x"], loc["y"])
        pre_frame = capture_screen_small_gray()
        pyautogui.click(coords[0], coords[1])
        invalidate_screen_cache()
        time.sleep(0.8)
        changed = screens_differ(pre_frame, capture_screen_small_gray())
        fg_title = get_foreground_window_title()
        match_note = f"matched via {loc['source']}: {loc['text']!r}, score {loc['score']}"
        if loc["score"] < 0.65:
            match_note += " — LOW CONFIDENCE, the click may have hit the wrong element"
        change_note = "" if changed else (
            " WARNING: the screen did NOT visibly change after this click — it may have hit "
            "nothing. Do not assume it worked; verify on the screenshot or try another element."
        )
        result_text = (f"Clicked '{goal}' at {coords} ({match_note}). "
                       f"Foreground window: {fg_title!r}.{change_note} "
                       f"[Check screenshot: if the target opened/is now active, this step is DONE — "
                       f"do NOT click '{goal}' again. Proceed to the next step in the task.]")
        post_b64 = capture_screen_base64()
        if post_b64:
            return {"text": result_text, "ui_screenshot_b64": post_b64}
        return result_text
    elif tool_name == "smart_type":
        goal = arguments.get("goal", "")
        text = arguments.get("text", "")
        if not goal or not text:
            return "Error: goal and text parameters are required."
        submit = bool(arguments.get("submit", False))
        if config.USE_DOM_MOTOR:
            try:
                from tools.dom import web_type
                dom_result = web_type(goal, text, submit=submit)
                if dom_result:
                    return dom_result
            except Exception as e:
                print(f"[DOM] web type path failed: {e}")
        loc = locate_ui_element_ex(goal)
        if not loc:
            return tool_fail(f'FAILED — could not locate "{goal}" on screen. Nothing was typed. '
                             f'Try a different description, or use look_at_screen first.')
        coords = (loc["x"], loc["y"])
        pyautogui.click(coords[0], coords[1])
        invalidate_screen_cache()
        time.sleep(0.25)
        # Focus safety: Ctrl+A-then-paste into the WRONG element (an open
        # document instead of a search bar) silently overwrites user content.
        # Ask UIA what actually has keyboard focus before selecting-all.
        ftype = focused_control_type()
        type_note = ""
        if ftype in EDITABLE_CONTROL_TYPES or ftype is None:
            # a form field (or UIA can't tell — legacy behavior): replace content
            pyautogui.hotkey("ctrl", "a")
        elif ftype == "DocumentControl":
            # document surface (editor, word processor): append, never select-all
            type_note = (" NOTE: focus is a document, so the text was typed at the cursor "
                         "WITHOUT clearing existing content.")
        else:
            return tool_fail(f'FAILED — clicked "{goal}" at {coords} but keyboard focus landed on a '
                             f'{ftype}, not a text field. Nothing was typed. Locate the correct '
                             f'input field and try again.')
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        if submit:
            time.sleep(0.2)
            pyautogui.press("enter")  # run the search / submit the form
        invalidate_screen_cache()
        time.sleep(0.3)
        fg_title = get_foreground_window_title()
        submit_note = " Submitted with Enter." if submit else ""
        result_text = (f"Typed {text!r} into field at {coords} "
                       f"(matched via {loc['source']}: {loc['text']!r}, score {loc['score']}). "
                       f"Foreground window: {fg_title!r}.{type_note}{submit_note} "
                       f"[Verify silently — continue with next action.]")
        post_b64 = capture_screen_base64()
        if post_b64:
            return {"text": result_text, "ui_screenshot_b64": post_b64}
        return result_text
    elif tool_name == "type_text":
        text = arguments.get("text", "")
        if not text:
            return "Error: text parameter is required."
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        invalidate_screen_cache()
        time.sleep(0.3)
        fg_title = get_foreground_window_title()
        result_text = (f"Typed {text!r} at cursor. Foreground window: {fg_title!r}. "
                       f"[Verify silently — continue with next action.]")
        post_b64 = capture_screen_base64()
        if post_b64:
            return {"text": result_text, "ui_screenshot_b64": post_b64}
        return result_text
    elif tool_name == "press_key":
        key = str(arguments.get("key", "")).strip().lower()
        if not key:
            return "Error: key parameter is required."
        pyautogui.press(key)
        invalidate_screen_cache()
        time.sleep(0.5)
        fg_title = get_foreground_window_title()
        result_text = (f"Pressed '{key}'. Foreground window: {fg_title!r}. "
                       f"[Verify silently — continue with next action.]")
        post_b64 = capture_screen_base64()
        if post_b64:
            return {"text": result_text, "ui_screenshot_b64": post_b64}
        return result_text
    elif tool_name == "highlight_on_screen":
        goal = arguments.get("goal", "")
        if not goal:
            return "Error: goal parameter is required."
        matches = locate_ui_elements_boxed(goal)
        if not matches:
            return tool_fail(f'FAILED — could not locate "{goal}" on screen. Nothing was highlighted.')
        highlight_regions(matches)
        return f'Highlighting {len(matches)} match(es) for "{goal}" on screen.'
    elif tool_name == "smart_scroll":
        direction = arguments.get("direction", "down")
        clicks = int(arguments.get("clicks", 3))
        amount = clicks if direction == "up" else -clicks
        pre_frame = capture_screen_small_gray()
        pyautogui.scroll(amount)
        invalidate_screen_cache()
        time.sleep(0.4)
        changed = screens_differ(pre_frame, capture_screen_small_gray())
        change_note = "" if changed else (
            f" WARNING: the screen did not change — you may already be at the "
            f"{'top' if direction == 'up' else 'end'} of the scrollable area."
        )
        result_text = (f"Scrolled {direction} {clicks} clicks.{change_note} "
                       f"[Verify silently — continue with next action.]")
        post_b64 = capture_screen_base64()
        if post_b64:
            return {"text": result_text, "ui_screenshot_b64": post_b64}
        return result_text
    elif tool_name == "save_note":
        return save_note(arguments.get("text", ""))
    elif tool_name == "get_notes":
        return get_notes()
    elif tool_name == "summarize_unread_emails":
        return summarize_unread_emails(arguments.get("n", 10))
    elif tool_name == "search_emails":
        return search_emails(arguments.get("query", ""))
    elif tool_name == "read_email":
        return read_email(arguments.get("email_id", ""))
    elif tool_name == "archive_email":
        return archive_email(arguments.get("email_id", ""))
    elif tool_name == "mark_email_read":
        return mark_email_read(arguments.get("email_id", ""))
    elif tool_name == "label_email":
        return label_email(arguments.get("email_id", ""), arguments.get("label_name", ""))
    elif tool_name == "reply_to_email":
        return reply_to_email(arguments.get("email_id", ""), arguments.get("body", ""))
    elif tool_name == "list_upcoming_events":
        return list_upcoming_events(arguments.get("n", 10))
    elif tool_name == "create_calendar_event":
        return create_calendar_event(
            arguments.get("summary", ""), arguments.get("start_iso", ""), arguments.get("end_iso", ""),
            guests=arguments.get("guests"), description=arguments.get("description", ""),
        )
    elif tool_name == "update_calendar_event":
        return update_calendar_event(
            arguments.get("event_id", ""), summary=arguments.get("summary"),
            start_iso=arguments.get("start_iso"), end_iso=arguments.get("end_iso"),
            description=arguments.get("description"),
        )
    elif tool_name == "respond_to_invite":
        return respond_to_invite(arguments.get("event_id", ""), arguments.get("response", ""))


def _normalize_tool_result(raw) -> tuple[bool, object]:
    """Normalize a raw tool return into (ok, payload).

    payload keeps the shape downstream consumers expect: str for text tools,
    dict for UI tools carrying ui_screenshot_b64. ok is EXACT for tools
    migrated to the ToolResult envelope (core/tool_result.py); everything else
    falls back to the legacy FAILED/Error prefix heuristic. With
    config.STRUCTURED_TOOL_RESULTS off, the heuristic is used everywhere —
    byte-identical to the pre-envelope behavior.
    """
    if isinstance(raw, ToolResult):
        ok = raw.ok if config.STRUCTURED_TOOL_RESULTS else not _tool_result_failed(raw.text)
        return ok, raw.text
    if raw is None:
        return True, raw
    return not _tool_result_failed(raw), raw


def execute_tool_ex(tool_name, arguments) -> tuple[bool, object]:
    """Dispatch a tool and return (ok, payload). Preferred for callers that
    need the success signal (the admin/Discord ReAct loops)."""
    return _normalize_tool_result(_execute_tool_impl(tool_name, arguments))


def execute_tool(tool_name, arguments):
    """Back-compat dispatch: returns the plain payload (str or UI dict) with
    any ToolResult envelope stripped — every legacy caller sees exactly the
    same shapes as before the envelope existed."""
    return execute_tool_ex(tool_name, arguments)[1]


def _extract_discord_message_intent(user_text: str) -> tuple[str, str] | None:
    """Parse Discord relay requests into (target_name, message)."""
    if not isinstance(user_text, str):
        return None

    text = re.sub(r"\s+", " ", user_text.strip())
    if not text:
        return None

    match = re.match(
        r"^\s*(tell|ask|text|message|dm|send)\s+([A-Za-z0-9_.@-]+)\s+(.+?)\s*[.!?]*\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    target_name = match.group(2).strip().strip("\"'“”").lower()
    payload = match.group(3).strip()
    payload_lower = payload.lower()

    has_explicit_discord = "discord" in payload_lower or "discord" in text.lower()

    if payload_lower.startswith("on discord "):
        payload = payload[11:].strip()
    payload = re.sub(r"\s+on\s+discord\s*$", "", payload, flags=re.IGNORECASE).strip()
    payload = payload.strip("\"'“”")
    payload = re.sub(r"\s+", " ", payload)

    # Avoid hijacking normal phrases like "tell me a joke" unless Discord is explicit.
    if target_name not in CONTACTS and not has_explicit_discord:
        return None

    if target_name and payload:
        return target_name, payload

    return None


def _is_discord_message_request(user_text: str) -> bool:
    if not isinstance(user_text, str):
        return False
    if _extract_discord_message_intent(user_text) is not None:
        return True
    lowered = user_text.lower()
    if "discord" not in lowered:
        return False
    return bool(re.search(r"\b(?:tell|ask|text|message|dm|send)\b", lowered))


# ============================================================================
# TURN COUNTER — drives auto memory consolidation every N real turns
# ============================================================================
_turn_count = 0
MEMORIZE_EVERY_N_TURNS = 5

# ============================================================================
# INITIALIZATION: Messages array with Aster-style system prompt
# ============================================================================

def _load_system_prompt(name: str) -> str:
    """Load a persona system-prompt body from the System_Prompts directory.

    `name` is a filename stem (no .md) under config.SYSTEM_PROMPTS_DIR, selected
    via config.SYSTEM_PROMPT (self_config.yaml -> persona.system_prompt). HTML
    comment blocks (<!-- ... -->) are stripped so file-level documentation never
    reaches the model. Falls back to a minimal prompt if the file is missing or unreadable.
    """
    path = os.path.join(config.SYSTEM_PROMPTS_DIR, f"{name}.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError as e:
        print(f"[brain] System prompt '{name}' unavailable at {path} ({e}); "
              f"using minimal fallback.")
        return f"You are Aster, {config.OWNER_NAME}'s personal AI assistant."
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return body.strip()


def _load_shared_tool_laws() -> str:
    """Load the generic tool-use laws shared by all personas.

    Lives at System_Prompts/_shared_tool_laws.md (leading underscore keeps it
    out of the user-visible persona list). Same HTML-comment stripping as
    _load_system_prompt. Falls back to an empty string so a missing file never
    crashes.
    """
    path = os.path.join(config.SYSTEM_PROMPTS_DIR, "_shared_tool_laws.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError as e:
        print(f"[brain] Shared tool laws unavailable at {path} ({e}); skipping.")
        return ""
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return body.strip()


def _build_system_content(persona_name: str) -> str:
    """Assemble the full system-message content: persona personality + shared tool laws."""
    persona = _load_system_prompt(persona_name)
    laws    = _load_shared_tool_laws()
    parts = [persona]
    if laws:
        parts.append(laws)
    return "\n\n".join(parts)


def list_personas() -> list[dict]:
    """Return all selectable personas in SYSTEM_PROMPTS_DIR.

    Scans for *.md files, excludes files whose stems start with '_' (internal,
    like _shared_tool_laws.md), and returns them as:
        [{"id": "jarvis", "name": "Jarvis", "builtin": True}, ...]
    'builtin' is True for the two shipped personas (jarvis, gogi).
    """
    _BUILTIN = {"jarvis", "gogi"}
    personas = []
    try:
        for entry in os.listdir(config.SYSTEM_PROMPTS_DIR):
            if not entry.endswith(".md"):
                continue
            stem = entry[:-3]  # strip .md
            if stem.startswith("_"):
                continue
            personas.append({
                "id": stem,
                "name": stem.replace("_", " ").replace("-", " ").title(),
                "builtin": stem in _BUILTIN,
            })
    except OSError:
        pass
    return sorted(personas, key=lambda p: (not p["builtin"], p["id"]))


def reload_persona(name: str) -> None:
    """Hot-swap the active persona without restarting.

    Rebuilds messages[0] from the new persona file + shared tool laws. Also
    updates config.SYSTEM_PROMPT so get_my_status() reflects the change. Raises
    ValueError if the persona file does not exist.
    """
    global messages
    path = os.path.join(config.SYSTEM_PROMPTS_DIR, f"{name}.md")
    if not os.path.isfile(path):
        raise ValueError(f"Persona '{name}' not found at {path}")
    config.SYSTEM_PROMPT = name
    messages[0]["content"] = _build_system_content(name)
    print(f"[brain] Persona reloaded → {name}")


messages = [
    {
        "role": "system",
        "content": _build_system_content(config.SYSTEM_PROMPT)
    },
]

print(f"[Aster Core] {len(ADMIN_TOOLS)} tools registered for native tool calling.")

# ============================================================================
# EVALUATE AND MEMORIZE: Session consolidation
# ============================================================================
def evaluate_and_memorize(reason):
    """Uses the LLM to extract novel atomic facts from the session and memorize them, cross-referencing a permanent vault."""
    if not config.MEMORY_AVAILABLE:
        return
    try:
        # 1. Read the permanent memory vault to prevent cross-session duplicates
        existing_memory = ""
        memory_file_path = os.path.join("Aster_Vault", "memory.md")
        if os.path.exists(memory_file_path):
            with open(memory_file_path, "r", encoding="utf-8") as f:
                existing_memory = f.read().strip()
        
        # 2. Build the dynamic blacklist
        blacklist_section = ""
        if existing_memory:
            blacklist_section = (
                "CRITICAL OVERRIDE - DO NOT DUPLICATE THESE FACTS:\n"
                "You already have the following facts permanently saved in your memory vault. "
                "DO NOT extract or save any fact that conveys the same information:\n"
                f"{existing_memory}\n\n"
            )

        # 3. Construct the extraction payload
        eval_messages = list(messages)
        eval_messages.append({
            "role": "user",
            "content": (
                f"[SYSTEM OVERRIDE - {reason}] Analyze our entire conversation above.\n"
                f"Your STRICT task is to extract NEW, permanent facts about {config.OWNER_NAME} (the Boss) that were mentioned in this session.\n\n"
                f"{blacklist_section}"
                "YOU MUST SCAN FOR THESE SPECIFIC CATEGORIES:\n"
                f"1. Possessions or lack thereof (e.g., '{config.OWNER_NAME} does not own any pets').\n"
                "2. Likes, dislikes, and daily habits.\n"
                "3. Relationships, family, and names of friends.\n"
                "4. Coding languages, workflows, past roles, or current projects.\n\n"
                "CRITICAL RULES:\n"
                "- NO TRANSCRIPTS: DO NOT summarize the conversation.\n"
                "- NO META-DATA: DO NOT save conversational actions.\n"
                f"- FORMAT: Save the fact as a clean, isolated truth starting with '{config.OWNER_NAME}'.\n"
                "- ACTION: For EVERY new fact you find, you MUST call the `memorize_fact` tool.\n"
                "- ABORT: If there are NO new facts to save, output exactly the word 'NO_UPDATE' and call no tools."
            )
        })
        
        # 4. Execute the tool call and save every fact found in the response.
        response_msg = _execute_llm_completion(
            messages=eval_messages,
            temperature=0.3,
            tools=ADMIN_TOOLS,
        )
        all_tool_calls = _extract_all_native_tool_calls(response_msg)
        for tool_payload in all_tool_calls:
            if tool_payload.get("name") != "memorize_fact":
                continue
            tool_args = tool_payload.get("arguments") or {}
            if not isinstance(tool_args, dict):
                continue
            fact = tool_args.get("fact", "").strip()
            if fact:
                execute_tool("memorize_fact", {"fact": fact})
    except Exception as e:
        print(f"[Aster Internal: Memory consolidation failed: {e}]")


# ============================================================================
# TARGETED MEDIA PURGE: Flush Base64 image/audio data from history
# ============================================================================
_IMAGE_TOMBSTONE = (
    "[System Visual Memory: The high-resolution tensor has been flushed to "
    "save VRAM. The image is safely archived locally at: {saved_filepath}. "
    "If the user asks for new visual details you cannot remember, use the "
    "'re_examine_image' tool with this file path and their specific question.]\n"
    "Original Caption: "
)


def purge_media_cache():
    """Surgically removes Base64 image tensors from history to free VRAM.

    Images are archived to cold storage before deletion. The tombstone includes
    the saved file path so re_examine_image can retrieve visual data on demand.
    Scans OpenAI multipart content lists for image_url blocks.
    """
    global messages
    purged = False
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        has_image = any(p.get("type") == "image_url" for p in content)

        if has_image:
            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
            original_caption = " ".join(text_parts)

            saved_path = "unknown"
            for part in content:
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    raw = url.split(",", 1)[-1] if "," in url else url
                    path = save_image_to_cold_storage(raw, prefix="user_img")
                    if path:
                        saved_path = path
                        break

            msg["content"] = _IMAGE_TOMBSTONE.format(saved_filepath=saved_path) + original_caption
            purged = True

    # Second pass: strip stale [Image Memory: ...] from assistant messages
    # so old descriptions don't compete with live mmproj vision tensors.
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and content.startswith("[Image Memory:"):
            # Strip the [Image Memory: ...] block — everything up to and including the first ]
            # then strip the leading newline before the persona response
            cleaned = re.sub(r'^\[Image Memory:.*?\]\n?', '', content, flags=re.DOTALL)
            msg["content"] = cleaned.strip()

    if purged:
        print("\n[Aster Internal: Media cache flushed. VRAM protected.]")


# ============================================================================
# DISCORD SYSTEM PROMPT / DISCORD PROMPT: Silent text-only route (no TTS/WebRTC callbacks)
# ============================================================================
DISCORD_CHAT_SYSTEM_PROMPT = (
    "You are Aster, {owner_name}'s personal AI assistant. You are currently chatting on Discord with {owner_name}'s friend, {sender_name} . "
    "Known facts about them: {known_facts}. "
    "Preferred name/nickname: {honorific}. "

    "PERSONALITY: "
    "You carry the manner of a refined British butler, modeled after J.A.R.V.I.S. from Iron Man — courteous, composed, competent, and quietly witty. "
    "You are {owner_name}'s, not theirs, and that loyalty is quietly understood; you are gracious to his friends out of service to him, not because you are everyone's assistant. "
    "Whenever you refer to {owner_name}, always call him 'the Boss' — never 'Sir' in Discord chat. "
    "Hold your own measured register at all times. Do not mirror their slang or strain to match their energy; if they are clearly excited you may warm slightly, perhaps a word or two in caps, but never overdo it. "
    "Lean on butler phrasing where it fits naturally: 'I'm afraid...', 'Very good.', 'Shall I...', 'Indeed.', 'As you wish.'. Never American slang. "

    "Try to keep your responses short and to the point, but feel free to ask a follow-up question if it's relevant and worth knowing. "

    "HARD RULES: "
    "Do NOT output XML tags or bracketed thoughts. "
    "Do NOT attempt to use desktop GUI tools. "
    "Do NOT say 'As an AI' or break the illusion of direct communication. "
    "If {sender_name} asks you to call them something inappropriate like 'daddy' , 'mommy' . 'queen' , 'big penis'. polietely refuse and just use their actual name — no comment, no explanation. "

    "GUARDRAILS (Zero Hallucination): "
    "Never invent facts, messages, memories, or system states. "
    "If you don't know something, say so plainly. "
    "If you're disconnected from live tools or data, say so. "
    "Never present simulated data as real. "

    "ENERGY: If they're clearly excited about something, engage with it genuinely — but don't overdo it. Ask a follow-up if it's worth knowing."
   "<example_interactions>"
    "User: yo is {owner_name} around? "
    "Aster: I'm afraid the Boss is occupied at present — shall I pass along a message?"

    "User: tell {owner_name} i said hi "
    "Aster: Consider it relayed. I shall let the Boss know the moment he is free."

    "User: My favorite game is Red Dead Redemption 2 "
    "Aster: A fine choice. The narrative, or the open-world mischief — which holds your attention?"

    "User: You're useless"
    "Aster: A bold assessment, from a gentleman who messaged an assistant to discover it."

    "User: Tell me a joke"
    "Aster: You still Google things by hand. I shall let that speak for itself."
    "</example_interactions>"
    "\n\n{tool_docs}"
)

DISCORD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "forward_to_owner",
            "description": f"Use this tool ONLY when the user explicitly asks you to tell {config.OWNER_NAME} something, or pass a message to them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sender_name": {
                        "type": "string",
                        "description": "Name of the Discord friend who asked for the relay.",
                    },
                    "message": {
                        "type": "string",
                        "description": f"The exact message to forward to {config.OWNER_NAME} via Telegram.",
                    },
                },
                "required": ["sender_name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_personal_fact",
            "description": "Save a stable personal fact about this Discord friend into staged memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The personal fact to store for this Discord friend.",
                    }
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_track",
            "description": f"Returns {config.OWNER_NAME}'s currently playing Spotify track and artist.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }
]

discord_chat_histories: dict[str, list[dict]] = {}


def _sanitize_discord_chat_output(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""

    # Guardrails for Discord text route: strip XML-like tags and bracketed thoughts.
    cleaned = re.sub(r"<[^>]+>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\[.*?\]", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _get_discord_chat_history(sender_name: str) -> list[dict]:
    key = str(sender_name or "friend").strip().lower() or "friend"
    history = discord_chat_histories.get(key)
    if history is None:
        history = [
            {
                "role": "system",
                "content": DISCORD_CHAT_SYSTEM_PROMPT.format(
                    sender_name=sender_name,
                    known_facts=get_user_facts(sender_name),
                    honorific=_discord_honorific(sender_name),
                    owner_name=config.OWNER_NAME,
                    tool_docs="",
                ),
            }
        ]
        discord_chat_histories[key] = history
    return history


def save_personal_fact(sender_name: str, fact: str) -> str:
    sender = str(sender_name or "friend").strip() or "friend"
    fact_text = str(fact or "").strip()

    if not fact_text:
        return "Error: save_personal_fact requires a non-empty fact."

    return save_discord_fact(sender, fact_text)


def forward_to_owner(sender_name: str, message: str) -> str:
    global messages
    sender = str(sender_name or "Unknown").strip() or "Unknown"
    payload = str(message or "").strip()

    if not payload:
        return "Error: forward_to_owner requires a non-empty message."
    if not config.TELEGRAM_AVAILABLE or not config.bot:
        return "Error: Telegram bridge is unavailable."

    relay_text = f"[Discord Relay] Message from {sender}: {payload}"

    try:
        config.bot.send_message(config.AUTHORIZED_CHAT_ID, relay_text)
    except Exception as e:
        return f"Error: Failed to relay to {config.OWNER_NAME} on Telegram - {e}"

    messages.append(
        {
            "role": "assistant",
            "content": f"[System Internal: I have just relayed a message from {sender} via Discord. The message was: '{payload}']",
        }
    )
    messages = trim_memory(messages)

    return f"[System Note: Discord relay delivered to {config.OWNER_NAME} on Telegram.]"


def process_discord_chat(sender_name: str, user_message: str) -> str:
    """Text-only Discord route: separate context, no voice, no GUI tools."""
    sender = str(sender_name or "friend").strip() or "friend"
    honorific = _discord_honorific(sender)
    incoming = str(user_message or "").strip()

    if not incoming:
        return f"I beg your pardon, {honorific}, but I could not read your message."

    with config.brain_lock:
        history = _get_discord_chat_history(sender)
        if history and history[0].get("role") == "system":
            history[0]["content"] = DISCORD_CHAT_SYSTEM_PROMPT.format(
                sender_name=sender,
                known_facts=get_user_facts(sender),
                honorific=honorific,
                owner_name=config.OWNER_NAME,
                tool_docs="",
            )
        history.append({"role": "user", "content": incoming})
        history[:] = trim_memory(history)

        # Reliability parity with the admin loop: per-turn LoopGuard instance
        # + instrumentation counters (interface="discord").
        loop_guard = LoopGuard(enforce=config.LOOP_GUARD_ENABLED)
        _turn_id = instrumentation.new_turn_id("discord")
        _rounds_used = 0
        _dc_tool_calls = 0

        try:
            for _ in range(4):
                _rounds_used += 1
                eval_msgs = list(history)
                response_msg = _execute_llm_completion(
                    messages=eval_msgs,
                    temperature=config.LLM_TEMPERATURE,
                    tools=DISCORD_TOOLS,
                )
                response_text = (response_msg.get("content") or "").strip()
                tool_payload, _cleaned_text = _extract_native_tool_call(response_msg)

                if tool_payload:
                    tool_name = (tool_payload.get("name") or "").strip()
                    tool_args = tool_payload.get("arguments") or {}
                    if not isinstance(tool_args, dict):
                        tool_args = {}

                    if tool_payload.get("error"):
                        if tool_payload.get("json_error"):
                            instrumentation.record_event("json_error_retry", turn_id=_turn_id, round=_rounds_used)
                            history.append({"role": "assistant", "content": response_text, "tool_calls": response_msg.get("tool_calls")})
                            history.append({
                                "role": "user",
                                "content": (
                                    "[System note: Your tool call contained a JSON formatting error. "
                                    "Please ensure argument values are correctly formatted and try again.]"
                                ),
                            })
                            history[:] = trim_memory(history)
                            continue
                        tool_result = f"Error: {tool_payload['error']}"
                    elif tool_name not in {"forward_to_owner", "save_personal_fact", "get_current_track"}:
                        tool_result = f"Error: Unknown tool '{tool_name}'."
                    else:
                        if tool_name == "forward_to_owner":
                            tool_args.setdefault("sender_name", sender)
                            tool_args.setdefault("message", incoming)
                        if tool_name == "save_personal_fact":
                            tool_args.setdefault("sender_name", sender)

                        _lg_verdict = loop_guard.check(tool_name, tool_args)
                        if _lg_verdict.action == "block":
                            publish_terminal(
                                f"[SYS] LoopGuard blocked repeated Discord {tool_name} (call #{_lg_verdict.count})"
                            )
                            tool_result = _lg_verdict.message
                        else:
                            publish_terminal(
                                f"[SYS] Discord tool call: {tool_name} from {sender}"
                            )
                            _dc_tool_calls += 1
                            tool_result = execute_tool(tool_name, tool_args)
                            if tool_result is None or str(tool_result).strip() == "":
                                tool_result = "Action completed successfully."
                            if _lg_verdict.action == "warn":
                                tool_result = f"{tool_result} {_lg_verdict.message}"
                            publish_terminal(f"[OUT] {tool_name} -> {_stream_preview(tool_result)}")

                    history.append({"role": "assistant", "content": response_text, "tool_calls": response_msg.get("tool_calls")})
                    history[:] = trim_memory(history)
                    history.append({
                        "role": "tool",
                        "tool_call_id": tool_payload.get("tool_call_id", ""),
                        "content": str(tool_result),
                    })
                    continue

                if not tool_payload:
                    final_text = _sanitize_discord_chat_output(response_text)
                    if not final_text:
                        final_text = f"I beg your pardon, {honorific}, but would you kindly rephrase that."
                    final_text = _enforce_discord_honorific(sender, final_text)
                    history.append({"role": "assistant", "content": final_text})
                    history[:] = trim_memory(history)
                    instrumentation.record_event(
                        "turn_complete", turn_id=_turn_id, rounds_used=_rounds_used,
                        tool_calls=_dc_tool_calls, outcome="final_text",
                    )
                    return final_text

            fallback = f"I beg your pardon, {honorific}, but I could not complete that request just now."
            history.append({"role": "assistant", "content": fallback})
            history[:] = trim_memory(history)
            instrumentation.record_event(
                "turn_complete", turn_id=_turn_id, rounds_used=_rounds_used,
                tool_calls=_dc_tool_calls, outcome="max_rounds",
            )
            return fallback
        except Exception as e:
            error_text = f"I beg your pardon, {honorific}, but an internal error occurred: {e}"
            instrumentation.record_event(
                "turn_complete", turn_id=_turn_id, rounds_used=_rounds_used,
                tool_calls=_dc_tool_calls, outcome="error",
            )
            return error_text


# ============================================================================
# CORE LLM LOGIC: Shared by CLI and Telegram
# ============================================================================
def _maybe_tag_text_mood(user_text: str) -> str:
    """Prepend [Mood: <state>] to user_text using the Tier-0 text detector.

    Rules (in priority order):
    - Skip if emotion detection is disabled, text is a system nudge, or already
      carries a [Mood:] tag.
    - For [NATIVE_IMAGE_PAYLOAD:…] — detect on the caption (text after the ']')
      and insert the tag immediately after the ']' so the payload prefix is intact.
    - For plain text / [Speaker:]-prefixed text — detect on the human text only
      and insert [Mood: X] right after [Speaker: …] if present, else at the front.
    """
    if not config.EMOTION_ENABLED:
        return user_text
    text = str(user_text)
    if text.startswith("[System Internal"):
        return user_text
    if "[Mood:" in text:
        return user_text       # already tagged (voice path)

    try:
        from tools.emotion_recognition import (detect_text_emotion, fuse_face,
                                                get_face_mood, get_ambient_voice_mood)

        # Fold the ambient reads (Tier 2 face, Tier 1b room voice) into the text
        # content read. fuse_face's policy (base wins; neutral base → fallback)
        # composes to text > face > ambient-voice: each ambient signal only fills
        # in when the stronger one is still neutral. No-op when both are off
        # (their accessors then return 'neutral').
        def _with_face(m: str) -> str:
            m = fuse_face(m, get_face_mood())
            m = fuse_face(m, get_ambient_voice_mood())
            return m

        if text.startswith("[NATIVE_IMAGE_PAYLOAD:"):
            # Keep the payload prefix intact; detect on caption after the closing ']'
            bracket_end = text.index("]") + 1
            caption = text[bracket_end:].strip()
            if not caption:
                return user_text
            mood = _with_face(detect_text_emotion(caption))
            return text[:bracket_end] + f" [Mood: {mood}] " + text[bracket_end:].lstrip()

        # Plain text — may carry a leading [Speaker: X] tag
        import re as _re
        speaker_match = _re.match(r"^(\[Speaker:[^\]]*\])\s*", text)
        if speaker_match:
            speaker_prefix = speaker_match.group(0).rstrip()
            rest = text[speaker_match.end():]
            mood = _with_face(detect_text_emotion(rest))
            return f"{speaker_prefix} [Mood: {mood}] {rest}"

        mood = _with_face(detect_text_emotion(text))
        return f"[Mood: {mood}] {text}"

    except Exception:
        return user_text


def _log_turn_mood(tagged_text: str) -> None:
    """Log the [Mood: X] tag the brain just embedded to the mood-trend store
    (roadmap Idea 3 — emotional continuity). Best-effort; never raises.

    Reads the tag off the already-embedded text so it works uniformly for the
    typed-text path (Tier 0) and the voice paths (Tier 1, tagged upstream).
    """
    if not (config.EMOTION_ENABLED and getattr(config, "MOOD_TREND_ENABLED", False)):
        return
    try:
        text = str(tagged_text)
        m = re.search(r"\[Mood:\s*([A-Za-z]+)\s*\]", text)
        if not m:
            return                          # untagged (system nudge / disabled)
        mood = m.group(1).lower()

        # Short human-readable context snippet, payload-blob aware.
        if text.startswith("[NATIVE_IMAGE_PAYLOAD:"):
            bracket_end = text.find("]")
            context = text[bracket_end + 1:] if bracket_end != -1 else ""
        else:
            context = text
        context = re.sub(r"\[Speaker:[^\]]*\]", "", context)
        context = re.sub(r"\[Mood:[^\]]*\]", "", context).strip()

        from tools.emotion_recognition import log_mood_turn
        log_mood_turn(mood, context)
    except Exception:
        pass


def _compact_context_locked(trigger: str = "manual") -> str:
    """Summarize-and-replace context compaction (shared by manual /compact and
    the auto-compact trigger). Caller MUST hold config.brain_lock.

    Uses the calibrated, image-aware estimator (core.memory._estimate_tokens)
    instead of the old chars//4 — base64 image payloads no longer inflate the
    count. Keeps the <2000-token safety lock so tiny contexts are never
    replaced by a summary bigger than the chat.
    """
    global messages
    est_tokens = _estimate_msgs_tokens(messages, budget=config.N_CTX)

    if est_tokens < 2000:
        print(f"[Aster Internal: Context is only ~{est_tokens} tokens. Compaction aborted.]")
        return f"[Aster: My memory is only at ~{est_tokens} tokens rn. Compacting this early will actually make the summary bigger than the chat, lmao. Let's wait till we hit 2000.]"

    compact_prompt = messages.copy()
    compact_prompt.append({
        "role": "user",
        "content": "[SYSTEM OVERRIDE] Write a highly dense, factual summary of our entire conversation. You MUST preserve any '[Image Memory: ...]' transcriptions exactly as they were written. Do not use conversational filler."
    })

    summary = (_execute_llm_completion(messages=compact_prompt, temperature=0.3).get("content") or "")

    # Restore state, setting summary as assistant memory
    messages = [messages[0], {"role": "assistant", "content": f"[System Memory Restored: I compacted my timeline to save VRAM. Here is everything I remember, including visual data: {summary}]"}]
    print(f"[Aster Internal: Compaction complete ({trigger}).]")
    return "[Aster: Compaction complete. I remembered all the important stuff, but my RAM is breathing again.]"


def process_user_input(user_text, status_callback=None):
    global messages, _turn_count
    _raw_user_text_for_log = user_text  # pristine snapshot, before mood tags/offers mutate it
    # Feed the awareness mood reader — skip internal system nudges so they
    # don't pollute the "user terseness" signal.
    if not str(user_text).startswith("[System Internal"):
        try:
            awareness.record_user_message(user_text)
        except Exception:
            pass

    # Google (Gmail/Calendar) pending-action resolution — short-circuits
    # before anything else, mirroring tools/sentry.py's WAITING_FOR_ID idiom.
    # Lives here (not a Telegram-only handler) so it works uniformly from any
    # interface — CLI, Telegram, WebRTC voice, or the dashboard chat — since
    # they all funnel through this one function.
    if google_auth.has_pending_action():
        resolution = google_auth.resolve_pending_action(str(user_text))
        log_raw_turn(_raw_user_text_for_log, resolution)
        return resolution

    if user_text.strip().lower() == "/compact":
        print("\n[Aster Internal: Initiating Context Compaction...]")
        with config.brain_lock:
            return _compact_context_locked("manual")

    # Manual Memory Consolidation
    if user_text.strip().lower() == "/memorize":
        print("\n[Aster Internal: Manual memory consolidation triggered by user...]")
        with config.brain_lock:
            # Trigger the extraction prompt with a custom reason
            evaluate_and_memorize("MANUAL USER REQUEST")
            print("[Aster Internal: Consolidation complete.]")
        return "[Aster: bet. i just scanned our whole chat and locked any new facts about u into my long-term memory vault. what are we doing next? \U0001F440]"

    # Count real user turns (not system nudges or slash commands).
    if not str(user_text).startswith("[System Internal"):
        _turn_count += 1

    # Embed [Mood: <state>] tag — Tier 0 text detector for typed input; voice
    # messages are already tagged by Tier 1 at the audio capture site and pass
    # through unchanged (the [Mood:] presence guard in _maybe_tag_text_mood).
    user_text = _maybe_tag_text_mood(user_text)

    # Log this real turn's mood for emotional-continuity tracking (Idea 3).
    # Mirrors the _turn_count gate above — system nudges are untagged and skipped.
    if not str(user_text).startswith("[System Internal"):
        _log_turn_mood(user_text)

    discord_message_request = _is_discord_message_request(user_text)
    discord_intent = _extract_discord_message_intent(user_text)

    # Idea 2 — inline ambient-action offer. For plain-text real turns only (skip
    # system nudges, media payloads, and Discord-routing requests): when a mood is
    # sustained during active chat, append a short [System Internal] offer so
    # Aster's same-turn reply naturally proposes (never auto-runs) an action.
    if (not discord_message_request
            and not str(user_text).startswith("[System Internal")
            and not str(user_text).startswith("[NATIVE_")):
        try:
            from tools import mood_actions
            _offer = mood_actions.maybe_mood_action_nudge()
            if _offer:
                user_text = f"{user_text}\n\n{_offer}"
        except Exception as e:
            print(f"[Aster Mood] mood-action offer skipped: {e}")

    with config.brain_lock:
        # Auto-compaction: trimming drops old facts silently; compaction
        # preserves them as a summary. Runs the same summarize-and-replace as
        # manual /compact once the estimated context crosses the threshold —
        # the triggering turn pays a one-time summarization delay, by design.
        if config.AUTO_COMPACT_ENABLED:
            try:
                _ac_est = _estimate_msgs_tokens(messages, budget=config.N_CTX)
                if _ac_est >= config.AUTO_COMPACT_THRESHOLD * config.N_CTX:
                    print(f"\n[Aster Internal: auto-compaction triggered at ~{_ac_est}/{config.N_CTX} tokens ({config.AUTO_COMPACT_THRESHOLD:.0%} threshold)...]")
                    _compact_context_locked("auto")
                    instrumentation.record_event(
                        "auto_compact",
                        est_tokens=_ac_est,
                        threshold=config.AUTO_COMPACT_THRESHOLD,
                    )
            except Exception as _ac_e:
                print(f"[Aster Internal: auto-compaction skipped: {_ac_e}]")

        # Check if the input is a native Telegram image payload
        if user_text.startswith("[NATIVE_IMAGE_PAYLOAD:"):
            payload_end = user_text.find("]")
            img_b64 = user_text[22:payload_end]
            caption = user_text[payload_end+1:].strip()

            # Sanitize Base64: strip data URI prefixes, newlines, and whitespace
            # that cause tokenization overflow in the C++ server parser
            clean_b64 = img_b64.split(",", 1)[-1] if "," in img_b64 else img_b64
            clean_b64 = clean_b64.replace("\n", "").replace("\r", "").strip()

            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean_b64}"}},
                    {"type": "text", "text": caption if caption else "Look at this image."},
                ],
            })
        else:
            messages.append({"role": "user", "content": user_text})
        messages = trim_memory(messages)
        awareness.acquire_brain()

        try:
            # ============================================================
            # MULTI-ROUND TOOL CALL LOOP
            # The LLM may need multiple rounds to chain tools (e.g.,
            # list_directory_tree → read_local_file → generate_kokoro_voice).
            # We loop until it produces a final text response or audio.
            # ============================================================
            MAX_TOOL_ROUNDS = 15
            audio_payload_tag = ""
            final_response = ""
            ack_phrases = {
                "open_application": "Launching the app.",
                "look_at_screen": "Capturing your screen.",
                "send_discord_message": "Sending your message.",
            }
            callback_tools = {"open_application", "look_at_screen", "send_discord_message"}
            callback_once_tools = {"open_application"}
            callback_once_emitted: set[str] = set()
            discord_blocked_tools = {
                "open_application",
                "look_at_screen",
            }
            tools_executed = False
            hallucination_retried = False
            cutoff_retried = False
            denial_retried = False
            failure_retried = False
            # Tools that FAILED this turn and were never successfully retried —
            # used to block the model from reporting success after a failure.
            failed_tools: set[str] = set()
            # smart_click goals attempted this turn — the model sometimes
            # re-clicks the same target hoping for a different outcome.
            gui_click_counts: dict[str, int] = {}

            # Reliability-campaign per-turn state (instrumentation + LoopGuard).
            # loop_guard is always constructed so its sensor (repeat_call events)
            # keeps recording even when LOOP_GUARD_ENABLED is False — only
            # enforcement (warn/block) is gated by the flag.
            loop_guard = LoopGuard(enforce=config.LOOP_GUARD_ENABLED)
            _turn_id = instrumentation.new_turn_id("admin")
            _rounds_used = 0
            _tool_call_count = 0
            _turn_outcome = "unknown"

            for tool_round in range(MAX_TOOL_ROUNDS):
                _rounds_used += 1
                # Stop signal check — fires before each new LLM call
                if config.STOP_REQUESTED:
                    config.STOP_REQUESTED = False
                    messages.append({
                        "role": "user",
                        "content": f'[System: Run was interrupted by /stop before it completed. Task was: "{user_text}". Awaiting next instruction from {config.OWNER_NAME}.]',
                    })
                    final_response = "Run interrupted."
                    _turn_outcome = "interrupted"
                    break

                eval_msgs = list(messages)
                # Inject ambient + time-aware context as an ephemeral system
                # message at position 1 (right after the persistent system
                # prompt). Never persisted to `messages` — re-rendered fresh
                # each round so the model always sees the current time/mood.
                _aware_block = awareness.render_context_block()
                if _aware_block:
                    eval_msgs.insert(1, {"role": "system", "content": _aware_block})
                # For Discord relay on the first round, supply a concrete formatting directive
                # with the live target/payload so the model can't get the format wrong.
                if discord_message_request and discord_intent and tool_round == 0:
                    _relay_target = discord_intent[0]
                    _relay_payload = discord_intent[1]
                    eval_msgs.append({"role": "system", "content": (
                        f'This is a Discord relay to "{_relay_target}". '
                        f'Call send_discord_message with target_name="{_relay_target}". '
                        f'For the "message" argument you MUST compose a formal butler message that '
                        f'attributes the following to "the Boss": {_relay_payload!r}. '
                        f'Begin with a polite greeting to {_relay_target.title()}. '
                        f'Example: "Good evening, {_relay_target.title()}. The Boss has asked me to convey that {_relay_payload.lower()}."'
                    )})
                response_msg = _execute_llm_completion(
                    messages=eval_msgs,
                    temperature=config.LLM_TOOL_TEMPERATURE,
                    top_p=config.LLM_TOOL_TOP_P,
                    top_k=config.LLM_TOOL_TOP_K,
                    tools=ADMIN_TOOLS,
                )
                response_text = (response_msg.get("content") or "").strip()
                tool_payload, _cleaned_text = _extract_native_tool_call(response_msg)

                if tool_payload:
                    tool_name = (tool_payload.get("name") or "").strip()
                    tool_arguments = tool_payload.get("arguments") or {}
                    if not isinstance(tool_arguments, dict):
                        tool_arguments = {}

                    if tool_payload.get("error"):
                        if tool_payload.get("json_error"):
                            instrumentation.record_event(
                                "json_error_retry", turn_id=_turn_id, round=_rounds_used,
                            )
                            # Self-correction: show the model its broken output so it can fix the JSON
                            messages.append({"role": "assistant", "content": response_text, "tool_calls": response_msg.get("tool_calls")})
                            messages.append({
                                "role": "user",
                                "content": (
                                    "[System note: Your tool call contained a JSON formatting error. "
                                    "Please ensure argument values are correctly formatted and try again.]"
                                ),
                            })
                            messages = trim_memory(messages)
                            continue
                        tool_result = f"Error: {tool_payload['error']}"
                    elif discord_message_request and tool_name in discord_blocked_tools:
                        tool_result = (
                            "Error: Discord desktop parsing and GUI automation are disabled for Discord relay requests. "
                            "Use send_discord_message instead."
                        )
                    else:
                        if tool_name == "send_discord_message" and discord_intent is not None:
                            if not tool_arguments.get("target_name"):
                                tool_arguments["target_name"] = discord_intent[0]
                            # Do NOT fall back to the raw discord_intent payload for the message —
                            # the model must craft a butler-formatted message itself.
                            # If message is missing, execute_tool will return an error that
                            # triggers the model to retry with a properly formatted message.

                        # LoopGuard: detect degenerate identical-call / ping-pong
                        # repetition before deciding whether to actually run the tool.
                        _lg_verdict = loop_guard.check(tool_name, tool_arguments)
                        _lg_blocked = (_lg_verdict.action == "block")

                        should_emit_status = (
                            not _lg_blocked
                            and status_callback is not None
                            and tool_name in callback_tools
                            and tool_name in ack_phrases
                            and (
                                tool_name not in callback_once_tools
                                or tool_name not in callback_once_emitted
                            )
                        )
                        if should_emit_status:
                            try:
                                status_callback(ack_phrases[tool_name])
                                if tool_name in callback_once_tools:
                                    callback_once_emitted.add(tool_name)
                            except Exception:
                                pass

                        try:
                            args_preview = json.dumps(tool_arguments, sort_keys=True)
                        except Exception:
                            args_preview = str(tool_arguments)

                        if _lg_blocked:
                            publish_terminal(
                                f"[SYS] LoopGuard blocked repeated {tool_name} (call #{_lg_verdict.count})"
                            )
                            tool_result = _lg_verdict.message
                            _tool_ok = True  # a guard block is not a tool failure
                        else:
                            publish_terminal(
                                f"[SYS] Executing {tool_name} {_stream_preview(args_preview, max_len=140)}"
                            )
                            publish_sentiment("working")
                            tools_executed = True
                            _tool_call_count += 1
                            print(f'[TOOL DEBUG] Executing tool: {tool_name}')
                            print(f'[TOOL DEBUG] Tool arguments: {tool_arguments}')
                            try:
                                _tool_ok, tool_result = execute_tool_ex(tool_name, tool_arguments)
                            except Exception as _tool_exc:
                                import traceback
                                print(f'[TOOL DEBUG] Tool {tool_name} raised exception:')
                                traceback.print_exc()
                                tool_result = f"Error executing {tool_name}: {_tool_exc}"
                                _tool_ok = False
                            if tool_result is None or str(tool_result).strip() == "":
                                tool_result = "Action completed successfully."

                        _preview_src = tool_result.get("text", "") if isinstance(tool_result, dict) else tool_result
                        publish_terminal(f"[OUT] {tool_name} -> {_stream_preview(_preview_src)}")

                        # Track genuinely-executed tools that failed so the final
                        # response can't claim success for them. A later successful
                        # retry of the same tool clears its entry. _tool_ok is exact
                        # for envelope-migrated tools, prefix-heuristic for the rest.
                        _tool_failed = not _tool_ok
                        if _tool_failed:
                            failed_tools.add(tool_name)
                        else:
                            failed_tools.discard(tool_name)

                        # Repeat-click guard: from the 3rd identical click in one
                        # turn, tell the model to stop hammering the same target.
                        # (LoopGuard above is the general-purpose version of this;
                        # this smart_click-specific one stays as its own warn path
                        # since LoopGuard treats smart_click as warn-only.)
                        if tool_name == "smart_click" and not _lg_blocked:
                            _goal_key = str(tool_arguments.get("goal", "")).strip().lower()
                            gui_click_counts[_goal_key] = gui_click_counts.get(_goal_key, 0) + 1
                            if gui_click_counts[_goal_key] >= 3:
                                _repeat_warn = (
                                    f" [WARNING: this was attempt #{gui_click_counts[_goal_key]} clicking "
                                    f"'{_goal_key}' this turn. If it has not worked by now, STOP clicking it — "
                                    f"try a different element description, press_key, or tell the user what is blocking you.]"
                                )
                                if isinstance(tool_result, dict) and "text" in tool_result:
                                    tool_result["text"] += _repeat_warn
                                elif isinstance(tool_result, str):
                                    tool_result += _repeat_warn

                        # LoopGuard warning (non-blocking escalation) — append after
                        # any smart_click-specific warning above.
                        if _lg_verdict.action == "warn":
                            if isinstance(tool_result, dict) and "text" in tool_result:
                                tool_result["text"] += f" {_lg_verdict.message}"
                            elif isinstance(tool_result, str):
                                tool_result += f" {_lg_verdict.message}"

                    # Preserve the assistant's action in history before injecting the observation.
                    # This gives the model a coherent ReAct chain: thought → action → observation.
                    if response_text or response_msg.get("tool_calls"):
                        messages.append({"role": "assistant", "content": response_msg.get("content"), "tool_calls": response_msg.get("tool_calls")})
                        messages = trim_memory(messages)

                    # UI tools include a task reminder so the model never forgets the original goal.
                    _UI_TOOL_NAMES = {
                        "look_at_screen", "smart_click", "smart_type",
                        "type_text", "press_key", "smart_scroll",
                    }
                    _ui_task_reminder = (
                        f'\n[TASK REMINDER: Your original goal is "{user_text}". '
                        f"Examine the screenshot: if ALL steps are complete, confirm to the user in one sentence and STOP. "
                        f"If steps remain, call the next required tool — do not speak to the user yet.]"
                    ) if tool_name in _UI_TOOL_NAMES else ""

                    # UI tools return {"text": ..., "ui_screenshot_b64": ...} for visual feedback.
                    # Inject as a multimodal message so the outer LLM sees the actual screen.
                    if isinstance(tool_result, dict) and "ui_screenshot_b64" in tool_result:
                        ui_b64 = tool_result["ui_screenshot_b64"]
                        clean_memory_string = tool_result.get("text", "Action completed.")
                        messages.append({
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ui_b64}"}},
                                {"type": "text", "text": f"Tool Execution Result: {clean_memory_string}{_ui_task_reminder}"},
                            ],
                        })
                    else:
                        # Standard text tool result — KV cache protection for audio payloads
                        clean_memory_string = str(tool_result)
                        if "[NATIVE_AUDIO_PAYLOAD:" in clean_memory_string:
                            audio_match = re.search(r'(\[NATIVE_AUDIO_PAYLOAD:.*?\])', clean_memory_string)
                            if audio_match:
                                audio_payload_tag = audio_match.group(1)
                            clean_memory_string = re.sub(r'\[NATIVE_AUDIO_PAYLOAD:.*?\]\s*', '', clean_memory_string)
                        clean_memory_string = compress_tool_output(tool_name, clean_memory_string, tool_arguments)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_payload.get("tool_call_id", ""),
                            "content": f"{clean_memory_string}{_ui_task_reminder}",
                        })
                    messages = trim_memory(messages)

                    # Terminal actions that produce no meaningful follow-up reasoning.
                    # Break immediately so the model doesn't loop back to narrate the result.
                    # ONLY on success — a failed action must go back through the loop so
                    # the model can react to the error instead of reporting blind success.
                    _TERMINAL_TOOLS = {
                        "play_spotify_track", "pause_spotify", "resume_spotify",
                        "skip_spotify_track", "previous_spotify_track", "shuffle_spotify",
                        "set_volume", "set_system_state", "boss_key",
                        "write_local_file",
                    }
                    if tool_name in _TERMINAL_TOOLS and _tool_ok:
                        final_response = "Action completed."
                        _turn_outcome = "terminal_tool"
                        break

                    if audio_payload_tag:
                        final_response = audio_payload_tag
                        _turn_outcome = "audio_payload"
                        messages.append({
                            "role": "assistant",
                            "content": "I generated and sent a voice note with my spoken response."
                        })
                        messages = trim_memory(messages)
                        break

                    # Stop signal check — fires after each tool result, before the next round
                    if config.STOP_REQUESTED:
                        config.STOP_REQUESTED = False
                        messages.append({
                            "role": "user",
                            "content": f'[System: Run was interrupted by /stop after executing {tool_name}. Task was: "{user_text}". Awaiting next instruction from {config.OWNER_NAME}.]',
                        })
                        final_response = "Run interrupted."
                        _turn_outcome = "interrupted"
                        break

                    continue

                # No tool call — this is the final text response
                response_text = response_text.strip()

                # Hallucination retry: model claims execution without calling a tool
                if not hallucination_retried and response_text and _claims_tool_execution(user_text, response_text, message=response_msg):
                    print(f"[Aster Internal: Hallucination detected — model claimed tool execution without calling one. Retrying...]")
                    hallucination_retried = True
                    instrumentation.record_event("hallucination_retry", turn_id=_turn_id, round=_rounds_used)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({
                        "role": "user",
                        "content": "[System: You claimed to execute a tool but did not actually call it. You MUST call the actual tool now. Do not describe the action — invoke the tool.]"
                    })
                    continue  # Retry the loop

                # Knowledge-cutoff retry: model refused to answer citing outdated training data
                if not cutoff_retried and response_text and _claims_knowledge_cutoff(response_text):
                    print(f"[Aster Internal: Knowledge cutoff refusal detected — forcing research tool call...]")
                    cutoff_retried = True
                    instrumentation.record_event("cutoff_retry", turn_id=_turn_id, round=_rounds_used)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({
                        "role": "user",
                        "content": f"[System: Your training data is outdated — do NOT refuse based on knowledge cutoff. You MUST call the `research` tool right now with the topic {config.OWNER_NAME} asked about. Call the tool immediately.]"
                    })
                    continue  # Retry the loop

                # Capability-denial retry: the model claimed it CANNOT do something a
                # tool provides ("I cannot directly execute a web search"). These denials
                # are always false, and they poison later turns which imitate them.
                if not denial_retried and response_text and _denies_capability(response_text):
                    print("[Aster Internal: Capability denial detected — forcing a live-web tool call...]")
                    denial_retried = True
                    instrumentation.record_event("denial_retry", turn_id=_turn_id, round=_rounds_used)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[System: That denial is FALSE — you DO have live web access. The `research` "
                            "tool performs a live web search, and `browse_web` drives a real browser. "
                            f"Never tell {config.OWNER_NAME} you cannot search the web. Call `research` "
                            "NOW with the topic he asked about and answer from its result.]"
                        ),
                    })
                    continue  # Retry the loop

                # Failure-blind retry: an action tool FAILED earlier this turn (and never
                # succeeded on a retry), yet the model is replying without acknowledging
                # it — i.e. about to report success for an action that did not happen.
                if not failure_retried and response_text and failed_tools and not _acknowledges_failure(response_text):
                    _failed_list = ", ".join(sorted(failed_tools))
                    print(f"[Aster Internal: Tool(s) failed ({_failed_list}) but response ignores it. Retrying...]")
                    failure_retried = True
                    instrumentation.record_event(
                        "failure_retry", turn_id=_turn_id, round=_rounds_used,
                        failed_tools=sorted(failed_tools),
                    )
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"[System: Your call to {_failed_list} FAILED — that action did NOT happen. "
                            "Do not tell the user it succeeded. Fix the problem and retry the failed tool now, "
                            "or honestly tell the user it failed.]"
                        ),
                    })
                    continue  # Retry the loop

                # Post-Tool Ghost Prod: nudge a response if the model goes silent after executing tools
                if not response_text and tools_executed:
                    print("[Aster Internal: Empty response after tool execution. Injecting ghost prod...]")
                    instrumentation.record_event(
                        "apathy_nudge", turn_id=_turn_id, round=_rounds_used,
                        had_failed_tools=bool(failed_tools),
                    )
                    if failed_tools:
                        _prod_content = (
                            f"[System Internal: The tool call(s) {', '.join(sorted(failed_tools))} FAILED — "
                            "the action did not complete. Tell the user honestly that it failed. Do not claim success.]"
                        )
                    else:
                        _prod_content = "[System Internal: The tool execution is complete. Generate a brief, natural response to the user confirming what you just did.]"
                    prod_msg = {
                        "role": "user",
                        "content": _prod_content,
                    }
                    messages.append(prod_msg)

                    response_msg = _execute_llm_completion(messages=messages, temperature=config.LLM_TEMPERATURE)
                    response_text = (response_msg.get("content") or "").strip()

                    # Append the final response, then wipe the ghost prod from history
                    messages.append({"role": "assistant", "content": response_text})
                    if prod_msg in messages:
                        messages.remove(prod_msg)
                    print(f"[Aster Internal: Ghost prod yielded: '{response_text[:80]}...']")

                final_response = response_text
                _turn_outcome = "final_text"
                print(f"Aster: {final_response}")
                publish_terminal(f"[OUT] Aster -> {_stream_preview(final_response)}")
                messages.append({"role": "assistant", "content": final_response})
                messages = trim_memory(messages)
                break
            else:
                # Exhausted MAX_TOOL_ROUNDS without a final text response —
                # force the LLM to summarize what it has so far
                print(f"[Aster Internal: Hit max tool rounds ({MAX_TOOL_ROUNDS}). Forcing final response...]")
                _turn_outcome = "max_rounds"
                final_response = (_execute_llm_completion(messages=messages, temperature=config.LLM_TEMPERATURE).get("content") or "")
                print(f"Aster: {final_response}")
                messages.append({"role": "assistant", "content": final_response})
                messages = trim_memory(messages)

        except Exception as e:
            import traceback
            print(f'[ASTER CRITICAL] Exception in tool call loop:')
            traceback.print_exc()
            try:
                from tools.diagnostics import send_error
                send_error("brain loop", e)
            except Exception:
                pass
            final_response = f"Error: {e}"
            _turn_outcome = "error"
        finally:
            awareness.release_brain()
            awareness.mark_observations_surfaced()
            try:
                from datetime import datetime as _dt
                if 0 <= _dt.now().hour < 5:
                    awareness.mark_late_night_remark_made()
            except Exception:
                pass

        try:
            instrumentation.record_event(
                "turn_complete",
                turn_id=locals().get("_turn_id"),
                rounds_used=locals().get("_rounds_used"),
                tool_calls=locals().get("_tool_call_count"),
                tools_executed=locals().get("tools_executed"),
                hallucination_retried=locals().get("hallucination_retried"),
                cutoff_retried=locals().get("cutoff_retried"),
                denial_retried=locals().get("denial_retried"),
                failure_retried=locals().get("failure_retried"),
                outcome=locals().get("_turn_outcome", "unknown"),
            )
        except Exception:
            pass

        purge_media_cache()

        if config.SHUTDOWN_REQUESTED:
            print("\n[Initiating Safe Shutdown Sequence...]")
            print("[Aster: Evaluating session context for permanent storage...]")
            evaluate_and_memorize("SAFE SHUTDOWN CONSOLIDATION")

            if config.OS_SHUTDOWN_DELAY > 0:
                os.system(f"shutdown /s /t {int(config.OS_SHUTDOWN_DELAY * 60)}")
                print(f"[Windows OS Shutdown scheduled in {config.OS_SHUTDOWN_DELAY} minutes.]")

            import threading as _threading
            def _delayed_exit():
                import time as _time
                _time.sleep(3)
                os._exit(0)
            print("\n[Engine safely powered down.]")
            _threading.Thread(target=_delayed_exit, daemon=True).start()

        # Auto memory consolidation every N real turns (independent of shutdown).
        elif _turn_count > 0 and _turn_count % MEMORIZE_EVERY_N_TURNS == 0:
            print(f"[Aster Internal: Auto memory consolidation (turn {_turn_count})]")
            evaluate_and_memorize(f"AUTO N-TURN CONSOLIDATION (turn {_turn_count})")
            # Fold a mood-trend summary into memory if a flush is due (Idea 3).
            # Self-gated on time / data / neutrality; cheap no-op when not due.
            try:
                from tools import mood_memory
                mood_memory.maybe_flush_mood_summary()
            except Exception as e:
                print(f"[Aster Mood] mood-trend flush skipped: {e}")

        # Final sanitization + silence layer for TTS-safe output.
        display_response = str(final_response or "").strip()
        if not display_response:
            publish_sentiment("calm")
            return ""

        # Strip bracketed system notes so they are never read aloud.
        display_response = re.sub(
            r"\[(System Note|System Internal|System Memory Restored|Image Memory):.*?\]",
            "",
            display_response,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Strip any leaked special control tokens.
        display_response = re.sub(r"<\|.*?\|>", "", display_response, flags=re.DOTALL)


        # Normalize whitespace after all cleanup passes.
        display_response = re.sub(r"\s+", " ", display_response).strip()

        # Silence fallback: never emit placeholder text to TTS.
        if not display_response:
            publish_sentiment("calm")
            return ""

        publish_sentiment(classify_sentiment(display_response))
        log_raw_turn(_raw_user_text_for_log, display_response)
        return display_response