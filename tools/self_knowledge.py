"""Self-introspection layer — Aster's runtime awareness of his own systems.

The six functions below back the admin tools `get_my_config`, `list_my_contacts`,
`list_my_capabilities`, `get_my_status`, `get_my_memory_stats`, and
`describe_my_tool`. They are pure read-only and never touch the brain's main
``messages`` list.

Design split:
- **Static facts** (identity, paths, intended integrations) come from
  ``self_config.yaml`` via ``config.SELF_CONFIG``.
- **Live runtime state** (active daemons, current initiative level, library
  availability, VRAM, uptime) is read directly from the live module globals —
  never from the YAML, since those values can drift the moment a toggle fires.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

import yaml

import config

_START_TIME = time.time()


def _safe_get(obj: Any, *path, default=None):
    node = obj
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node


# ── tool 1: get_my_config ─────────────────────────────────────────────────────
def get_my_config(section: str = "all") -> str:
    """Return the full self_config.yaml, or one section of it, as YAML text."""
    cfg = config.SELF_CONFIG or {}
    if not cfg:
        return "self_config.yaml is empty or could not be loaded."
    section = (section or "all").strip().lower()
    if section in ("", "all"):
        return yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False).strip()
    if section in cfg:
        return yaml.safe_dump({section: cfg[section]}, default_flow_style=False, sort_keys=False).strip()
    available = ", ".join(cfg.keys())
    return f'Section "{section}" not found. Available sections: {available}.'


# ── tool 2: list_my_contacts ──────────────────────────────────────────────────
def list_my_contacts(platform: str = "all") -> str:
    """List known contacts across messaging platforms."""
    platform = (platform or "all").strip().lower()
    result: dict[str, Any] = {}

    if platform in ("discord", "all"):
        try:
            from tools.discord_api import CONTACTS
            result["discord"] = {
                name: f"id:{cid[-4:]}" for name, cid in CONTACTS.items()
            }
        except Exception as e:
            result["discord"] = f"Discord contacts unavailable: {e}"

    if platform in ("telegram", "all"):
        # Telegram is a single-user C2 channel — AUTHORIZED_CHAT_ID is the only contact.
        try:
            result["telegram"] = {
                f"{config.OWNER_NAME} (authorized)": f"chat_id:{config.AUTHORIZED_CHAT_ID}",
                "note": "Telegram is a single-authorized-user C2 bridge, not a contact list.",
            }
        except Exception:
            result["telegram"] = "Telegram bridge not configured."

    if platform not in ("discord", "telegram", "all"):
        return f'Unknown platform "{platform}". Valid: discord, telegram, all.'

    return json.dumps(result, indent=2)


# ── tool 3: list_my_capabilities ──────────────────────────────────────────────
def list_my_capabilities() -> str:
    """Return a structured snapshot of what Aster can do."""
    cfg = config.SELF_CONFIG or {}
    capabilities = {
        "vision": {
            "screen_capture": True,
            "webcam": True,
            "ui_element_locator": "DOM motor (ARIA/CDP + UIA, opt-in) + UIA + pytesseract + OmniParser v2 pixel fallback (flag-gated)",
            "image_re_examination": True,
            "cold_storage": _safe_get(cfg, "paths", "cold_storage_images", default="Aster_Vault/images"),
            "face_recognition": True,
            "screen_watcher": True,
        },
        "audio": {
            "transcription": "Faster-Whisper medium.en (shared process-wide)",
            "synthesis": "Kokoro TTS 82M (ref-counted, VRAM-offloaded when idle)",
            "live_call": "LiveKit Agents SDK + Silero VAD",
            "wake_word": _safe_get(cfg, "runtime", "wake_word_model", default="hey_jarvis"),
        },
        "memory": {
            "short_term": f"context window ({getattr(config, 'N_CTX', 60000)} tokens)",
            "long_term": "ChromaDB vector store",
            "markdown_vault": _safe_get(cfg, "memory", "memory_file", default="Aster_Vault/memory.md"),
            "rag_vault": _safe_get(cfg, "memory", "rag_vault_dir", default="Aster_Vault/database"),
            "fact_recall": True,
            "rag_search": True,
            "compaction": "/compact (LLM-summarised when context > 2000 tokens)",
        },
        "pc_control": {
            "power": ["shutdown", "restart", "sleep", "lock"],
            "volume": True,
            "applications": True,
            "window_management": True,
            "ui_automation": ["smart_click", "smart_type", "smart_scroll", "press_key", "highlight_on_screen"],
            "dom_motor": bool(getattr(config, "USE_DOM_MOTOR", False)),
            "pixel_fallback": bool(getattr(config, "USE_PIXEL_FALLBACK", True)),
            "system1_kernel": bool(getattr(config, "USE_LAYA_KERNEL", False)),
        },
        "integrations": {
            "discord_messaging": bool(_safe_get(cfg, "integrations", "discord", default="enabled") == "enabled"),
            "telegram_messaging": bool(getattr(config, "TELEGRAM_AVAILABLE", False)),
            "spotify_control": bool(getattr(config, "SPOTIFY_AVAILABLE", False)),
            "livekit_voice": bool(_safe_get(cfg, "integrations", "livekit", default="enabled") == "enabled"),
            "web_search_rag": True,
        },
        "modes": {
            "intervention": "tools/intervention.py — distraction-window focus daemon",
            "awareness": "tools/awareness.py — ambient context + initiative dial",
            "sentry": "tools/sentry.py — webcam intruder detection (auto-start disabled)",
            "gesture": "tools/gesture.py — MediaPipe Hands gesture daemon",
            "assist": "tools/assist.py — tkinter dim overlay highlight",
        },
    }
    return json.dumps(capabilities, indent=2)


# ── tool 4: get_my_status ─────────────────────────────────────────────────────
def _live_daemon_state() -> dict:
    """Read live ACTIVE flags from each daemon module — never from the YAML."""
    state: dict[str, Any] = {}
    try:
        from tools import sentry as _sentry
        state["sentry_mode"] = bool(_sentry.SENTRY_ACTIVE)
    except Exception:
        state["sentry_mode"] = "unknown"
    try:
        from tools import intervention as _intervention
        state["intervention_mode"] = bool(_intervention.INTERVENTION_ACTIVE)
    except Exception:
        state["intervention_mode"] = "unknown"
    try:
        from tools import gesture as _gesture
        state["gesture_mode"] = bool(_gesture.GESTURE_ACTIVE)
    except Exception:
        state["gesture_mode"] = "unknown"
    try:
        from tools import awareness as _awareness
        state["awareness_mode"] = bool(_awareness.AWARENESS_ACTIVE)
    except Exception:
        state["awareness_mode"] = "unknown"
    try:
        import webrtc_bridge
        state["webrtc_call_active"] = bool(webrtc_bridge.call_is_active())
    except Exception:
        state["webrtc_call_active"] = "unknown"
    return state


def get_my_status() -> str:
    """Return live runtime state — uptime, daemons, integrations, VRAM, RAM."""
    uptime_seconds = time.time() - _START_TIME
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)

    cfg = config.SELF_CONFIG or {}
    daemons = _live_daemon_state()
    active_daemons = [name for name, on in daemons.items() if on is True]

    status: dict[str, Any] = {
        "uptime": f"{hours}h {minutes}m",
        "personality_mode": _safe_get(cfg, "persona", "system_prompt",
                                       default=_safe_get(cfg, "identity", "personality_mode", default="jarvis")),
        "initiative_level": int(getattr(config, "INITIATIVE_LEVEL", 2)),
        "active_daemons": active_daemons,
        "daemon_state": daemons,
        "integrations_live": {
            "spotify": bool(getattr(config, "SPOTIFY_AVAILABLE", False)),
            "telegram": bool(getattr(config, "TELEGRAM_AVAILABLE", False)),
            "discord": True,
            "livekit": True,
            "memory_db": bool(getattr(config, "MEMORY_AVAILABLE", False)),
        },
        "llm_engine": "llama-server at http://localhost:8080",
        "model": _safe_get(cfg, "runtime", "llm_model", default="Qwen3.6-35B-A3B-UD-IQ4_XS"),
        "context_window": int(getattr(config, "N_CTX", 60000)),
    }

    try:
        import torch
        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated() / 1024**3
            reserved = torch.cuda.memory_reserved() / 1024**3
            status["gpu"] = torch.cuda.get_device_name(0)
            status["vram_allocated_gb"] = round(allocated, 2)
            status["vram_reserved_gb"] = round(reserved, 2)
    except Exception:
        pass

    try:
        import psutil
        process = psutil.Process()
        status["ram_mb"] = round(process.memory_info().rss / 1024**2, 1)
        status["cpu_percent"] = process.cpu_percent(interval=0.1)
    except Exception:
        pass

    return json.dumps(status, indent=2)


# ── tool 5: get_my_memory_stats ───────────────────────────────────────────────
def get_my_memory_stats() -> str:
    """Return live ChromaDB + memory.md stats."""
    try:
        from core.memory import get_collection_stats
        return json.dumps(get_collection_stats(), indent=2)
    except Exception as e:
        return f"Could not read memory stats: {e}"


# ── tool 6: describe_my_tool ──────────────────────────────────────────────────
def describe_my_tool(tool_name: str) -> str:
    """Return the schema for any admin tool by name (case-insensitive)."""
    target = (tool_name or "").strip().lower()
    if not target:
        return "Error: tool_name is required."
    try:
        from core.brain import ADMIN_TOOLS
    except Exception as e:
        return f"Could not load ADMIN_TOOLS: {e}"

    available: list[str] = []
    for tool in ADMIN_TOOLS:
        # ADMIN_TOOLS items are {"type": "function", "function": {"name": ..., ...}}
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name", ""))
        available.append(name)
        if name.lower() == target:
            return json.dumps(fn, indent=2)

    return f'Tool "{tool_name}" not found. Available ({len(available)}): {", ".join(sorted(available))}.'
