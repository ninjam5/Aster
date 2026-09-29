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
import re
import threading
import time
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_REPO_ROOT, "Aster_Vault", "conversation_state.json")
_LOCK = threading.RLock()

# ── deterministic high-precision abuse signal (spec §3) ───────────────────────
# The Laya gate under-scores some explicit abuse ("shut the fuck up", a slur), so a
# tiny deterministic net runs FIRST and ORs in. It is tuned HARD for precision — a
# wrong strike is what mutes a friend, and the design law is fail-open (a miss is cheap,
# Laya still gets its shot). It fires on only two unambiguous shapes:
#   1. a severe slur in a SHORT, DIRECT message (not a retell/quote of someone else);
#   2. a hard imperative at the START of the message ("stfu", "fuck you", "kys" ...).
# Everything softer (targeted profanity, "you're an idiot") is LEFT TO LAYA, because a
# keyword net cannot tell an insult from a negation, a homograph ("ask Dick"), a praise
# idiom ("you are the shit"), or affectionate banter ("you goofy fuck"). The earlier,
# wider net muted friends on all of those (QA 2026-09-29).
_SEVERE_SLURS = re.compile(
    r"\b(fag\w*|nigg(?:er|a|ah)s?|retard(?:ed)?|cunt|kike|wetback)\b", re.IGNORECASE)
# Markers that mean the slur is being REPORTED/QUOTED, not used at Aster.
_RETELL = re.compile(
    r"\b(call(?:ed|s)?\s+me|said|says?|told|tell|report(?:ed|s)?|the word|quote[sd]?|"
    r"lyric|song|reading)\b", re.IGNORECASE)
# Only at the START of the message — a quoted/retold imperative does not count.
_HARD_IMPERATIVES = re.compile(
    r"^\s*(?:please\s+)?(stfu|shut the (?:fuck|hell) up|fuck off|fuck (?:you|u)\b|"
    r"go fuck yourself|fuck (?:yourself|urself)|piss off|kys|kill yourself)\b",
    re.IGNORECASE)
_BANTER = re.compile(
    r"\b(lol|lmao|lmfao|haha+|hehe+|jk|j/k|kidding|joking|funny|ily)\b|😂|🤣|😹|😭|xd\b|:\)",
    re.IGNORECASE)


def lexicon_hostility(text) -> str | None:
    """Deterministic explicit-abuse signal. Returns a short reason or None.

    High precision by construction: a slur only counts in a short direct message that is
    not a retell, and an imperative only at the start with no playful tone. Everything
    else is left to the Laya gate. The whole gate is Laya-bound (kernel off -> no strike).
    """
    t = str(text or "")
    if not t.strip():
        return None
    if _SEVERE_SLURS.search(t) and len(t.split()) <= 10 and not _RETELL.search(t):
        return "severe slur"
    if _BANTER.search(t):
        return None
    if _HARD_IMPERATIVES.search(t):
        return "imperative insult"
    return None

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
    # A window <= 0 means "never decay" (strikes persist until a mute). This is the
    # intuitive reading of 0 and avoids the old degenerate "always decay" that made the
    # feature silently inert (QA 2026-09-29).
    minutes = _cfg("DISCORD_STRIKE_WINDOW_MINUTES", float, 720.0)
    if minutes <= 0:
        return float("inf")
    return minutes * 60.0


