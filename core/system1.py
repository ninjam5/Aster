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
_PREDICT_LOCK = threading.Lock()
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
        import config as _c
        if not bool(getattr(_c, "LAYA_KEEP_RESIDENT", False)):
            print("[System1] WARNING: laya_keep_resident is false — EVERY pass reloads "
                  "the checkpoint (~35 s cold). Set automation.laya_keep_resident: true "
                  "for any per-turn use (QA round 2).")
    except Exception:
        pass
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
    """Model call seam (mocked in tests). One forward pass over all questions.

    `agent.predict` is serialized under `_PREDICT_LOCK`: the kernel is now called from
    several threads (the LiveKit RTC loop, the awareness daemon, the sentry daemon, the
    brain thread) and they share one resident model instance. Laya is not documented as
    reentrant, and serializing costs only the ~0.31 s the pass takes anyway.
    """
    agent = acquire()
    try:
        with _PREDICT_LOCK:
            return agent.predict(state, questions)
    finally:
        release()


def reset_model():
    """Testing/diagnostics: drop the cached model and refcount.

    QA round 3: takes `_PREDICT_LOCK` first, so it can never pull the model out from
    under an in-flight `agent.predict` (the lock order matches `_predict`).
    """
    global _REFCOUNT
    with _PREDICT_LOCK:
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
            # QA 2026-09-28: the margin must be measured against the CHOSEN key, not the
            # distribution's top two. A response whose `choice` disagreed with its own
            # probabilities could otherwise pass the gate with a large "margin" on a key
            # that was not actually the argmax.
            if choice is not None:
                if choice not in distribution:
                    # QA round 2: the chosen key has no probability at all, so its
                    # support is unknown — another key's margin must not stand in for it.
                    candidate = None
                else:
                    chosen = float(distribution[choice])
                    others = [float(v) for k, v in distribution.items() if k != choice]
                    candidate = (chosen - max(others)) if others else None
            else:
                vals = sorted((float(v) for v in distribution.values()), reverse=True)
                candidate = vals[0] - vals[1]
            if candidate is not None and math.isfinite(candidate):
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


def _gate_escalates(margin, threshold: float = None) -> bool:
    """True when a margin is missing, non-finite, or below `threshold`.

    `threshold` overrides the global `LAYA_MARGIN_THRESHOLD` for one call — write-time
    and safety-critical decisions use a stricter value than the DOM motor's.
    """
    if threshold is None:
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
                state_text: str = "", min_margin: float = None) -> dict:
    """Neutral-key yes/no gate (deliberately NOT Laya `noul` — bug #156).

    `min_margin` overrides the global threshold for this call.
    Returns {"answer": bool | None, "margin", "escalate", ...}.
    """
    criteria = {"A": yes_description, "B": no_description}
    effective = _margin_threshold() if min_margin is None else float(min_margin)
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
    escalate = answer is None or _gate_escalates(margin, effective)
    if escalate and not reason:
        reason = "unrecognized answer" if answer is None else "low or unavailable margin"
    return _verdict("state", question, answer, margin, distribution, escalate,
                    reason, key=choice, answer=answer, criteria=criteria,
                    state_text=state_text, threshold=effective)


def _criteria_dict(criteria) -> dict:
    """Normalize caller-supplied options into {KEY: description} with neutral keys.

    Accepts a dict (keys preserved, values stringified) or a list/tuple of labels
    (keys A, B, C... in order). Laya itself accepts either shape; normalizing here
    means callers of `choose`/`score_candidates` never have to think about keys.
    """
    if isinstance(criteria, dict):
        return {str(k): ("" if v is None else str(v)) for k, v in criteria.items()}
    if isinstance(criteria, (list, tuple)):
        return {chr(ord("A") + i): str(c) for i, c in enumerate(criteria)}
    return {}


def choose(question: str, criteria, key: str = "choice", state: dict = None,
           kind: str = "choice", min_margin: float = None) -> dict:
    """Generic bounded choice over caller-supplied options (neutral keys).

    The workhorse for every non-DOM decision: pass the question and the option set,
    get back the picked option plus a margin-gated verdict. Use `check_state` for
    the common binary yes/no case; use this for >2 options or caller-defined keys.

    `criteria` is a dict {KEY: description} (keys preserved) or a list of labels
    (keys A..R in order). `key` is the question id in the Laya payload (only matters
    for debugging/log correlation). `state` adds extra text the model should see.
    `min_margin` overrides the global threshold for this call (write-time and
    safety-critical gates use a stricter value).

    Returns the standard verdict dict: {"choice", "margin", "distribution",
    "escalate", "reason", "keys", "criteria", "threshold", ...}. Escalation is
    caller-owned — never act on an `escalate=True` verdict without a fallback.
    """
    options = _criteria_dict(criteria)
    qid = str(key or "choice")
    effective = _margin_threshold() if min_margin is None else float(min_margin)
    if not options:
        return _verdict(kind, question, None, None, None, True, "empty criteria",
                        key=None, keys=[], criteria={}, threshold=effective)
    state_payload = {"question": question, "options": options}
    if state:
        state_payload.update(state)
    questions = {qid: {"type": "choice", "instructions": question, "criteria": options}}
    choice, margin, distribution, reason = _ask(state_payload, questions, qid, set(options))
    escalate = choice is None or _gate_escalates(margin, effective)
    if escalate and not reason:
        reason = "unrecognized choice" if choice is None else "low or unavailable margin"
    return _verdict(kind, question, choice, margin, distribution, escalate, reason,
                    key=choice, keys=list(options), criteria=options, threshold=effective)


