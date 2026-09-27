"""Aster first-run setup wizard.

Run once after cloning the repo — `python first_run_setup.py` — or any time
you want to add/change credentials; it's safe to re-run (existing values
become the defaults for each prompt). Writes secrets.yaml and the
identity/contacts fields of self_config.yaml. Never touches config.py.

Every integration is optional: leaving a prompt blank disables that
integration and Aster keeps working without it.

The prompting/collection side (`run_wizard`) is the only part that calls
`input()`. Everything else is pure functions operating on plain data, so the
file-writing logic can be exercised by tests without a real terminal.
"""

import re
import shutil
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
SELF_CONFIG_PATH = ROOT / "self_config.yaml"
SELF_CONFIG_EXAMPLE_PATH = ROOT / "self_config.example.yaml"
SECRETS_PATH = ROOT / "secrets.yaml"

DEFAULT_SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8081"

_SECRET_DEFAULTS = {
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_redirect_uri": DEFAULT_SPOTIFY_REDIRECT_URI,
    "telegram_bot_token": "",
    "telegram_chat_id": 0,
    "discord_bot_token": "",
    "livekit_url": "",
    "livekit_api_key": "",
    "livekit_api_secret": "",
    "firecrawl_api_key": "",
}


# ============================================================================
# Pure functions — no input(), fully testable with sample data
# ============================================================================

