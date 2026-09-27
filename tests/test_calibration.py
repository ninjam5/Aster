"""
Calibration tool Test Suite — Stage 4 (engine_testing/calibrate_system1.py).

Run: pytest tests/test_calibration.py -v
Pure math + JSONL parsing; no model, no network.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine_testing.calibrate_system1 import (
    _apply_temperature,
    calibrate,
    chosen_probability,
    ece,
    fit_temperature,
    labeled_pairs,
    load_rows,
    main,
    nll,
    summarize,
)


class TestLoadRows:
    def test_missing_file_is_empty_not_error(self, tmp_path):
        rows, bad = load_rows(str(tmp_path / "nope.jsonl"))
        assert rows == [] and bad == 0

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "log.jsonl"
        path.write_text(
            '{"kind": "element"}\nnot json\n\n[1, 2]\n{"kind": "state"}\n',
            encoding="utf-8",
        )
        rows, bad = load_rows(str(path))
        assert len(rows) == 2 and bad == 2


class TestProbabilityAndPairs:
    def test_chosen_probability(self):
        assert chosen_probability(
            {"choice": "A", "distribution": {"A": 0.7, "B": 0.3}}) == 0.7
        assert chosen_probability({"choice": "A"}) is None
        assert chosen_probability(
            {"choice": "A", "distribution": {"A": "x"}}) is None
        assert chosen_probability(
            {"choice": None, "distribution": {"A": 0.7}}) is None

    def test_semantic_choice_uses_raw_key(self):
        """Operation/state rows log semantic choice + raw key (QA round 2, F4)."""
        row = {"kind": "state", "key": "B", "choice": False,
               "distribution": {"A": 0.45, "B": 0.55}, "outcome": False}
        assert chosen_probability(row) == 0.55
        assert labeled_pairs([row]) == [(0.55, False)]

    def test_labeled_pairs_filters_non_outcomes(self):
        rows = [
            {"choice": "A", "distribution": {"A": 0.9, "B": 0.1}, "outcome": True},
            {"choice": "A", "distribution": {"A": 0.9, "B": 0.1}},          # unlabeled
            {"choice": "A", "distribution": {"A": 0.9, "B": 0.1}, "outcome": "yes"},
        ]
        assert labeled_pairs(rows) == [(0.9, True)]


class TestEce:
    def test_perfectly_calibrated_is_low(self):
        # Empirical rates exactly match the predicted probabilities per bin.
        pairs = ([(0.1, i < 10) for i in range(100)]
                 + [(0.9, i < 90) for i in range(100)])
        assert ece(pairs) < 0.001

    def test_overconfident_is_high(self):
        pairs = [(0.9, i % 2 == 0) for i in range(100)]  # 50% accuracy at p=0.9
        assert ece(pairs) > 0.3

    def test_empty_is_zero(self):
        assert ece([]) == 0.0


class TestTemperature:
    def test_fit_reduces_nll_and_ece(self):
        pairs = [(0.9, i % 10 < 6) for i in range(200)]  # 60% accuracy at p=0.9
        t = fit_temperature(pairs)
        assert t > 1.0, "overconfident data must need softening"
        recal = [(_apply_temperature(p, t), y) for p, y in pairs]
        assert nll(recal, 1.0) < nll(pairs, 1.0)
        assert ece(recal) < ece(pairs)

    def test_no_data_is_identity(self):
        assert fit_temperature([]) == 1.0


class TestSummarize:
    def test_counts_and_margins(self):
        rows = [
            {"kind": "element", "escalate": False, "margin": 0.7},
            {"kind": "element", "escalate": True, "margin": 0.05},
            {"kind": "state", "escalate": False, "margin": None},
        ]
        s = summarize(rows)
        assert s["element"]["count"] == 2 and s["element"]["escalated"] == 1
        assert s["element"]["escalation_rate"] == 0.5
        assert s["state"]["margin_mean"] is None


class TestCalibrate:
    def test_calibrate_end_to_end(self):
        rows = [
            {"kind": "element", "choice": "A", "distribution": {"A": 0.9, "B": 0.1},
             "outcome": i % 10 < 6, "margin": 0.8, "escalate": False}
            for i in range(200)
        ]
        report = calibrate(rows)
        info = report["kinds"]["element"]
        assert info["labeled"] == 200
        assert info["temperature"] > 1.0
        assert info["ece_after"] < info["ece_before"]


class TestCli:
    def test_selftest_passes(self):
        assert main(["--selftest"]) == 0

    def test_empty_log_is_not_an_error(self, tmp_path):
        assert main(["--log", str(tmp_path / "none.jsonl")]) == 0
