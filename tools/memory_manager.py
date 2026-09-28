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


_FACT_RELEVANCE_LEVELS = [
    "irrelevant to this message",
    "vaguely related",
    "possibly relevant",
    "clearly relevant",
]
_FACT_KEEP_AT = 0.34          # keep "vaguely related" and above
_FACT_MIN_ANSWER_CONF = 0.4   # ignore noisy scores


def _laya_select_facts(facts: list, query: str) -> list:
    """The subset of `facts` relevant to `query`, via one batched Laya pass (ID 16).

    CONSERVATIVE: returns ALL facts on any doubt — kernel off, no query, a failure, or
    when nothing clears the bar. Dropping a fact the friend's reply needed is worse
    than carrying a few extra ones.
    """
    if not facts or not str(query or "").strip():
        return facts
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return facts
        verdict = system1.score_candidates(
            _FACT_RELEVANCE_LEVELS,
            "How relevant is this stored fact to the incoming message?",
            {f"f{i}": f for i, f in enumerate(facts)},
            state={"message": str(query)[:400]},
        )
    except Exception:
        return facts
    normalized = verdict.get("normalized") or {}
    confidence = verdict.get("answer_confidence") or {}
    kept = []
    for i, fact in enumerate(facts):
        key = f"f{i}"
        score = normalized.get(key)
        if score is None or score < _FACT_KEEP_AT:
            continue
        conf = confidence.get(key)
        if conf is not None and conf < _FACT_MIN_ANSWER_CONF:
            continue
        kept.append(fact)
    return kept or facts


def get_user_facts(user_name: str, query: str = "") -> str:
    """Staged facts for a friend, optionally narrowed to the current message (ID 16).

    Without `query` this is exactly the old behaviour (every fact, joined). With it,
    Laya keeps only the relevant ones — `farah` alone carried 9 facts including a
    ~1,500-char self-description, injected on every turn.
    """
    user_key = str(user_name or "Unknown").strip() or "Unknown"

    with _memory_lock:
        payload = _load_memories()
        records = _normalize_records(payload.get(user_key, []))

    if not records:
        return "No known staged facts for this friend yet."

    facts = [entry.get("fact", "").strip() for entry in records if entry.get("fact", "").strip()]
    if not facts:
        return "No known staged facts for this friend yet."

    if query:
        facts = _laya_select_facts(facts, query)

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
