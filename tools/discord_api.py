import difflib
import json
import os
import re
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


def contact_names() -> list:
    """Canonical contact names — the real JSON keys, case preserved (ID 15)."""
    return list(CONTACTS.keys())


def resolve_contact(name):
    """(canonical_name, id) for an EXACT case/whitespace-insensitive name, else None.

    Fixes the live 'Adham' bug (Part A): the keys in discord_contacts.json are not all
    lowercase, but every lookup path lowercased the input first, so a capitalised key
    was unreachable — "Unknown Discord contact 'Adham'" for a contact that exists.
    """
    key = str(name or "").strip().lower()
    if not key:
        return None
    for canonical, recipient_id in CONTACTS.items():
        if canonical.strip().lower() == key:
            return canonical, recipient_id
    return None


def _fuzzy_token_match(text: str):
    """Deterministic typo resolution against the contact names (no model).

    Measured 2026-09-28: `difflib` resolves misspellings cleanly ('geroge' -> george,
    'farrah' -> farah, 'maski' -> masky) and correctly returns nothing for words that
    are not names ('brother', 'plumber') — while Laya's margins on the same typos were
    0.15-0.37 (it even guessed 'tiger' for "my brother"). So typos go to difflib and
    semantics go to Laya.
    """
    tokens = re.findall(r"[A-Za-z0-9_.@-]+", str(text or "").lower())
    lowered = [n.lower() for n in contact_names()]
    for token in tokens:
        if len(token) < 4:
            continue
        hit = difflib.get_close_matches(token, lowered, n=1, cutoff=0.8)
        if hit:
            for canonical in contact_names():
                if canonical.lower() == hit[0]:
                    return canonical
    return None


def looks_like_contact(text: str) -> bool:
    """Cheap deterministic gate: could this text name a known contact? (no model)

    QA round 6: the relay prefilter used a bare substring test, so a TYPO'd name
    ("geroge") matched nothing and the typo resolution never ran. This adds the
    difflib near-match, which is free.
    """
    lowered = str(text or "").lower()
    if any(str(n).lower() in lowered for n in contact_names()):
        return True
    return bool(_fuzzy_token_match(text))


def laya_pick_contact(request_text: str) -> dict:
    """Laya: which known contact does this request address? (ID 15 / Part B)

    Returns {"name": canonical or None, "exact": bool, "escalate": bool, "reason": str}.
    `exact` is True only when that contact's name appears verbatim (case-insensitive)
    in the request — those may send without confirmation. Anything else is a guess, so
    the caller must confirm it first (the owner's choice: never DM a guessed contact
    silently).

    Uses Laya's strong shape — candidates as `criteria`, the request in the state —
    and never invents a contact: no match, low margin, kernel off or failure all come
    back with name=None.
    """
    names = contact_names()
    if not names:
        return {"name": None, "exact": False, "escalate": True, "reason": "no contacts configured"}
    text = str(request_text or "")
    tokens = re.findall(r"[a-z0-9_.@-]+", text.lower())
    # Longest first, so "georgette" can never match "george".
    by_length = sorted(names, key=lambda n: len(str(n)), reverse=True)
    if len(tokens) <= 2:
        # A bare name (or name + punctuation) — an exact match.
        for canonical in by_length:
            if str(canonical).strip().lower() in tokens:
                return {"name": canonical, "exact": True, "escalate": False,
                        "reason": "name appears verbatim in the request"}
    else:
        # A full REQUEST: a name in it may belong to the payload rather than the
        # addressee ("tell my brother to say hi to Farah"), so it is only a guess and
        # must be confirmed. QA 2026-09-28: this used to be a whole-text substring
        # check that returned exact=True and skipped the confirm gate entirely.
        for canonical in by_length:
            if str(canonical).strip().lower() in tokens:
                return {"name": canonical, "exact": False, "escalate": False,
                        "reason": "name appears in the request — confirm before sending"}

    # Typo/misspelling -> deterministic fuzzy match (still a guess: confirm first).
    typo = _fuzzy_token_match(text)
    if typo:
        return {"name": typo, "exact": False, "escalate": False,
                "reason": f"close spelling match for '{typo}' (confirm before sending)"}

    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return {"name": None, "exact": False, "escalate": True, "reason": "kernel disabled"}
        criteria = {chr(65 + i): n for i, n in enumerate(names[:18])}
        criteria["Z"] = "none of these — no known contact matches"
        verdict = system1.choose(
            "Which known contact is this message meant for?",
            criteria, key="contact", state={"request": text[:400]}, min_margin=0.5)
    except Exception as e:
        return {"name": None, "exact": False, "escalate": True,
                "reason": f"kernel error ({e.__class__.__name__})"}
    if verdict.get("escalate"):
        return {"name": None, "exact": False, "escalate": True,
                "reason": verdict.get("reason") or "low or unavailable margin"}
    choice = verdict.get("choice")
    if not choice or choice == "Z":
        return {"name": None, "exact": False, "escalate": False, "reason": "no matching contact"}
    return {"name": criteria.get(choice), "exact": False, "escalate": False,
            "reason": "Laya pick (not an exact name in the request)"}


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


def send_discord_message(target_name, message, confirm=False, assume_guess=False):
    """Send a Discord DM via bot token + REST API only (no discord.py).

    Target resolution (ID 15):
      1. an EXACT case-insensitive name match sends immediately;
      2. otherwise Laya picks among the known contacts, and unless `confirm=True` the
         tool REFUSES and reports the guess so it can be confirmed first — a wrong
         guess must never DM the wrong person silently.

    `assume_guess=True` means the CALLER already knows the target was guessed (e.g. the
    relay parser resolved a nickname to a real key). QA round 2: without it the guess
    was canonicalised before this function saw it, so `resolve_contact` succeeded and
    the confirmation was silently skipped — the gate was advisory only.
    """
    original_name = str(target_name or "").strip()
    content = str(message or "").strip()

    if not original_name:
        return "Error: target_name is required."
    if not content:
        return "Error: message is required."

    if assume_guess and not confirm:
        return (
            f"FAILED — [CONFIRM REQUIRED: '{original_name}' was inferred from a nickname "
            f"or fuzzy match, not stated exactly. Ask {config.OWNER_NAME} to confirm, then "
            f"re-call send_discord_message with target_name='{original_name}' and "
            f"confirm=true. Nothing was sent.]"
        )

    resolved = resolve_contact(original_name)
    if resolved:
        canonical_name, recipient_id = resolved
    else:
        pick = laya_pick_contact(original_name)
        if not pick.get("name"):
            extra = f" ({pick.get('reason')})" if pick.get("reason") else ""
            return f"Error: Unknown Discord contact '{original_name}'.{extra}"
        if not confirm:
            return (
                f"FAILED — [CONFIRM REQUIRED: '{original_name}' is not an exact contact "
                f"name — it resolves to '{pick['name']}'. Ask {config.OWNER_NAME} to "
                f"confirm, then re-call send_discord_message with "
                f"target_name='{pick['name']}' and confirm=true. Nothing was sent.]"
            )
        canonical_name, recipient_id = pick["name"], CONTACTS.get(pick["name"])

    if str(recipient_id).startswith("INSERT_ID_HERE"):
        return f"Error: Contact '{canonical_name}' does not have a real Discord ID configured yet."

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

    return f"[System Note: Message delivered to {canonical_name} on Discord.]"