def load_existing_secrets(path: Path = SECRETS_PATH) -> dict:
    """Read secrets.yaml (if present) into the flat dict shape used below.

    Used to pre-fill defaults so re-running the wizard doesn't clobber
    integrations that were already configured.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    def _get(section, key, default):
        node = data.get(section)
        return node.get(key, default) if isinstance(node, dict) else default

    return {
        "spotify_client_id": _get("spotify", "client_id", ""),
        "spotify_client_secret": _get("spotify", "client_secret", ""),
        "spotify_redirect_uri": _get("spotify", "redirect_uri", DEFAULT_SPOTIFY_REDIRECT_URI),
        "telegram_bot_token": _get("telegram", "bot_token", ""),
        "telegram_chat_id": _get("telegram", "authorized_chat_id", 0),
        "discord_bot_token": _get("discord", "bot_token", ""),
        "livekit_url": _get("livekit", "url", ""),
        "livekit_api_key": _get("livekit", "api_key", ""),
        "livekit_api_secret": _get("livekit", "api_secret", ""),
        "firecrawl_api_key": _get("firecrawl", "api_key", ""),
    }


def render_secrets_yaml(values: dict) -> str:
    """Build secrets.yaml content from a flat dict of collected values.

    Missing keys fall back to blank/zero — every integration is optional.
    Hand-built (not yaml.safe_dump) so we control comments/formatting
    exactly, with no round-trip risk. Every value is escaped via
    _yaml_scalar so embedded quotes or backslashes in a credential token
    can't break the output.
    """
    v = {**_SECRET_DEFAULTS, **values}
    q = lambda key: _yaml_scalar(str(v[key]))
    return (
        "# Aster — Secrets (real values, gitignored)\n"
        "#\n"
        "# Written by first_run_setup.py. See secrets.example.yaml for field docs.\n"
        "\n"
        "spotify:\n"
        f"  client_id: {q('spotify_client_id')}\n"
        f"  client_secret: {q('spotify_client_secret')}\n"
        f"  redirect_uri: {q('spotify_redirect_uri')}\n"
        "\n"
        "telegram:\n"
        f"  bot_token: {q('telegram_bot_token')}\n"
        f"  authorized_chat_id: {int(v['telegram_chat_id'] or 0)}\n"
        "\n"
        "discord:\n"
        f"  bot_token: {q('discord_bot_token')}\n"
        "\n"
        "livekit:\n"
        f"  url: {q('livekit_url')}\n"
        f"  api_key: {q('livekit_api_key')}\n"
        f"  api_secret: {q('livekit_api_secret')}\n"
        "\n"
        "firecrawl:\n"
        f"  api_key: {q('firecrawl_api_key')}\n"
    )


def write_secrets(values: dict, path: Path = SECRETS_PATH) -> None:
    path.write_text(render_secrets_yaml(values), encoding="utf-8")


_YAML_RESERVED_WORDS = {
    w.lower() for w in ("true", "false", "yes", "no", "on", "off", "null", "~")
}


def _yaml_scalar(s: str) -> str:
    """Quote a scalar only if a bare YAML value would misparse it.

    Covers special characters, leading/trailing whitespace, empty strings,
    and values that would otherwise implicitly resolve to a bool/null/number
    (e.g. a credential that happens to be all-digits, or literally "true").
    """
    looks_numeric = bool(re.fullmatch(r"[+-]?\d+(\.\d+)?", s))
    looks_reserved = s.lower() in _YAML_RESERVED_WORDS
    if (
        s == ""
        or s != s.strip()
        or looks_numeric
        or looks_reserved
        or re.search(r'[:#"\'{}\[\]&*!|>%@`]', s)
    ):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def update_identity(
    agent_name: str,
    owner_name: str,
    female_names: list[str],
    path: Path = SELF_CONFIG_PATH,
    example_path: Path = SELF_CONFIG_EXAMPLE_PATH,
) -> None:
    """Set identity.name / identity.owner / contacts.female_names.

    Uses narrow regex substitution rather than a full YAML parse+dump, so the
    hand-written comments throughout self_config.yaml survive untouched.
    Seeds self_config.yaml from the example template first if it doesn't
    exist yet.
    """
    if not path.exists():
        shutil.copyfile(example_path, path)

    text = path.read_text(encoding="utf-8")

    agent_name = _yaml_scalar(agent_name.strip() or "Aster")
    owner_name = _yaml_scalar(owner_name.strip() or "User")

    text = re.sub(r"(?m)^(  name: ).*$", lambda m: m.group(1) + agent_name, text, count=1)
    text = re.sub(r"(?m)^(  owner: ).*$", lambda m: m.group(1) + owner_name, text, count=1)

    names = [n.strip().lower() for n in female_names if n.strip()]
    if names:
        replacement = "  female_names:\n" + "\n".join(f"    - {n}" for n in names)
    else:
        replacement = "  female_names: []"
    # Existing list items may be indented 2 OR 4 spaces (both are valid YAML
    # block-sequence styles; hand-edited files use 2) — consume any indented
    # "- item" lines so none are left dangling under the replacement list.
    text = re.sub(r"(?m)^  female_names:.*(?:\n[ \t]+-[^\n]*)*", lambda m: replacement, text, count=1)

    path.write_text(text, encoding="utf-8")


def poll_telegram_chat_id(bot_token: str, timeout_seconds: float = 60.0,
                           poll_interval: float = 2.0) -> int | None:
    """Poll getUpdates for a chat.id. Returns None on timeout/error — never raises.

    Caller is expected to have already asked the user to message their bot.
    """
    deadline = time.monotonic() + timeout_seconds
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            for update in reversed(data.get("result", [])):
                chat = (update.get("message") or {}).get("chat") or {}
                if chat.get("id"):
                    return int(chat["id"])
        except Exception:
            pass
        time.sleep(poll_interval)
    return None


# ============================================================================
# Interactive wizard — the only part that calls input()
# ============================================================================

def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else " (optional, Enter to skip)"
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def run_wizard() -> None:
    print("=" * 70)
    print("  Aster First-Run Setup")
    print("=" * 70)
    print("Every integration below is optional — leave blank to skip or keep")
    print("the existing value shown in [brackets].\n")

    existing = load_existing_secrets()

    agent_name = _prompt("Agent name", default="Aster")
    owner_name = ""
    while not owner_name:
        owner_name = _prompt("Your name (Aster's owner)")
        if not owner_name:
            print("Owner name is required.")

    female_raw = input(
        "Comma-separated names Aster should address as 'Ma'am'/'Ms.' (optional): "
    ).strip()
    female_names = [n.strip() for n in female_raw.split(",") if n.strip()]

    update_identity(agent_name, owner_name, female_names)
    print(f"\nSaved identity to {SELF_CONFIG_PATH.name}.\n")

    values = dict(existing)

    print("--- Telegram (optional — remote control via bot) ---")
    tg_token = _prompt("Telegram bot token (from @BotFather)",
                        default=existing.get("telegram_bot_token", ""))
    if tg_token:
        values["telegram_bot_token"] = tg_token
        print("Send /start to your bot now — waiting up to 60s to capture your chat ID...")
        chat_id = poll_telegram_chat_id(tg_token)
        if chat_id is None:
            manual = _prompt("Couldn't auto-detect it. Paste your chat ID manually")
            chat_id = int(manual) if manual.strip().lstrip("-").isdigit() else existing.get("telegram_chat_id", 0)
        values["telegram_chat_id"] = chat_id
        print(f"Telegram configured (chat ID: {chat_id}).\n")
    else:
        values["telegram_chat_id"] = existing.get("telegram_chat_id", 0)
        print("Skipped — Telegram features disabled.\n")

    print("--- Spotify (optional — music control) ---")
    sp_id = _prompt("Spotify client ID", default=existing.get("spotify_client_id", ""))
    if sp_id:
        values["spotify_client_id"] = sp_id
        values["spotify_client_secret"] = _prompt(
            "Spotify client secret", default=existing.get("spotify_client_secret", "")
        )
        values["spotify_redirect_uri"] = _prompt(
            "Spotify redirect URI (must match your app's dashboard exactly)",
            default=existing.get("spotify_redirect_uri", DEFAULT_SPOTIFY_REDIRECT_URI),
        )
        print("Spotify configured.\n")
    else:
        print("Skipped — Spotify tools disabled.\n")

    print("--- Firecrawl (optional — web-search fallback beyond Wikipedia) ---")
    fc_key = _prompt("Firecrawl API key", default=existing.get("firecrawl_api_key", ""))
    values["firecrawl_api_key"] = fc_key
    print("Firecrawl configured.\n" if fc_key else "Skipped — research falls back to Wikipedia only.\n")

    print("--- LiveKit (optional — voice call) ---")
    lk_url = _prompt("LiveKit URL (wss://...)", default=existing.get("livekit_url", ""))
    if lk_url:
        values["livekit_url"] = lk_url
        values["livekit_api_key"] = _prompt(
            "LiveKit API key", default=existing.get("livekit_api_key", "")
        )
        values["livekit_api_secret"] = _prompt(
            "LiveKit API secret", default=existing.get("livekit_api_secret", "")
        )
        print("LiveKit configured.\n")
    else:
        print("Skipped — voice call disabled.\n")

    print("--- Discord (optional — friend DM bridge) ---")
    dc_token = _prompt("Discord bot token", default=existing.get("discord_bot_token", ""))
    values["discord_bot_token"] = dc_token
    print("Discord configured.\n" if dc_token else "Skipped — Discord tools disabled.\n")

    write_secrets(values)
    print(f"Saved credentials to {SECRETS_PATH.name}.")
    print("\nSetup complete. Run `python main.py` to start Aster.")


if __name__ == "__main__":
    run_wizard()
