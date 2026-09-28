"""Structured per-person record (ID 13 of laya-integration.md).

Contacts were only a name→Discord-ID map (`tools/discord_api.py`) plus a flat
`config.DISCORD_FEMALE_NAMES` set, so there was **no machine-readable place** for a
person's pronouns, honorific, or relationship — which is why Discord Aster called
everyone "Sir" and could not learn better.

Store: `Aster_Vault/people.json` (per-install vault data, gitignored). Shape:

    {"farah": {"pronoun": "she", "gender": "female", "honorific": "Ma'am",
               "relationship": "friend", "asked": true, "source": "owner",
               "updated": "2026-09-28"}}

Never raises: a missing/corrupt file reads as empty and writes fail silently (logged).
"""
import json
import os
import threading
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Absolute, so launching from another directory cannot silently use a different store
# (QA 2026-09-28 — the memory vault file is absolute for the same reason).
_PATH = os.path.join(_REPO_ROOT, "Aster_Vault", "people.json")
_LOCK = threading.RLock()

# Pronoun -> honorific. "they" has no honorific in this register, so it falls through
# to the plain respectful default rather than inventing one.
_PRONOUN_HONORIFIC = {"he": "Sir", "she": "Ma'am"}
_GENDER_PRONOUN = {"male": "he", "female": "she"}
_PRONOUN_ALIASES = {
    "he": "he", "him": "he", "his": "he", "he/him": "he", "male": "he", "m": "he",
    "she": "she", "her": "she", "hers": "she", "she/her": "she", "female": "she", "f": "she",
    "they": "they", "them": "they", "their": "they", "they/them": "they",
    "unknown": "unknown", "unsure": "unknown",
}


