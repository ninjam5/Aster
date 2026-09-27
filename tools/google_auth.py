"""Shared OAuth/credential management for the Gmail + Calendar integration.

One consent grant covers both APIs, since config.GOOGLE_ACCESS_TIER is a
single setting shared by both services. Mirrors how config.py builds the
Spotify client (credentials from secrets.yaml, gated on non-empty values,
degrades to unavailable rather than crashing) — the one structural
difference is that Google's InstalledAppFlow needs an interactive browser
consent step, which must never fire from a daemon/ambient context (it would
block that thread waiting on a browser window nobody opened). Callers that
just want to use an already-connected account call get_gmail_service() /
get_calendar_service() (non-interactive, raise cleanly if not connected);
only a deliberate user action should call connect_google_account().
"""

import json
import os
from datetime import date

import config

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    _GOOGLE_LIBS_AVAILABLE = False

# Partial and Autonomous need identical Google scopes — the difference
# between them is Aster's own send-approval policy, not what Google grants.
SCOPES_BY_TIER = {
    "limited": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    ],
    "partial": [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar.events",
    ],
    "autonomous": [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar.events",
    ],
}

_credentials = None       # in-process cache
_last_refresh_failed = False  # surfaced to the awareness daemon's re-auth reminder

# Shared pending-approval slot for BOTH Gmail replies and Calendar
# guest-affecting actions — one slot, not one per service, so "send it"/
# "discard" is never ambiguous about which pending action it resolves.
# Mirrors tools/sentry.py's WAITING_FOR_ID idiom: resolved by the next
# free-text reply (checked in main.py's Telegram catch-all handler), not by
# any button/callback infrastructure.
_pending_action = None  # {"kind": str, "description": str, "payload": dict} or None

# One shared daily cap across BOTH Gmail autonomous sends and Calendar
# autonomous guest-affecting actions, so the two services can't each spend
# AUTONOMOUS_SEND_DAILY_CAP independently for double the intended budget.
_AUTONOMOUS_STATE_FILE = os.path.join(config.VAULT_DIR, "google_autonomous_sends.json")


