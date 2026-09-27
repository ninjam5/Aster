"""Stage 3 — System-1 decision kernel (Laya).

Bounded, calibrated decisions over the DOM motor's shortlist: which operation,
which element, is-this-state-true. Laya is a non-autoregressive, text-only
decision model (choice / score / noul over predefined schemas) — it never
generates text, so it cannot plan or type; the LLM plans and supplies free text,
deterministic code executes, and this kernel only picks among candidates.

Responsibilities:
  - lazy, ref-counted model load (CPU-first; same acquire/release shape as
    tools/audio.py) so an unused kernel costs nothing
  - neutral-key choice schemas — deliberately avoids Laya's documented `noul`
    label-bias bug (#156) for yes/no gates
  - top-1/top-2 margin gating; below threshold or any failure the verdict is
    `escalate=True` and the caller falls back to the existing LLM/UIA path
  - JSONL decision logging (config.LAYA_LOG_PATH) with the full distribution,
    for the Stage-4 calibration dataset

Never raises to callers: every public function returns an escalate verdict on
any failure. The module is import-safe without `laya` installed (lazy import).
"""
import json
import math
import os
import threading
import time

MAX_SHORTLIST = 18

_OPERATION_CRITERIA = {
    "A": "click a button, link, menu item, or other control",
    "B": "type text into a text field or search box",
    "C": "press a keyboard key or shortcut",
    "D": "scroll the page or window",
    "E": "wait for the page or app to load / change",
    "F": "the goal is already complete (done)",
    "G": "cannot proceed safely (blocked; needs a different approach or help)",
}
_OPERATION_BY_KEY = {
    "A": "click", "B": "type", "C": "press", "D": "scroll",
    "E": "wait", "F": "done", "G": "blocked",
}

_MODEL = None
_REFCOUNT = 0
_LOCK = threading.RLock()
_LOG_LOCK = threading.Lock()


def kernel_enabled() -> bool:
    try:
        import config
        return bool(getattr(config, "USE_LAYA_KERNEL", False))
    except Exception:
        return False


def _margin_threshold() -> float:
    try:
        import config
        return float(getattr(config, "LAYA_MARGIN_THRESHOLD", 0.25))
    except Exception:
        return 0.25


# ---------------------------------------------------------------------------
# Model lifecycle (lazy + ref-counted)
# ---------------------------------------------------------------------------
def _load_model():
    """Import and construct the Laya agent. Raises on any failure — callers
    convert that into an escalate verdict. Device is passed through when the
    installed Laya version accepts it, otherwise the library default is used."""
    import config
    import laya
    model_id = str(getattr(config, "LAYA_MODEL_ID", "convaiinnovations/laya"))
    device = str(getattr(config, "LAYA_DEVICE", "cpu"))
    print(f"[System1] Loading Laya checkpoint {model_id} (device={device}) ...")
    try:
        return laya.load(model_id, device=device)
    except TypeError:
        return laya.load(model_id)


def acquire():
    """Load on first acquire; increment the refcount."""
    global _MODEL, _REFCOUNT
    with _LOCK:
        if _REFCOUNT == 0 and _MODEL is None:
            _MODEL = _load_model()
        _REFCOUNT += 1
    return _MODEL


def _free_model_memory():
    """Drop the cached model and release accelerator memory.

    Laya runs on CPU by default (RAM, no VRAM), but if LAYA_DEVICE=cuda this
    empties the CUDA cache so the VRAM is genuinely returned after the last
    caller releases.
    """
    global _MODEL
    if _MODEL is not None:
        print("[System1] Unloading Laya (refcount 0) and freeing memory")
    _MODEL = None
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def release():
    """Decrement the refcount; unload at zero unless LAYA_KEEP_RESIDENT."""
    global _REFCOUNT
    try:
        import config
        keep = bool(getattr(config, "LAYA_KEEP_RESIDENT", False))
    except Exception:
        keep = False
    with _LOCK:
        if _REFCOUNT > 0:
            _REFCOUNT -= 1
        if _REFCOUNT == 0 and not keep:
            _free_model_memory()


