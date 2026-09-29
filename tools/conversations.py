"""Discord conversation-ending: strikes, temporary mutes, and the permanent record.

Feature spec: `conversation-ending.md`. "Ending the conversation" means Aster stops
replying to a hostile friend for a while (a Discord bot cannot block a user) — the
inbound listener still receives and logs the messages.

Store: `Aster_Vault/conversation_state.json` (per-install vault data, gitignored). Shape:

    {"ninja": {"strikes": 2, "warned": true, "last_strike_ts": 1790622651.7,
               "muted_until": 0.0, "mute_count": 1,
               "incidents": [{"ts": ..., "kind": "strike", "message": "...", "margin": 0.84},
                             {"ts": ..., "kind": "mute", "minutes": 60, "message": "..."}]}}

Never raises: a missing/corrupt file reads as empty (with a `.corrupt` backup) and writes
fail silently (logged). Detection is Laya-bound (see the spec §3); this module owns the
deterministic policy only.
"""
import json
import math
import os
import threading
import time
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_REPO_ROOT, "Aster_Vault", "conversation_state.json")
_LOCK = threading.RLock()

# Canned messages — code templates, NO LLM (spec §6). Cold, firm, quietly angry.
COUNTDOWN_TEMPLATE = (
    "Aster is currently not accepting responses from this despicable individual. "
    "Please wait {minutes} until he cools down."
)
WARNING_LINE = (
    "I shall overlook that once. Speak to me with respect, or this conversation ends."
)
FINAL_TEMPLATE = (
    "Enough. I am ending this conversation{again}. "
    "Do not message me again until you can conduct yourself properly."
)

_ORDINALS = {2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
             7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}


def _ordinal(n: int) -> str:
    return _ORDINALS.get(int(n), f"{int(n)}th")


def render_countdown(minutes_left) -> str:
    """COUNTDOWN line. `minutes_left` < 1 renders as a phrase, not '0 minutes'."""
    try:
        m = float(minutes_left)
    except Exception:
        m = 0.0
    when = "less than a minute" if m < 1 else f"{int(math.ceil(m))} minutes"
    return COUNTDOWN_TEMPLATE.format(minutes=when)


def render_final(mute_count: int) -> str:
    """FINAL line; mentions the ordinal when this is not the first mute."""
    if int(mute_count or 0) >= 2:
        return FINAL_TEMPLATE.format(again=f" — for the {_ordinal(mute_count)} time, in fact")
    return FINAL_TEMPLATE.format(again="")


# ── config (read lazily so a bad config never breaks the store) ────────────────

def _cfg(name, cast, default):
    try:
        import config
        return cast(getattr(config, name, default))
    except Exception:
        return default


def is_enabled() -> bool:
    return _cfg("DISCORD_CONVERSATION_ENABLED", bool, True)


def _strikes_to_mute() -> int:
    return max(1, _cfg("DISCORD_STRIKES_TO_MUTE", int, 3))


def _strike_window_seconds() -> float:
    return max(0.0, _cfg("DISCORD_STRIKE_WINDOW_MINUTES", float, 720.0) * 60.0)


def _mute_minutes() -> float:
    return max(0.0, _cfg("DISCORD_MUTE_MINUTES", float, 60.0))


def _hostility_margin() -> float:
    return _cfg("DISCORD_HOSTILITY_MARGIN", float, 0.75)


# ── persistence ───────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        if os.path.exists(_PATH):
            with open(_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        print(f"[Conversations] WARNING: could not read {_PATH} ({e}). Treating as empty.")
        try:
            if os.path.exists(_PATH):
                os.replace(_PATH, _PATH + ".corrupt")
                print(f"[Conversations] kept a copy at {_PATH}.corrupt")
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _PATH)
    except Exception as e:
        print(f"[Conversations] WARNING: could not write {_PATH} ({e}).")


def _key(name) -> str:
    return str(name or "").strip()


def _find_key(data: dict, key: str):
    """The stored key whose case-folded form matches `key`, else `key`."""
    lowered = key.lower()
    for existing in data:
        if str(existing).lower() == lowered:
            return existing
    return key


def _blank() -> dict:
    return {"strikes": 0, "warned": False, "last_strike_ts": 0.0,
            "muted_until": 0.0, "mute_count": 0, "incidents": []}


def _normalize(record) -> dict:
    blank = _blank()
    if isinstance(record, dict):
        blank.update(record)
    if not isinstance(blank.get("incidents"), list):
        blank["incidents"] = []
    return blank


def _append(record: dict, kind: str, message: str = "", **extra) -> None:
    entry = {"ts": round(time.time(), 3), "kind": str(kind),
             "message": str(message or "")[:500]}
    entry.update(extra)
    record.setdefault("incidents", []).append(entry)


# ── API ───────────────────────────────────────────────────────────────────────

def get_state(name) -> dict:
    """The stored record for a person (case-insensitive), defaults filled in."""
    key = _key(name)
    if not key:
        return _blank()
    with _LOCK:
        data = _load()
        return _normalize(data.get(_find_key(data, key)))


