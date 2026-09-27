"""tools/presets.py — Voice + Persona preset store.

Presets pair a persona id with a voice id under a human-readable name.
They are stored in Aster_Vault/presets.json (user-saved entries only).
Two built-in defaults are always present (non-deletable):
  - jarvis   → Jarvis persona + bm_george (British male)
  - gogi     → Gogi persona  + am_michael (American male)

Public API:
  load_presets()                 → list[dict]  (builtins first, then user-saved)
  save_preset(name, persona, voice) → str (id)
  delete_preset(preset_id)       → None  (raises ValueError for builtins/missing)
"""

import json
import os
import re
import threading

import config

_PRESETS_FILE = os.path.join(config.VAULT_DIR, "presets.json")
_lock = threading.Lock()

_BUILTIN_PRESETS: list[dict] = [
    {
        "id":      "jarvis_default",
        "name":    "Jarvis — British Butler",
        "persona": "jarvis",
        "voice":   "bm_george",
        "builtin": True,
    },
    {
        "id":      "gogi_default",
        "name":    "Gogi — Casual Friend",
        "persona": "gogi",
        "voice":   "am_michael",
        "builtin": True,
    },
]

_BUILTIN_IDS = {p["id"] for p in _BUILTIN_PRESETS}


def _load_user_presets() -> list[dict]:
    """Load user-saved presets from disk; return [] on any error."""
    try:
        if os.path.isfile(_PRESETS_FILE):
            with open(_PRESETS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                # Filter out any stale copy of a builtin id (backwards-compat guard)
                return [p for p in data if p.get("id") not in _BUILTIN_IDS]
    except Exception:
        pass
    return []


def _save_user_presets(user_presets: list[dict]) -> None:
    os.makedirs(os.path.dirname(_PRESETS_FILE), exist_ok=True)
    with open(_PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_presets, f, indent=2, ensure_ascii=False)


def load_presets() -> list[dict]:
    """Return all presets — builtins first, then user-saved."""
    with _lock:
        return list(_BUILTIN_PRESETS) + _load_user_presets()


def save_preset(name: str, persona: str, voice: str) -> str:
    """Save a named persona+voice combo; return the preset id.

    If a user preset with the same id already exists it is replaced.
    Built-in ids can never be overwritten via this function.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "preset"
    # Ensure no collision with builtin ids
    preset_id = slug
    if preset_id in _BUILTIN_IDS:
        preset_id = f"user_{slug}"

    entry = {
        "id":      preset_id,
        "name":    name,
        "persona": persona,
        "voice":   voice,
        "builtin": False,
    }
    with _lock:
        user = _load_user_presets()
        # Replace existing entry with the same id
        user = [p for p in user if p["id"] != preset_id]
        user.append(entry)
        _save_user_presets(user)

    return preset_id


def delete_preset(preset_id: str) -> None:
    """Delete a user-saved preset.  Raises ValueError for builtins or unknown ids."""
    if preset_id in _BUILTIN_IDS:
        raise ValueError(f"Cannot delete built-in preset '{preset_id}'.")
    with _lock:
        user = _load_user_presets()
        new_user = [p for p in user if p["id"] != preset_id]
        if len(new_user) == len(user):
            raise ValueError(f"Preset '{preset_id}' not found.")
        _save_user_presets(new_user)


def get_preset(preset_id: str) -> dict:
    """Return a preset by id, or raise ValueError if not found."""
    for p in load_presets():
        if p["id"] == preset_id:
            return p
    raise ValueError(f"Preset '{preset_id}' not found.")