def _predict(state: dict, questions: dict):
    """Model call seam (mocked in tests). One forward pass over all questions."""
    agent = acquire()
    try:
        return agent.predict(state, questions)
    finally:
        release()


def reset_model():
    """Testing/diagnostics: drop the cached model and refcount."""
    global _REFCOUNT
    with _LOCK:
        _REFCOUNT = 0
        _free_model_memory()


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------
def _log(entry: dict) -> None:
    try:
        import config
        path = getattr(config, "LAYA_LOG_PATH", None)
    except Exception:
        path = None
    if not path:
        return
    try:
        entry = dict(entry)
        entry["ts"] = time.time()
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        # Serialize appends: concurrent short writes from independent handles
        # are not atomic on Windows and corrupt the calibration JSONL.
        with _LOG_LOCK:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        print(f"[System1] log write failed: {e}")


# ---------------------------------------------------------------------------
# Verdict extraction
# ---------------------------------------------------------------------------
def _extract_choice(answer: dict, valid_keys: set):
    """(choice_key, margin, distribution) from a Laya answer dict, tolerantly.

    Margin = top-1 minus top-2 from the probability distribution. When only a
    scalar `confidence` is present it is ONLY usable for binary decisions,
    where top1-top2 == 2c-1; for >2 options it yields margin=None so the gate
    escalates instead of letting a 0.9-confidence near-tie pass.
    """
    if not isinstance(answer, dict):
        return None, None, None
    choice = answer.get("choice")
    if choice is None:
        choice = answer.get("value") or answer.get("key")
    if not isinstance(choice, str) or choice not in valid_keys:
        choice = None
    probs = answer.get("probabilities") or answer.get("probability")
    distribution = dict(probs) if isinstance(probs, dict) else None
    margin = None
    if distribution and len(distribution) >= 2:
        try:
            vals = sorted((float(v) for v in distribution.values()), reverse=True)
            candidate = vals[0] - vals[1]
            if math.isfinite(candidate):
                margin = candidate
        except Exception:
            margin = None
    if margin is None and answer.get("confidence") is not None and len(valid_keys) == 2:
        try:
            candidate = 2.0 * float(answer["confidence"]) - 1.0
            if math.isfinite(candidate):
                margin = candidate
        except Exception:
            margin = None
    return choice, margin, distribution


def _gate_escalates(margin) -> bool:
    """True when a margin is missing, non-finite, or below threshold."""
    threshold = _margin_threshold()
    if margin is None or not math.isfinite(margin):
        return True
    return margin < threshold


def _node_description(node) -> str:
    if isinstance(node, dict):
        return f"{node.get('role', '?')}: {node.get('name') or node.get('text') or '(unnamed)'}"
    return f"?: {node}"


def _ask(state: dict, questions: dict, key: str, valid_keys):
    """Run one predict() and extract the choice. (None, None, None, reason)."""
    try:
        result = _predict(state, questions)
    except Exception as e:
        return None, None, None, f"kernel failure: {e.__class__.__name__}: {e}"
    if not isinstance(result, dict):
        return None, None, None, "malformed model response"
    answers = result.get("answers")
    if not isinstance(answers, dict):
        return None, None, None, "no answers in model response"
    answer = answers.get(key)
    if not isinstance(answer, dict):
        return None, None, None, "no answer in model response"
    choice, margin, distribution = _extract_choice(answer, valid_keys)
    if choice is None:
        return None, margin, distribution, "unrecognized choice key"
    return choice, margin, distribution, ""


def _verdict(kind: str, goal: str, choice, margin, distribution, escalate,
             reason: str, **extra):
    verdict = {
        "kind": kind, "goal": goal, "choice": choice, "margin": margin,
        "distribution": distribution, "escalate": bool(escalate),
        "reason": reason or ("low or unavailable margin" if escalate else ""),
        "threshold": _margin_threshold(),
    }
    verdict.update(extra)
    _log(verdict)
    return verdict


