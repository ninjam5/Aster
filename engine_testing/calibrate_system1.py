"""Stage-4 calibration tool for the System-1 kernel decision log.

Reads the JSONL written by core/system1.py (config.LAYA_LOG_PATH) and:

  1. summarizes verdict volume by kind, escalation rate, margin stats;
  2. joins decisions with outcomes when rows carry `outcome: true|false`
     (filled by the caller after the deterministic verification of the next
     step) to compute accuracy and expected calibration error (ECE);
  3. fits a single temperature per question kind that minimizes NLL on the
     labeled rows (grid search, dependency-free) and reports ECE before/after.

Rows without `outcome` are counted but excluded from accuracy/calibration.
Missing log file is not an error — it prints a "nothing collected yet" note.

Usage (repo root):
  python engine_testing/calibrate_system1.py [--log PATH] [--json]
  python engine_testing/calibrate_system1.py --selftest
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def load_rows(path: str) -> tuple[list[dict], int]:
    """(rows, bad_line_count). Tolerates a missing file and malformed lines."""
    if not path or not os.path.exists(path):
        return [], 0
    rows, bad = [], 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
                else:
                    bad += 1
            except Exception:
                bad += 1
    return rows, bad


def chosen_probability(row: dict):
    """P(chosen option) from the row distribution, or None when unavailable.

    `key` is the raw model choice (A/B/...); `choice` may be semantic (an
    operation name, a boolean). Prefer the raw key when present.
    """
    dist = row.get("distribution")
    choice = row.get("key") or row.get("choice")
    if not isinstance(dist, dict) or not choice:
        return None
    try:
        p = float(dist.get(choice))
    except Exception:
        return None
    if not math.isfinite(p) or not (0.0 <= p <= 1.0):
        return None
    return p


def labeled_pairs(rows: list[dict]) -> list[tuple]:
    """[(p_chosen, correct_bool)] for rows with a boolean outcome + probability."""
    pairs = []
    for row in rows:
        outcome = row.get("outcome")
        if not isinstance(outcome, bool):
            continue
        p = chosen_probability(row)
        if p is None:
            continue
        pairs.append((p, outcome))
    return pairs


def ece(pairs: list[tuple], bins: int = 10) -> float:
    """Expected calibration error over (p, y) pairs."""
    if not pairs:
        return 0.0
    buckets: dict[int, list] = {}
    for p, y in pairs:
        idx = min(bins - 1, int(p * bins))
        buckets.setdefault(idx, []).append((p, y))
    total = len(pairs)
    err = 0.0
    for items in buckets.values():
        avg_p = sum(p for p, _ in items) / len(items)
        avg_y = sum(1 for _, y in items if y) / len(items)
        err += (len(items) / total) * abs(avg_p - avg_y)
    return err


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _apply_temperature(p: float, t: float) -> float:
    """Recalibrate a binary probability with temperature t (>0)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    logit = math.log(p / (1 - p))
    return _sigmoid(logit / max(t, 1e-3))


def nll(pairs: list[tuple], t: float) -> float:
    if not pairs:
        return 0.0
    total = 0.0
    for p, y in pairs:
        q = _apply_temperature(p, t)
        total -= math.log(q if y else (1 - q))
    return total / len(pairs)


def fit_temperature(pairs: list[tuple], lo: float = 0.25, hi: float = 4.0,
                    step: float = 0.05) -> float:
    """Grid-search the temperature minimizing NLL (1.0 = no change)."""
    if not pairs:
        return 1.0
    best_t, best = 1.0, nll(pairs, 1.0)
    t = lo
    while t <= hi + 1e-9:
        score = nll(pairs, t)
        if score < best:
            best_t, best = t, score
        t += step
    return round(best_t, 2)


def summarize(rows: list[dict]) -> dict:
    by_kind: dict[str, dict] = {}
    for row in rows:
        kind = str(row.get("kind") or "?")
        info = by_kind.setdefault(kind, {"count": 0, "escalated": 0,
                                         "margins": [], "labeled": 0})
        info["count"] += 1
        if row.get("escalate"):
            info["escalated"] += 1
        margin = row.get("margin")
        if isinstance(margin, (int, float)) and math.isfinite(margin):
            info["margins"].append(float(margin))
        if isinstance(row.get("outcome"), bool) and chosen_probability(row) is not None:
            info["labeled"] += 1
    out = {}
    for kind, info in sorted(by_kind.items()):
        margins = info.pop("margins")
        info["escalation_rate"] = round(info["escalated"] / info["count"], 3) if info["count"] else 0.0
        info["margin_mean"] = round(sum(margins) / len(margins), 3) if margins else None
        out[kind] = info
    return out


def calibrate(rows: list[dict]) -> dict:
    report = {"summary": summarize(rows), "kinds": {}}
    for kind in report["summary"]:
        kind_rows = [r for r in rows if str(r.get("kind") or "?") == kind]
        pairs = labeled_pairs(kind_rows)
        if not pairs:
            report["kinds"][kind] = {"labeled": 0}
            continue
        t = fit_temperature(pairs)
        recal = [(_apply_temperature(p, t), y) for p, y in pairs]
        acc = sum(1 for _, y in pairs if y) / len(pairs)
        report["kinds"][kind] = {
            "labeled": len(pairs),
            "accuracy": round(acc, 3),
            "temperature": t,
            "ece_before": round(ece(pairs), 3),
            "ece_after": round(ece(recal), 3),
            "nll_before": round(nll(pairs, 1.0), 3),
            "nll_after": round(nll(pairs, t), 3),
        }
    return report


def _selftest() -> int:
    """Synthetic overconfident rows: temperature fitting must lower ECE."""
    rows = []
    for i in range(400):
        correct = (i % 10) < 6  # 60% accuracy
        p = 0.9 if correct else 0.85
        rows.append({"kind": "element", "choice": "A",
                     "distribution": {"A": p, "B": 1 - p},
                     "margin": p - (1 - p), "escalate": False,
                     "outcome": correct})
    before = ece(labeled_pairs(rows))
    report = calibrate(rows)
    after = report["kinds"]["element"]["ece_after"]
    print(f"selftest: ECE {before:.3f} -> {after:.3f} "
          f"(T={report['kinds']['element']['temperature']})")
    ok = after < before and after < 0.10
    print("selftest PASS" if ok else "selftest FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=None, help="decision log JSONL path")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()

    path = args.log
    if not path:
        try:
            import config
            path = config.LAYA_LOG_PATH
        except Exception:
            path = os.path.join(os.path.dirname(HERE), "Aster_Vault", "system1_log.jsonl")

    rows, bad = load_rows(path)
    if not rows:
        print(f"No decisions collected yet at {path} — nothing to calibrate.")
        return 0
    report = calibrate(rows)
    report["log"] = path
    report["rows"] = len(rows)
    report["bad_lines"] = bad
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"Decision log: {path}  ({len(rows)} rows, {bad} bad)")
    for kind, info in report["summary"].items():
        print(f"  {kind:9s} n={info['count']:<5d} escalated={info['escalated']:<4d} "
              f"rate={info['escalation_rate']:.2f} margin_mean={info['margin_mean']}")
    for kind, info in report["kinds"].items():
        if not info.get("labeled"):
            print(f"  {kind}: no labeled outcomes yet — add 'outcome' fields to calibrate")
            continue
        print(f"  {kind}: acc={info['accuracy']:.3f} T={info['temperature']} "
              f"ECE {info['ece_before']:.3f} -> {info['ece_after']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