def autonomous_sends_today() -> int:
    try:
        with open(_AUTONOMOUS_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") == date.today().isoformat():
            return int(state.get("count", 0))
    except Exception:
        pass
    return 0


def record_autonomous_send() -> None:
    try:
        with open(_AUTONOMOUS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": date.today().isoformat(), "count": autonomous_sends_today() + 1}, f)
    except Exception:
        pass


def _scopes_for_tier() -> list:
    return SCOPES_BY_TIER.get(config.GOOGLE_ACCESS_TIER, SCOPES_BY_TIER["limited"])


def _load_cached_credentials():
    if not os.path.exists(config.GOOGLE_TOKEN_PATH):
        return None
    try:
        return Credentials.from_authorized_user_file(config.GOOGLE_TOKEN_PATH, _scopes_for_tier())
    except Exception:
        return None


def _save_credentials(creds) -> None:
    try:
        with open(config.GOOGLE_TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    except Exception:
        pass


def get_credentials(interactive: bool = False):
    """Returns valid Credentials for the current access tier.

    Non-interactive by default: raises RuntimeError instead of opening a
    browser if there's no usable cached token, so ambient/tool-call paths
    fail with a clear message rather than hanging a background thread on a
    consent screen nobody is looking at. Pass interactive=True only from a
    deliberate user action (e.g. the dashboard's "Connect" button).
    """
    global _credentials, _last_refresh_failed

    if not _GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError("google-api-python-client not installed")
    if not config.GOOGLE_AVAILABLE:
        raise RuntimeError("Google integration not configured — set google.client_id/client_secret in secrets.yaml")

    creds = _credentials or _load_cached_credentials()
    needs_upgrade = bool(creds) and bool(set(_scopes_for_tier()) - set(creds.scopes or []))

    if creds and creds.valid and not needs_upgrade:
        _credentials = creds
        _last_refresh_failed = False
        return creds

    if creds and creds.expired and creds.refresh_token and not needs_upgrade:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
            _credentials = creds
            _last_refresh_failed = False
            return creds
        except Exception:
            _last_refresh_failed = True
            creds = None

    if not interactive:
        if needs_upgrade:
            raise RuntimeError(
                f"Google access tier is '{config.GOOGLE_ACCESS_TIER}' but the saved authorization "
                f"doesn't cover it yet — reconnect via the dashboard to grant the broader scope."
            )
        raise RuntimeError("Google account not connected — connect it via the dashboard first.")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=_scopes_for_tier(),
    )
    creds = flow.run_local_server(port=0)
    _save_credentials(creds)
    _credentials = creds
    _last_refresh_failed = False
    return creds


def connect_google_account():
    """Explicit, user-initiated consent flow — opens the OS browser and blocks
    the calling thread until it completes. Never call this from a daemon tick."""
    return get_credentials(interactive=True)


def is_connected() -> bool:
    """Best-effort, non-blocking check — never raises, never opens a browser."""
    try:
        creds = _credentials or _load_cached_credentials()
        return bool(creds and creds.refresh_token)
    except Exception:
        return False


def reauth_needed() -> bool:
    """True if the last refresh attempt failed — the Awareness daemon's cue
    to proactively remind the owner before Google's ~7-day token fully lapses."""
    return _last_refresh_failed


def get_gmail_service():
    return build("gmail", "v1", credentials=get_credentials())


def get_calendar_service():
    return build("calendar", "v3", credentials=get_credentials())


def tier_allows(action: str) -> bool:
    """action: 'read' | 'modify' | 'send'. All tiers can read; modify/send
    require partial or autonomous — identical Google scopes for both, so the
    partial-vs-autonomous split is enforced by the calling tool, not here."""
    tier = config.GOOGLE_ACCESS_TIER
    if action == "read":
        return True
    if action in ("modify", "send"):
        return tier in ("partial", "autonomous")
    return False


def send_policy(guardrail_ok: bool) -> str:
    """Shared send/invite decision for Gmail replies and Calendar
    guest-affecting actions: 'blocked' | 'ask' | 'send'. Partial always asks;
    Autonomous sends immediately unless guardrail_ok is False (e.g. no prior
    correspondence, or a daily cap was hit), in which case it falls back to
    asking rather than silently dropping the action."""
    tier = config.GOOGLE_ACCESS_TIER
    if tier == "limited":
        return "blocked"
    if tier == "partial":
        return "ask"
    return "send" if guardrail_ok else "ask"


def set_pending_action(kind: str, description: str, payload: dict) -> None:
    global _pending_action
    _pending_action = {"kind": kind, "description": description, "payload": payload}


def has_pending_action() -> bool:
    return _pending_action is not None


def pending_action_description() -> str:
    return _pending_action["description"] if _pending_action else ""


def resolve_pending_action(user_reply: str) -> str:
    """Called from main.py's Telegram catch-all handler when a Google action
    is pending, before the reply is routed to the LLM. Dispatches to whichever
    module created the pending action based on its 'kind'."""
    global _pending_action
    if not _pending_action:
        return "No pending Google action to resolve."

    action = _pending_action
    reply = user_reply.strip().lower()
    approve = reply in ("send it", "send", "yes", "approve", "confirm")
    deny = reply in ("discard", "cancel", "no", "reject")
    if not approve and not deny:
        return f"There's a pending action — {action['description']}. Reply 'send it' or 'discard'."

    _pending_action = None
    if action["kind"] == "gmail_reply":
        import tools.gmail_tool as gmail_tool
        return gmail_tool._finish_pending_reply(action["payload"], approve)
    if action["kind"].startswith("calendar_"):
        import tools.google_calendar as google_calendar
        return google_calendar._finish_pending_action(action["kind"], action["payload"], approve)
    return "Unknown pending action type."