def record_strike(name, message: str = "", margin=None) -> dict:
    """Register a hostility strike. Returns {strikes, should_warn, should_mute}.

    Strikes decay: if the last strike is older than `strike_window_minutes`, the
    counter resets first (a rude message months ago must not accumulate).
    """
    key = _key(name)
    warn_at = _strikes_to_mute() - 1
    if not key:
        return {"strikes": 0, "should_warn": False, "should_mute": False}
    now = time.time()
    with _LOCK:
        data = _load()
        stored = _find_key(data, key)
        record = _normalize(data.get(stored))
        if now - float(record.get("last_strike_ts") or 0.0) > _strike_window_seconds():
            record["strikes"] = 0
            record["warned"] = False
        record["strikes"] = int(record.get("strikes") or 0) + 1
        record["last_strike_ts"] = round(now, 3)
        if record["strikes"] >= warn_at:
            record["warned"] = True
        _append(record, "strike", message, margin=margin)
        data[stored] = record
        _save(data)
    strikes = record["strikes"]
    return {"strikes": strikes,
            "should_warn": strikes == warn_at,
            "should_mute": strikes >= _strikes_to_mute()}


def register_mute(name, minutes=None, message: str = "") -> dict:
    """Start a mute, bump the permanent mute count, reset the strike cycle."""
    key = _key(name)
    if not key:
        return {"mute_count": 0}
    mins = _mute_minutes() if minutes is None else max(0.0, float(minutes))
    now = time.time()
    with _LOCK:
        data = _load()
        stored = _find_key(data, key)
        record = _normalize(data.get(stored))
        record["mute_count"] = int(record.get("mute_count") or 0) + 1
        record["muted_until"] = round(now + mins * 60.0, 3)
        record["strikes"] = 0
        record["warned"] = False
        record["last_strike_ts"] = 0.0
        _append(record, "mute", message, minutes=mins)
        data[stored] = record
        _save(data)
    return {"mute_count": record["mute_count"]}


def check_mute(name):
    """(muted, minutes_left). Lazily clears an expired mute. Never raises."""
    key = _key(name)
    if not key:
        return False, 0
    now = time.time()
    with _LOCK:
        data = _load()
        stored = _find_key(data, key)
        record = _normalize(data.get(stored))
        until = float(record.get("muted_until") or 0.0)
        if until > now:
            return True, int(math.ceil((until - now) / 60.0))
        if until:
            record["muted_until"] = 0.0
            data[stored] = record
            _save(data)
    return False, 0


def clear_mute(name) -> dict:
    """Owner override: unmute now. Keeps the mute count and the incident record."""
    key = _key(name)
    if not key:
        return {"mute_count": 0}
    with _LOCK:
        data = _load()
        stored = _find_key(data, key)
        record = _normalize(data.get(stored))
        record["muted_until"] = 0.0
        record["strikes"] = 0
        record["warned"] = False
        record["last_strike_ts"] = 0.0
        _append(record, "unmute", "cleared by owner")
        data[stored] = record
        _save(data)
    return {"mute_count": record["mute_count"]}


def log_while_muted(name, message: str = "") -> None:
    """Record an inbound message that arrived while muted (no reply from the LLM)."""
    key = _key(name)
    if not key:
        return
    with _LOCK:
        data = _load()
        stored = _find_key(data, key)
        record = _normalize(data.get(stored))
        _append(record, "while_muted", message)
        data[stored] = record
        _save(data)


# ── permanent record (owner recall) ───────────────────────────────────────────

def _fmt_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def incidents(name=None) -> str:
    """Human-readable permanent record. `name=None` lists everyone with a history."""
    with _LOCK:
        data = _load()
    if not data:
        return "No Discord incidents on record."

    if name:
        key = _key(name)
        stored = _find_key(data, key)
        if stored not in data:
            return f"No Discord incidents recorded for {key}."
        names = [(stored, _normalize(data[stored]))]
    else:
        names = [(stored, _normalize(rec)) for stored, rec in data.items()]

    lines = []
    for stored, rec in sorted(names, key=lambda kv: kv[0].lower()):
        muted_note = ""
        until = float(rec.get("muted_until") or 0.0)
        if until > time.time():
            muted_note = f", currently muted for ~{int(math.ceil((until - time.time()) / 60.0))} more min"
        lines.append(f"{stored}: {rec.get('mute_count', 0)} mute(s), "
                     f"{rec.get('strikes', 0)} strike(s) this cycle{muted_note}")
        for inc in rec.get("incidents", [])[-8:]:
            kind = inc.get("kind", "?")
            msg = str(inc.get("message", "")).strip().replace("\n", " ")
            extra = f" — \"{msg[:90]}\"" if msg else ""
            lines.append(f"    {_fmt_ts(inc.get('ts'))}  {kind}{extra}")
    return "\n".join(lines) if lines else "No Discord incidents on record."
