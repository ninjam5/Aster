import json
import os
from typing import Any

import requests

import config


_CONTACTS_FALLBACK = {
    # Real contacts live in Aster_Vault/discord_contacts.json (gitignored,
    # per-install). This fallback only kicks in when that file is missing —
    # e.g. a fresh checkout — so it stays a placeholder, never real IDs.
    # The "INSERT_ID_HERE" prefix trips the graceful-error check below.
    "example_friend": "INSERT_ID_HERE_example_friend",
}


def _load_contacts() -> dict:
    """Load CONTACTS from the JSON file pointed to by self_config.yaml.

    Falls back to the bundled defaults if the file is missing or malformed,
    so Discord still works on a fresh checkout without the JSON file.
    """
    path = getattr(config, "DISCORD_CONTACTS_FILE", None)
    if not path or not os.path.exists(path):
        return dict(_CONTACTS_FALLBACK)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        print(f"[Discord] WARNING: failed to read {path} ({e}). Using bundled contacts.")
    return dict(_CONTACTS_FALLBACK)


CONTACTS: dict = _load_contacts()


DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_BOT_TOKEN = config._secret("discord", "bot_token", default="")


def _extract_error_detail(response: requests.Response) -> str:
    try:
        payload: Any = response.json()
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    except Exception:
        pass

    text = (response.text or "").strip()
    return text[:280] if text else "Unknown Discord API error"


def send_discord_message(target_name, message):
    """Send a Discord DM via bot token + REST API only (no discord.py)."""
    lookup_name = str(target_name or "").strip().lower()
    original_name = str(target_name or "").strip()
    content = str(message or "").strip()

    if not lookup_name:
        return "Error: target_name is required."
    if not content:
        return "Error: message is required."

    recipient_id = CONTACTS.get(lookup_name)
    if not recipient_id:
        return f"Error: Unknown Discord contact '{original_name}'."
    if str(recipient_id).startswith("INSERT_ID_HERE"):
        return f"Error: Contact '{lookup_name}' does not have a real Discord ID configured yet."

    token = DISCORD_BOT_TOKEN.strip()
    if not token:
        return "Error: DISCORD_BOT_TOKEN is empty."

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    # 1) Open (or fetch) DM channel with recipient.
    try:
        dm_response = requests.post(
            f"{DISCORD_API_BASE}/users/@me/channels",
            headers=headers,
            json={"recipient_id": str(recipient_id)},
            timeout=15,
        )
    except requests.RequestException as e:
        return f"Error: Failed to open Discord DM channel - {e}"

    if dm_response.status_code not in (200, 201):
        detail = _extract_error_detail(dm_response)
        return (
            f"Error: Discord DM channel creation failed "
            f"(HTTP {dm_response.status_code}) - {detail}"
        )

    try:
        channel_id = str((dm_response.json() or {}).get("id", "")).strip()
    except Exception:
        channel_id = ""

    if not channel_id:
        return "Error: Discord API did not return a DM channel id."

    # 2) Send the exact message body as provided by the LLM.
    try:
        msg_response = requests.post(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            headers=headers,
            json={"content": content},
            timeout=15,
        )
    except requests.RequestException as e:
        return f"Error: Failed to send Discord DM - {e}"

    if msg_response.status_code not in (200, 201):
        detail = _extract_error_detail(msg_response)
        return (
            f"Error: Discord message send failed "
            f"(HTTP {msg_response.status_code}) - {detail}"
        )

    return f"[System Note: Message delivered to {lookup_name} on Discord.]"