def score_candidates(levels, instruction: str, candidates, state: dict = None,
                     min_confidence: float = 0.5, min_margin: float = None) -> dict:
    """Ordinal-score candidates on a caller-defined ordered scale (Laya `score`).

    IMPORTANT — Laya's `score` is NOT a per-candidate probability. Its answer is the
    **expected level index** over `levels`, so `levels` must run worst -> best
    (index 0 first), e.g. ["irrelevant", "background", "partly answers", "answers"].
    To rank N candidates we issue one score question per candidate and run them all
    in a SINGLE batched forward pass (that is what `_predict` is for).

    Returns {"scores": {id: raw_index}, "normalized": {id: 0..1}, "best": id|None,
    "margin": float|None, "answer_confidence": {id: float}, "escalate", "reason", ...}.

    Escalation: multiple candidates escalate when the normalized best-vs-runner-up
    margin is below the gate; a single candidate escalates when its calibrated
    `answer_confidence` is below `min_confidence` (there is no margin to compare).
    """
    lv = [str(x) for x in (levels or [])]
    cands = _criteria_dict(candidates)
    base = {"levels": lv, "scores": {}, "normalized": {}, "best": None,
            "answer_confidence": {}, "keys": list(cands)}
    if not lv:
        return _verdict("score", instruction, None, None, None, True, "empty levels", **base)
    if not cands:
        return _verdict("score", instruction, None, None, None, True, "empty candidates", **base)
    scale = max(1, len(lv) - 1)
    # QA round 3: record/apply the EFFECTIVE threshold. A single candidate is gated on
    # min_confidence, but the log used to claim the global margin threshold either way,
    # which corrupts the calibration curve.
    effective = _margin_threshold() if min_margin is None else float(min_margin)
    state_payload = {"question": instruction, "levels": lv}
    if state:
        state_payload.update(state)
    questions = {
        cid: {"type": "score",
              "instructions": f"{instruction} Candidate: {desc}",
              "criteria": lv}
        for cid, desc in cands.items()
    }
    try:
        result = _predict(state_payload, questions)
    except Exception as e:
        return _verdict("score", instruction, None, None, None, True,
                        f"kernel failure: {e.__class__.__name__}: {e}", **base)
    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        return _verdict("score", instruction, None, None, None, True,
                        "no answers in model response", **base)
    scores, normalized, conf = {}, {}, {}
    for cid in cands:
        answer = answers.get(cid)
        if not isinstance(answer, dict) or answer.get("score") is None:
            continue
        try:
            raw = float(answer["score"])
        except (TypeError, ValueError):
            continue
        scores[cid] = raw
        normalized[cid] = max(0.0, min(1.0, raw / scale))
        try:
            conf[cid] = float(answer.get("answer_confidence"))
        except (TypeError, ValueError):
            pass
    if not scores:
        return _verdict("score", instruction, None, None, None, True,
                        "no usable scores in model response", **base)
    order = sorted(scores, key=lambda c: scores[c], reverse=True)
    best = order[0]
    margin = None
    escalate, reason = False, ""
    if len(order) > 1:
        margin = normalized[best] - normalized[order[1]]
        if _gate_escalates(margin, effective):
            escalate, reason = True, "low or unavailable margin"
    else:
        c = conf.get(best)
        if c is not None and c < float(min_confidence):
            escalate = True
            reason = f"low answer confidence ({c:.2f} < {min_confidence})"
    return _verdict("score", instruction, best, margin, None, escalate, reason,
                    scores=scores, normalized=normalized, best=best, levels=lv,
                    answer_confidence=conf, keys=list(cands), threshold=effective)


def answer_margin(answer: dict) -> float | None:
    """top1-top2 margin of a RAW answer dict (choice/score probabilities).

    For callers that batch independent questions through `ask_batch` and must gate
    each answer themselves. QA round 3: measures against the CHOSEN key (like
    `_extract_choice`) — the old top-two form could borrow another key's margin.
    """
    if not isinstance(answer, dict):
        return None
    probs = answer.get("probabilities") or {}
    if not isinstance(probs, dict) or len(probs) < 2:
        return None
    choice = answer.get("choice")
    try:
        if choice is not None:
            if choice not in probs:
                return None
            chosen = float(probs[choice])
            others = [float(v) for k, v in probs.items() if k != choice]
            margin = chosen - max(others) if others else None
        else:
            vals = sorted((float(v) for v in probs.values()), reverse=True)
            margin = vals[0] - vals[1]
    except (TypeError, ValueError):
        return None
    return margin if margin is not None and math.isfinite(margin) else None


def ask_batch(questions: dict, state: dict = None) -> dict:
    """Run several INDEPENDENT questions in ONE forward pass.

    `questions` is the raw Laya payload: {qid: {"type": "choice"|"score"|"noul",
    "instructions": str, "criteria": ...}}. This is the batching seam — independent
    gates cost one model call, not N, which is what makes Laya viable per turn.

    Returns {"ok": bool, "answers": {qid: answer}, "error": str}. Callers own the
    margin gate per answer; for a single decision with gating use `choose`,
    `score_candidates` or `check_state` instead.
    """
    if not questions:
        return {"ok": False, "answers": {}, "error": "empty questions"}
    try:
        result = _predict(dict(state or {}), questions)
    except Exception as e:
        return {"ok": False, "answers": {},
                "error": f"kernel failure: {e.__class__.__name__}: {e}"}
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        return {"ok": False, "answers": {}, "error": "no answers in model response"}
    _log({"kind": "batch", "goal": "", "choice": None, "margin": None,
          "distribution": None, "escalate": False, "reason": "",
          "threshold": _margin_threshold(), "questions": list(questions),
          "answered": sorted(result["answers"])})
    return {"ok": True, "answers": result["answers"], "error": ""}


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