def _load() -> dict:
    try:
        if os.path.exists(_PATH):
            with open(_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        print(f"[People] WARNING: could not read {_PATH} ({e}). Treating as empty.")
        # QA 2026-09-28: a corrupt file used to be silently treated as empty, and the
        # next write would then rewrite the file and wipe every record. Keep a copy.
        try:
            if os.path.exists(_PATH):
                os.replace(_PATH, _PATH + ".corrupt")
                print(f"[People] kept a copy at {_PATH}.corrupt")
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
        print(f"[People] WARNING: could not write {_PATH} ({e}).")


def _key(name) -> str:
    return str(name or "").strip()


def get_person(name) -> dict:
    """The stored record for a person (case-insensitive), or {} when unknown."""
    key = _key(name)
    if not key:
        return {}
    data = _load()
    if key in data:
        return dict(data[key])
    lowered = key.lower()
    for stored, record in data.items():
        if stored.lower() == lowered:
            return dict(record)
    return {}


def set_person(name, **fields) -> dict:
    """Merge `fields` into a person's record and persist. Returns the new record."""
    key = _key(name)
    if not key:
        return {}
    with _LOCK:
        data = _load()
        stored_key = key
        for existing in data:
            if existing.lower() == key.lower():
                stored_key = existing
                break
        record = dict(data.get(stored_key) or {})
        record.update({k: v for k, v in fields.items() if v is not None})
        record["updated"] = datetime.now().strftime("%Y-%m-%d")
        data[stored_key] = record
        _save(data)
    return record


def normalize_pronoun(value) -> str:
    """'she/her' / 'her' / 'f' -> 'she'; '' when it is not a pronoun we can store."""
    return _PRONOUN_ALIASES.get(str(value or "").strip().lower(), "")


def honorific_for(name) -> str:
    """'Ma'am' / 'Sir' for a known person, else the config fallback (today's behavior).

    Resolution order: an explicit honorific on the record -> a stored pronoun ->
    `config.DISCORD_FEMALE_NAMES` (back-compat) -> 'Sir'.
    """
    record = get_person(name)
    explicit = str(record.get("honorific") or "").strip()
    if explicit:
        return explicit
    pronoun = normalize_pronoun(record.get("pronoun"))
    if pronoun in _PRONOUN_HONORIFIC:
        return _PRONOUN_HONORIFIC[pronoun]
    try:
        import config
        if _key(name).lower() in getattr(config, "DISCORD_FEMALE_NAMES", set()):
            return "Ma'am"
    except Exception:
        pass
    return "Sir"


def pronoun_for(name) -> str:
    return normalize_pronoun(get_person(name).get("pronoun"))


def laya_guess_gender(name, known_facts: str = "") -> dict:
    """Laya: {male, female, unknown} from the name (plus any known facts).

    Returns {"gender": "male"|"female"|"unknown", "escalate": bool, "reason": str}.
    Never guesses on doubt — kernel off, low margin or failure all give "unknown",
    which is what makes the caller ask the person instead of misgendering them.
    """
    unknown = {"gender": "unknown", "escalate": True, "reason": "kernel unavailable"}
    if not _key(name):
        return unknown
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return unknown
        verdict = system1.choose(
            "From the name (and any known facts), is this person male, female, or "
            "impossible to tell?",
            {"A": "male (he/him)", "B": "female (she/her)", "C": "cannot tell from this"},
            key="gender",
            state={"name": _key(name), "known_facts": str(known_facts or "")[:300]},
            min_margin=0.5,
        )
    except Exception as e:
        return {"gender": "unknown", "escalate": True,
                "reason": f"kernel error ({e.__class__.__name__})"}
    if verdict.get("escalate"):
        return {"gender": "unknown", "escalate": True,
                "reason": verdict.get("reason") or "low or unavailable margin"}
    gender = {"A": "male", "B": "female", "C": "unknown"}.get(verdict.get("choice"), "unknown")
    return {"gender": gender, "escalate": False, "reason": ""}


_RELATIONSHIP_CHOICES = {
    "A": "friend",
    "B": "family (brother, sister, parent, cousin)",
    "C": "partner or spouse",
    "D": "colleague or classmate",
    "E": "acquaintance",
    "F": "cannot tell",
}


def laya_guess_relationship(name, known_facts: str = "") -> dict:
    """Laya: {friend, family, partner, colleague, acquaintance, unknown} (ID 14).

    Returns {"relationship": str, "escalate": bool, "reason": str}; "unknown" on any
    doubt, so the caller keeps today's hardcoded assumption ("friend").
    """
    unknown = {"relationship": "unknown", "escalate": True, "reason": "kernel unavailable"}
    if not _key(name):
        return unknown
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return unknown
        verdict = system1.choose(
            "How is this person related to the owner?",
            _RELATIONSHIP_CHOICES, key="relationship",
            state={"name": _key(name), "known_facts": str(known_facts or "")[:300]},
            min_margin=0.5,
        )
    except Exception as e:
        return {"relationship": "unknown", "escalate": True,
                "reason": f"kernel error ({e.__class__.__name__})"}
    if verdict.get("escalate"):
        return {"relationship": "unknown", "escalate": True,
                "reason": verdict.get("reason") or "low or unavailable margin"}
    relationship = {"A": "friend", "B": "family", "C": "partner",
                    "D": "colleague", "E": "acquaintance", "F": "unknown"}.get(
        verdict.get("choice"), "unknown")
    return {"relationship": relationship, "escalate": False, "reason": ""}


def resolve_relationship(name, known_facts: str = "") -> str:
    """Stored relationship, else one Laya guess (saved), else 'friend' (today's default)."""
    record = get_person(name)
    stored = str(record.get("relationship") or "").strip()
    if stored:
        return stored
    # QA round 2: an inconclusive guess used to be re-run on EVERY Discord message.
    if record.get("relationship_tried"):
        return "friend"
    guess = laya_guess_relationship(name, known_facts)
    set_person(name, relationship_tried=True)
    if guess.get("relationship") and guess["relationship"] != "unknown":
        set_person(name, relationship=guess["relationship"],
                   source=record.get("source") or "laya")
        return guess["relationship"]
    return "friend"


def resolve_identity(name, known_facts: str = "") -> dict:
    """Ensure a person has a pronoun: stored -> Laya guess -> ask the person.

    Returns {"pronoun", "honorific", "needs_ask", "source"}. `needs_ask` is True only
    when nothing is known AND we have not asked before (so the caller asks once).
    """
    record = get_person(name)
    pronoun = normalize_pronoun(record.get("pronoun"))
    if pronoun:
        return {"pronoun": pronoun, "honorific": honorific_for(name),
                "needs_ask": False, "source": str(record.get("source") or "stored")}

    # QA round 2: do not re-guess forever. Once a guess has been ATTEMPTED, skip the
    # model on later turns (it was paying a Laya pass every message indefinitely).
    if record.get("gender_tried"):
        return {"pronoun": "unknown", "honorific": honorific_for(name),
                "needs_ask": not bool(record.get("asked")), "source": "unknown"}

    guess = laya_guess_gender(name, known_facts)
    if guess.get("gender") in _GENDER_PRONOUN:
        pronoun = _GENDER_PRONOUN[guess["gender"]]
        set_person(name, pronoun=pronoun, gender=guess["gender"], source="laya",
                   gender_tried=True)
        return {"pronoun": pronoun, "honorific": honorific_for(name),
                "needs_ask": False, "source": "laya"}

    set_person(name, gender_tried=True)
    needs_ask = not bool(record.get("asked"))
    if needs_ask:
        set_person(name, asked=True)
    return {"pronoun": "unknown", "honorific": honorific_for(name),
            "needs_ask": needs_ask, "source": "unknown"}
