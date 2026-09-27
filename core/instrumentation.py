"""
Reliability-campaign instrumentation — cheap, log-only counters for the agentic
loop's existing failure mitigations (hallucination retry, apathy nudge, cutoff
retry, failure-blind retry, JSON-error re-prompt) plus per-turn round/outcome
stats and repeated-tool-call sensing.

Writes one JSON object per line to Aster_Vault/reliability_log.jsonl and prints
a mirrored "[RELIABILITY] ..." line (auto-forwarded by tools/diagnostics.py's
stdout shim to Telegram diagnostics mode). Modeled on
tools/emotion_recognition.py:log_mood_turn() — thread-safe, append-only, never
raises.

This module changes NO agent behavior by itself; it only makes failure rates
measurable. See .claude/skills/aster-gemma-reliability-campaign.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

import config

_log_lock = threading.Lock()


def new_turn_id(interface: str) -> str:
    """A short, sortable id for correlating every event within one user turn."""
    return f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{interface}-{int(time.monotonic() * 1000) % 100000}"


def record_event(event: str, **fields) -> None:
    """Append one instrumentation event. No-op when RELIABILITY_LOG_ENABLED is False.

    Never raises — a failure here must never interrupt the agentic loop it is
    observing.
    """
    if not config.RELIABILITY_LOG_ENABLED:
        return
    try:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **fields,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _log_lock:
            with open(config.RELIABILITY_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        print(f"[RELIABILITY] {line}")
    except Exception as e:
        print(f"[Aster Reliability] instrumentation write failed: {e}")


def read_log(max_lines: int | None = None) -> list[dict]:
    """Read the JSONL log into a list of dicts (oldest first). Skips malformed
    lines. Returns [] if the file doesn't exist yet."""
    try:
        with open(config.RELIABILITY_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    except Exception:
        return []

    if max_lines is not None:
        lines = lines[-max_lines:]

    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries
