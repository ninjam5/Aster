"""
tools/config_writer.py — Round-trip writer for self_config.yaml.

Uses ruamel.yaml to preserve all comments and formatting in self_config.yaml
when settings are updated at runtime. Also updates the corresponding live
`config.*` globals so changes are visible immediately to the running process
without a restart.

Install:  python -m pip install ruamel.yaml
"""

import os
import config as _config

_SELF_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "self_config.yaml",
)


def _get_ruamel():
    """Return a ruamel.yaml.YAML instance configured for round-trip editing."""
    try:
        from ruamel.yaml import YAML
    except ImportError as e:
        raise ImportError(
            "ruamel.yaml is required for config writing. "
            "Install with: python -m pip install ruamel.yaml"
        ) from e
    yml = YAML()
    yml.preserve_quotes = True
    return yml


def set_config_values(updates: dict) -> None:
    """Write `updates` into self_config.yaml (preserving comments) and apply
    the changes to live config.* globals immediately.

    `updates` is a flat dict mapping dotted yaml paths to values, e.g.:
        {
            "settings.llm_temperature": 0.7,
            "settings.voice_name": "am_michael",
            "persona.system_prompt": "jarvis",
        }

    For restart-required knobs (runtime.context_window, runtime.kv_cache_type)
    the caller is responsible for rewriting start.bat — this function only
    persists the yaml side.
    """
    yml = _get_ruamel()
    with open(_SELF_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yml.load(f)

    for dotted_key, value in updates.items():
        parts = dotted_key.split(".")
        node = data
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value

    with open(_SELF_CONFIG_PATH, "w", encoding="utf-8") as f:
        yml.dump(data, f)

    # ── Apply to live config globals ─────────────────────────────────────────
    _apply_live(updates)


def _apply_live(updates: dict) -> None:
    """Mirror yaml updates onto config.* globals so the running process sees
    them immediately (no restart needed for sampling knobs)."""
    _MAP = {
        "settings.llm_temperature": ("LLM_TEMPERATURE", float),
        "settings.llm_top_p":       ("LLM_TOP_P",       float),
        "settings.llm_top_k":       ("LLM_TOP_K",       int),
        "settings.voice_speed":     ("VOICE_SPEED",     float),
        "settings.voice_name":      ("VOICE_NAME",      str),
        "persona.system_prompt":    ("SYSTEM_PROMPT",   str),
        "integrations.google.access_tier": ("GOOGLE_ACCESS_TIER", str),
        # context_window / kv_cache_type are restart-gated — NOT hot-applied.
    }
    for dotted_key, value in updates.items():
        if dotted_key in _MAP:
            attr, cast = _MAP[dotted_key]
            setattr(_config, attr, cast(value))


# ── start.bat rewriter (restart-gated knobs) ─────────────────────────────────

_STARTBAT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "start.bat",
)

_CTX_PATTERN  = r"--ctx-size\s+\d+"
_CTK_PATTERN  = r"-ctk\s+\S+"
_CTV_PATTERN  = r"-ctv\s+\S+"


def rewrite_startbat(context_window: int = None, kv_cache_type: str = None) -> None:
    """Rewrite the relevant launch args in start.bat.

    Only modifies the specific token(s) passed; leaves everything else unchanged.
    Does nothing if start.bat is not found (CI / non-Windows environments).
    """
    import re
    if not os.path.isfile(_STARTBAT_PATH):
        return

    with open(_STARTBAT_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    if context_window is not None:
        content = re.sub(_CTX_PATTERN, f"--ctx-size {int(context_window)}", content)

    if kv_cache_type is not None:
        safe = str(kv_cache_type).strip()
        content = re.sub(_CTK_PATTERN, f"-ctk {safe}", content)
        content = re.sub(_CTV_PATTERN, f"-ctv {safe}", content)

    with open(_STARTBAT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