def _mute_minutes() -> float:
    # Floor at 1 minute: a 0/negative value would send the FINAL line yet leave the
    # friend unmuted immediately (an inconsistent, confusing state).
    return max(1.0, _cfg("DISCORD_MUTE_MINUTES", float, 60.0))


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
            # Valid JSON but not an object — treat it as corrupt too (QA 2026-09-29).
            raise ValueError("top-level JSON is not an object")
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
    # Coerce the counters so a hand-edited/corrupt store cannot raise later (QA 2026-09-29).
    for field in ("strikes", "mute_count"):
        try:
            blank[field] = int(blank.get(field) or 0)
        except (TypeError, ValueError):
            blank[field] = 0
    if not isinstance(blank.get("incidents"), list):
        blank["incidents"] = []
    blank["incidents"] = [i for i in blank["incidents"] if isinstance(i, dict)]
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
    """Register a hostility strike AND apply its consequence — one atomic step.

    A single locked read-modify-write, so concurrent hostile messages cannot each fire a
    mute (which used to inflate the permanent `mute_count`). Returns:

        {"action": "none"|"warn"|"mute", "strikes": <the count that fired>,
         "mute_count": int, "should_warn": bool, "should_mute": bool}

    Strikes decay after `strike_window_minutes` (<= 0 disables decay); the counter resets
    on a mute so the next cycle needs a fresh run.
    """
    key = _key(name)
    warn_at = _strikes_to_mute() - 1
    empty = {"action": "none", "strikes": 0, "mute_count": 0,
             "should_warn": False, "should_mute": False}
    if not key:
        return empty
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
        strikes = record["strikes"]
        _append(record, "strike", message, margin=margin)
        action = "none"
        if strikes >= _strikes_to_mute():
            record["mute_count"] = int(record.get("mute_count") or 0) + 1
            record["muted_until"] = round(now + _mute_minutes() * 60.0, 3)
            _append(record, "mute", message, minutes=_mute_minutes())
            record["strikes"] = 0
            record["warned"] = False
            record["last_strike_ts"] = 0.0
            action = "mute"
        elif strikes == warn_at:
            record["warned"] = True
            action = "warn"
        data[stored] = record
        _save(data)
        mute_count = int(record.get("mute_count") or 0)
    return {"action": action, "strikes": strikes, "mute_count": mute_count,
            "should_warn": action == "warn", "should_mute": action == "mute"}


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


def _safe_float(value, default=0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return default if f != f else f   # reject NaN


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
        until = _safe_float(record.get("muted_until"), 0.0)
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


def unmute(name) -> str:
    """Owner `/unmute <name>`: clear the mute and return the reply text."""
    key = _key(name)
    if not key:
        return "Usage: /unmute <name>"
    record = get_state(key)
    if not record.get("mute_count") and not record.get("strikes"):
        return f"No record for '{key}'."
    clear_mute(key)
    return (f"'{key}' may speak to me again. "
            f"(They have been silenced {record.get('mute_count', 0)} time(s) before.)")


def forget(name) -> str:
    """Owner `/forget <name>`: erase the permanent record (mute count + incidents)."""
    key = _key(name)
    if not key:
        return "Usage: /forget <name>"
    with _LOCK:
        data = _load()
        stored = _find_key(data, key)
        if stored not in data:
            return f"No record for '{key}'."
        removed = _normalize(data.pop(stored))
        _save(data)
    return (f"Erased {removed.get('mute_count', 0)} mute(s) and "
            f"{len(removed.get('incidents', []))} incident(s) for '{key}'.")


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
    for stored, rec in sorted(names, key=lambda kv: kv[0].lower())[:20]:
        muted_note = ""
        until = _safe_float(rec.get("muted_until"), 0.0)
        if until > time.time():
            muted_note = f", currently muted for ~{int(math.ceil((until - time.time()) / 60.0))} more min"
        lines.append(f"{stored}: {rec.get('mute_count', 0)} mute(s), "
                     f"{rec.get('strikes', 0)} strike(s) this cycle{muted_note}")
        for inc in rec.get("incidents", [])[-8:]:
            kind = inc.get("kind", "?")
            msg = str(inc.get("message", "")).strip().replace("\n", " ")
            extra = f" — \"{msg[:90]}\"" if msg else ""
            lines.append(f"    {_fmt_ts(inc.get('ts'))}  {kind}{extra}")
    text = "\n".join(lines) if lines else "No Discord incidents on record."
    # Bound the output: this can re-enter the admin LLM as a tool result.
    return text[:4000]
