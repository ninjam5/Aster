import json
import os
import threading
from typing import Callable


MEMORY_FILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "discord_memories.json",
)

_memory_lock = threading.Lock()


def _ensure_memory_file() -> None:
    if not os.path.exists(MEMORY_FILE_PATH):
        with open(MEMORY_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump({}, f)


def _normalize_records(raw_records) -> list[dict]:
    normalized: list[dict] = []

    if not isinstance(raw_records, list):
        return normalized

    for item in raw_records:
        if isinstance(item, dict):
            fact_text = str(item.get("fact", "")).strip()
            if not fact_text:
                continue
            normalized.append(
                {
                    "fact": fact_text,
                    "synced": bool(item.get("synced", False)),
                }
            )
        elif isinstance(item, str):
            fact_text = item.strip()
            if not fact_text:
                continue
            # Backward-compatible migration path from string-only records.
            normalized.append({"fact": fact_text, "synced": False})

    return normalized


def _load_memories() -> dict:
    _ensure_memory_file()

    try:
        with open(MEMORY_FILE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    normalized_payload: dict = {}
    for user_key, raw_records in payload.items():
        normalized_payload[str(user_key)] = _normalize_records(raw_records)

    return normalized_payload


def _save_memories(payload: dict) -> None:
    with open(MEMORY_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def save_fact(user_name: str, fact: str) -> str:
    user_key = str(user_name or "Unknown").strip() or "Unknown"
    fact_text = str(fact or "").strip()

    if not fact_text:
        return "Error: Fact text cannot be empty."

    with _memory_lock:
        payload = _load_memories()
        records = _normalize_records(payload.get(user_key, []))

        already_exists = any(r.get("fact", "").strip().lower() == fact_text.lower() for r in records)
        if already_exists:
            payload[user_key] = records
            _save_memories(payload)
            return f"[System Note: Fact already staged for {user_key}.]"

        records.append({"fact": fact_text, "synced": False})
        payload[user_key] = records
        _save_memories(payload)

    return f"[System Note: Staged personal fact for {user_key}.]"


def get_user_facts(user_name: str) -> str:
    user_key = str(user_name or "Unknown").strip() or "Unknown"

    with _memory_lock:
        payload = _load_memories()
        records = _normalize_records(payload.get(user_key, []))

    if not records:
        return "No known staged facts for this friend yet."

    facts = [entry.get("fact", "").strip() for entry in records if entry.get("fact", "").strip()]
    if not facts:
        return "No known staged facts for this friend yet."

    return "; ".join(facts)


def sync_unsynced_facts(vector_save_func: Callable[[str], str]) -> str:
    if not callable(vector_save_func):
        return "Error: Vector save function is not callable."

    with _memory_lock:
        payload = _load_memories()

        scanned_unsynced = 0
        absorbed = 0
        failed = 0

        for user_key, raw_records in payload.items():
            records = _normalize_records(raw_records)

            for entry in records:
                if entry.get("synced", False):
                    continue

                fact_text = str(entry.get("fact", "")).strip()
                if not fact_text:
                    entry["synced"] = True
                    continue

                scanned_unsynced += 1
                formatted_fact = f"User {user_key} stated: {fact_text}"

                try:
                    result = str(vector_save_func(formatted_fact) or "")
                except Exception as e:
                    failed += 1
                    entry["sync_error"] = str(e)
                    continue

                if result.lower().startswith("error") or result.lower().startswith("failed"):
                    failed += 1
                    entry["sync_error"] = result
                    continue

                entry["synced"] = True
                entry.pop("sync_error", None)
                absorbed += 1

            payload[user_key] = records

        _save_memories(payload)

    if scanned_unsynced == 0:
        return "[System Note: No unsynced Discord memory facts were found.]"

    return (
        "[System Note: Discord memory sync complete. "
        f"Unsynced scanned: {scanned_unsynced}. "
        f"Absorbed: {absorbed}. Failures: {failed}.]"
    )