# ---------------------------------------------------------------------------
# Public decisions
# ---------------------------------------------------------------------------
def pick_element(goal: str, shortlist, op: str = None) -> dict:
    """Choose one candidate from the DOM motor's shortlist (neutral keys A..R).

    Returns {"index", "choice", "margin", "escalate", "distribution", ...}.
    `index` indexes into the provided shortlist (None when escalating).
    """
    try:
        nodes = list(shortlist or [])[:MAX_SHORTLIST]
    except TypeError:
        nodes = []
    if not nodes:
        return _verdict("element", goal, None, None, None, True, "empty shortlist",
                        index=None, op=op)
    keys = [chr(ord("A") + i) for i in range(len(nodes))]
    criteria = {k: _node_description(n) for k, n in zip(keys, nodes)}
    instruction = f"Which element should be used to: {goal}?"
    if op:
        instruction += f" (the intended action is '{op}')"
    questions = {
        "element": {"type": "choice", "instructions": instruction,
                    "criteria": criteria}
    }
    choice, margin, distribution, reason = _ask(
        {"goal": goal, "options": criteria}, questions, "element", keys)
    index = keys.index(choice) if choice in keys else None
    escalate = index is None or _gate_escalates(margin)
    if escalate and not reason:
        reason = "unrecognized choice" if index is None else "low or unavailable margin"
    return _verdict("element", goal, choice, margin, distribution, escalate,
                    reason, index=index, op=op, keys=keys, criteria=criteria)


def pick_operation(goal: str) -> dict:
    """Choose the next bounded operation for a goal (click/type/.../blocked)."""
    valid = set(_OPERATION_BY_KEY)
    questions = {
        "operation": {
            "type": "choice",
            "instructions": f"What is the next action to accomplish: {goal}?",
            "criteria": _OPERATION_CRITERIA,
        }
    }
    choice, margin, distribution, reason = _ask(
        {"goal": goal, "options": _OPERATION_CRITERIA}, questions,
        "operation", valid)
    operation = _OPERATION_BY_KEY.get(choice) if choice else None
    escalate = operation is None or _gate_escalates(margin)
    if escalate and not reason:
        reason = "unrecognized operation" if operation is None else "low or unavailable margin"
    return _verdict("operation", goal, operation, margin, distribution, escalate,
                    reason, key=choice, keys=list(_OPERATION_CRITERIA),
                    criteria=_OPERATION_CRITERIA)


def check_state(question: str, yes_description: str, no_description: str,
                state_text: str = "") -> dict:
    """Neutral-key yes/no gate (deliberately NOT Laya `noul` — bug #156).

    Returns {"answer": bool | None, "margin", "escalate", ...}.
    """
    criteria = {"A": yes_description, "B": no_description}
    questions = {
        "check": {"type": "choice", "instructions": question, "criteria": criteria}
    }
    state = {"question": question, "state": state_text}
    choice, margin, distribution, reason = _ask(state, questions, "check", {"A", "B"})
    answer = None
    if choice == "A":
        answer = True
    elif choice == "B":
        answer = False
    escalate = answer is None or _gate_escalates(margin)
    if escalate and not reason:
        reason = "unrecognized answer" if answer is None else "low or unavailable margin"
    return _verdict("state", question, answer, margin, distribution, escalate,
                    reason, key=choice, answer=answer, criteria=criteria,
                    state_text=state_text)


def kernel_status() -> dict:
    """Cheap, read-only status (never loads the model)."""
    status = {"enabled": kernel_enabled(), "loaded": _MODEL is not None,
              "refcount": _REFCOUNT}
    try:
        import config
        status["model"] = str(getattr(config, "LAYA_MODEL_ID", ""))
        status["device"] = str(getattr(config, "LAYA_DEVICE", "cpu"))
        status["margin_threshold"] = float(getattr(config, "LAYA_MARGIN_THRESHOLD", 0.25))
        status["keep_resident"] = bool(getattr(config, "LAYA_KEEP_RESIDENT", False))
        status["log_path"] = str(getattr(config, "LAYA_LOG_PATH", ""))
    except Exception:
        pass
    return status
