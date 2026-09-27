"""Standalone mock harness for the System-1 kernel (Stage 3, no Laya install).

Same shape as emotion-test.py / dom-motor-test.py: drive the real
core/system1.py verdict logic with canned Laya-shaped responses.
Run: python system1-test.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.system1 as system1  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"[PASS] {name}")
    else:
        _FAIL += 1
        print(f"[FAIL] {name}")


SHORTLIST = [
    {"role": "button", "name": "Open", "text": ""},
    {"role": "button", "name": "Open", "text": ""},
    {"role": "link", "name": "Settings", "text": ""},
]


def _result(key, answer):
    return {"answers": {key: answer}}


def main():
    # ── isolate the decision log ────────────────────────────────────────────
    tmp_log = os.path.join(tempfile.gettempdir(), "aster_system1_harness.jsonl")
    try:
        os.remove(tmp_log)
    except OSError:
        pass
    import config
    config.LAYA_LOG_PATH = tmp_log
    # Pin the margin threshold: the checks below assert default-threshold
    # behavior, independent of what the owner tuned in self_config.yaml.
    config.LAYA_MARGIN_THRESHOLD = 0.25

    # ── pick_element ───────────────────────────────────────────────────────
    system1._predict = lambda state, q: _result(
        "element", {"choice": "B", "probabilities": {"A": 0.1, "B": 0.8, "C": 0.1}})
    v = system1.pick_element("second open", SHORTLIST, op="click")
    check("high margin picks index B", v["index"] == 1 and v["escalate"] is False)
    check("margin is top1-top2", abs(v["margin"] - 0.7) < 1e-9)
    check("criteria are role+name", "button: Open" in v["criteria"].values())

    system1._predict = lambda state, q: _result(
        "element", {"choice": "A", "probabilities": {"A": 0.40, "B": 0.35, "C": 0.25}})
    v = system1.pick_element("open", SHORTLIST)
    check("low margin escalates",
          v["escalate"] is True and v["reason"] == "low or unavailable margin")

    system1._predict = lambda state, q: _result(
        "element", {"choice": "A", "probabilities": {"A": 0.9, "B": 0.05}})
    v = system1.pick_element("x", [])
    check("empty shortlist escalates", v["escalate"] is True)

    def _boom(state, q):
        raise RuntimeError("no model")
    system1._predict = _boom
    v = system1.pick_element("x", SHORTLIST)
    check("kernel failure escalates, never raises",
          v["escalate"] is True and "kernel failure" in v["reason"])

    # ── pick_operation ─────────────────────────────────────────────────────
    system1._predict = lambda state, q: _result(
        "operation", {"choice": "B", "probabilities": {"A": 0.05, "B": 0.9}})
    v = system1.pick_operation("fill the search box")
    check("operation B maps to type", v["choice"] == "type" and v["escalate"] is False)

    system1._predict = lambda state, q: _result(
        "operation", {"choice": "G", "probabilities": {"F": 0.1, "G": 0.85}})
    v = system1.pick_operation("delete everything")
    check("blocked is a valid decision", v["choice"] == "blocked" and v["escalate"] is False)

    # ── check_state (neutral keys) ─────────────────────────────────────────
    captured = {}

    def _capture(state, q):
        captured.update(q["check"])
        return _result("check", {"choice": "A", "probabilities": {"A": 0.95, "B": 0.05}})
    system1._predict = _capture
    v = system1.check_state("Is the chat ready?", "yes, ready", "no, loading")
    check("check_state returns True for A", v["answer"] is True and v["escalate"] is False)
    check("check_state uses choice with neutral keys",
          captured.get("type") == "choice" and set(captured.get("criteria")) == {"A", "B"})

    system1._predict = lambda state, q: _result(
        "check", {"choice": "B", "probabilities": {"A": 0.45, "B": 0.55}})
    v = system1.check_state("Q", "yes", "no")
    check("low-margin check escalates", v["answer"] is False and v["escalate"] is True)

    # ── logging ────────────────────────────────────────────────────────────
    with open(tmp_log, "w", encoding="utf-8"):
        pass  # clear decisions logged by the checks above
    system1._predict = lambda state, q: _result(
        "element", {"choice": "A", "probabilities": {"A": 0.9, "B": 0.1}})
    system1.pick_element("x", SHORTLIST)
    try:
        with open(tmp_log, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        check("decision log has one entry", len(entries) == 1)
        check("log carries full distribution", entries[0]["distribution"] == {"A": 0.9, "B": 0.1})
        check("log carries kind+margin", entries[0]["kind"] == "element" and "margin" in entries[0])
    except Exception:
        check("decision log readable", False)

    # ── lifecycle ──────────────────────────────────────────────────────────
    system1.reset_model()
    fake = object()
    original_load = system1._load_model
    system1._load_model = lambda: fake
    config.LAYA_KEEP_RESIDENT = False
    check("acquire loads once", system1.acquire() is fake and system1.acquire() is fake)
    system1.release()
    check("one holder keeps model", system1._MODEL is fake)
    system1.release()
    check("unloads at zero refs", system1._MODEL is None)
    system1._load_model = original_load
    system1.reset_model()

    check("status shape", set(system1.kernel_status()) >= {"enabled", "loaded", "refcount"})

    total = _PASS + _FAIL
    print(f"\n{_PASS}/{total} checks passed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
