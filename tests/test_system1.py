"""
System-1 kernel Test Suite — Stage 3 (core/system1.py, mocked Laya).

Per-feature test classes:
  Feature 1 — pick_element (neutral keys, margin, escalation)
  Feature 2 — pick_operation (bounded operation vocabulary)
  Feature 3 — check_state (neutral-key yes/no; NOT noul)
  Feature 4 — decision logging (calibration dataset)
  Feature 5 — model lifecycle (lazy, ref-counted, unload-on-zero)
  Feature 6 — DOM motor integration (kernel choice, disabled default)

Run: pytest tests/test_system1.py -v
No model download, no network: _predict is mocked with canned Laya-shaped dicts.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.system1 as system1


@pytest.fixture(autouse=True)
def _isolate_log(tmp_path):
    """Every verdict would append to the real decision log — isolate it."""
    with patch("config.LAYA_LOG_PATH", str(tmp_path / "s1.jsonl")):
        yield


def _laya_result(key, answer):
    return {"answers": {key: answer}}


SHORTLIST = [
    {"role": "button", "name": "Open", "text": "", "source": "web", "score": 0.9},
    {"role": "button", "name": "Open", "text": "", "source": "web", "score": 0.9},
    {"role": "link", "name": "Settings", "text": "", "source": "web", "score": 0.8},
]


# ============================================================================
# Feature 1 — pick_element
# ============================================================================

class TestPickElement:
    def test_high_margin_choice_wins(self):
        answer = {"choice": "B", "probabilities": {"A": 0.1, "B": 0.8, "C": 0.1}}
        with patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            v = system1.pick_element("open the second open button", SHORTLIST, op="click")
        assert v["index"] == 1 and v["escalate"] is False
        assert v["margin"] == pytest.approx(0.7)

    def test_low_margin_escalates_but_reports_choice(self):
        answer = {"choice": "A", "probabilities": {"A": 0.4, "B": 0.35, "C": 0.25}}
        with patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            v = system1.pick_element("open", SHORTLIST)
        assert v["index"] == 0 and v["escalate"] is True
        assert v["reason"] == "low or unavailable margin"

    def test_kernel_failure_escalates(self):
        with patch.object(system1, "_predict", side_effect=RuntimeError("no model")):
            v = system1.pick_element("open", SHORTLIST)
        assert v["index"] is None and v["escalate"] is True
        assert "kernel failure" in v["reason"]

    def test_empty_shortlist_escalates(self):
        v = system1.pick_element("open", [])
        assert v["escalate"] is True and v["reason"] == "empty shortlist"
        assert v["index"] is None

    def test_shortlist_capped_at_18(self):
        many = [{"role": "button", "name": f"n{i}"} for i in range(30)]
        seen = {}

        def fake_predict(state, questions):
            seen.update(questions["element"]["criteria"])
            return _laya_result("element", {"choice": "A",
                                            "probabilities": {"A": 0.9, "B": 0.05}})

        with patch.object(system1, "_predict", side_effect=fake_predict):
            system1.pick_element("x", many)
        assert len(seen) == 18

    def test_unrecognized_choice_escalates(self):
        answer = {"choice": "Z", "probabilities": {"A": 0.9, "B": 0.1}}
        with patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            v = system1.pick_element("x", SHORTLIST)
        assert v["escalate"] is True and v["index"] is None

    def test_criteria_carry_role_and_name(self):
        captured = {}

        def fake_predict(state, questions):
            captured.update(questions["element"]["criteria"])
            return _laya_result("element", {"choice": "C",
                                            "probabilities": {"A": 0.05, "B": 0.05, "C": 0.9}})

        with patch.object(system1, "_predict", side_effect=fake_predict):
            system1.pick_element("settings", SHORTLIST)
        assert captured["C"] == "link: Settings"
        assert captured["A"] == "button: Open"


# ============================================================================
# Feature 2 — pick_operation
# ============================================================================

class TestPickOperation:
    def test_operation_mapping(self):
        answer = {"choice": "B", "probabilities": {"A": 0.05, "B": 0.9}}
        with patch.object(system1, "_predict", return_value=_laya_result("operation", answer)):
            v = system1.pick_operation("fill the search box")
        assert v["choice"] == "type" and v["escalate"] is False
        assert v["key"] == "B", "raw distribution key must be logged for calibration"

    def test_blocked_is_a_valid_decision(self):
        answer = {"choice": "G", "probabilities": {"F": 0.1, "G": 0.85}}
        with patch.object(system1, "_predict", return_value=_laya_result("operation", answer)):
            v = system1.pick_operation("delete every email")
        assert v["choice"] == "blocked" and v["escalate"] is False


# ============================================================================
# Feature 3 — check_state (neutral keys, never noul)
# ============================================================================

class TestCheckState:
    def test_yes_answer_uses_neutral_keys(self):
        answer = {"choice": "A", "probabilities": {"A": 0.95, "B": 0.05}}
        captured = {}

        def fake_predict(state, questions):
            captured.update(questions["check"])
            return _laya_result("check", answer)

        with patch.object(system1, "_predict", side_effect=fake_predict):
            v = system1.check_state("Is the chat ready?", "yes, ready", "no, loading")
        assert v["answer"] is True and v["escalate"] is False
        assert v["key"] == "A", "raw distribution key must be logged for calibration"
        assert captured["type"] == "choice"
        assert captured["criteria"] == {"A": "yes, ready", "B": "no, loading"}

    def test_no_answer(self):
        answer = {"choice": "B", "probabilities": {"A": 0.1, "B": 0.9}}
        with patch.object(system1, "_predict", return_value=_laya_result("check", answer)):
            v = system1.check_state("Q", "yes", "no")
        assert v["answer"] is False and v["escalate"] is False

    def test_low_margin_answer_escalates(self):
        answer = {"choice": "B", "probabilities": {"A": 0.45, "B": 0.55}}
        with patch.object(system1, "_predict", return_value=_laya_result("check", answer)):
            v = system1.check_state("Q", "yes", "no")
        assert v["answer"] is False and v["escalate"] is True

    def test_missing_answer_escalates(self):
        with patch.object(system1, "_predict", return_value={"answers": {}}):
            v = system1.check_state("Q", "yes", "no")
        assert v["answer"] is None and v["escalate"] is True


# ============================================================================
# Feature 4 — decision logging
# ============================================================================

class TestDecisionLogging:
    def test_verdict_logged_with_full_distribution(self, tmp_path):
        log = tmp_path / "s1.jsonl"
        answer = {"choice": "A", "probabilities": {"A": 0.9, "B": 0.1}}
        with patch("config.LAYA_LOG_PATH", str(log)), \
             patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            system1.pick_element("x", SHORTLIST)
        entries = [json.loads(line) for line in
                   log.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 1
        e = entries[0]
        assert e["kind"] == "element" and e["choice"] == "A"
        assert e["distribution"] == {"A": 0.9, "B": 0.1}
        assert e["margin"] == pytest.approx(0.8)
        assert e["escalate"] is False and "ts" in e

    def test_log_failure_never_raises(self):
        with patch("config.LAYA_LOG_PATH", "\0invalid\0path"), \
             patch.object(system1, "_predict",
                          return_value=_laya_result("element",
                                                    {"choice": "A",
                                                     "probabilities": {"A": 0.9, "B": 0.1}})):
            v = system1.pick_element("x", SHORTLIST)
        assert v["index"] == 0  # decision still returned


# ============================================================================
# Feature 5 — model lifecycle
# ============================================================================

class TestLifecycle:
    def setup_method(self):
        system1.reset_model()

    def teardown_method(self):
        system1.reset_model()

    def test_refcounted_acquire_release(self):
        fake = object()
        with patch.object(system1, "_load_model", return_value=fake) as loader, \
             patch("config.LAYA_KEEP_RESIDENT", False):
            assert system1.acquire() is fake
            assert system1.acquire() is fake
            assert loader.call_count == 1
            system1.release()
            assert system1._MODEL is fake, "one holder left"
            system1.release()
            assert system1._MODEL is None, "unload at zero refs"

    def test_keep_resident_skips_unload(self):
        fake = object()
        with patch.object(system1, "_load_model", return_value=fake), \
             patch("config.LAYA_KEEP_RESIDENT", True):
            system1.acquire()
            system1.release()
            assert system1._MODEL is fake

    def test_release_frees_memory_when_not_resident(self):
        with patch.object(system1, "_free_model_memory") as free, \
             patch("config.LAYA_KEEP_RESIDENT", False):
            system1._MODEL = object()
            system1._REFCOUNT = 1
            system1.release()
        assert free.called and system1._REFCOUNT == 0

    def test_kernel_enabled_reads_config_lazily(self):
        with patch("config.USE_LAYA_KERNEL", True):
            assert system1.kernel_enabled() is True
        with patch("config.USE_LAYA_KERNEL", False):
            assert system1.kernel_enabled() is False

    def test_status_never_loads_model(self):
        s = system1.kernel_status()
        assert {"enabled", "loaded", "refcount", "model", "log_path"} <= set(s)
        assert s["loaded"] is False


# ============================================================================
# Feature 6 — DOM motor integration
# ============================================================================

class TestDomIntegration:
    def test_web_click_uses_kernel_choice(self):
        import tools.dom as dom

        page = MagicMock()
        page.aria_snapshot.return_value = ('- button "Open" [ref=a]\n'
                                           '- button "Open" [ref=b]')
        page.url = "https://x.test/"
        chosen = {"role": "button", "name": "Open", "text": "", "ref": "b",
                  "bbox": None, "type": None, "source": "web", "score": 0.9}
        with patch.object(dom, "_kernel_pick", return_value=chosen) as kp, \
             patch.object(dom, "resolve_locator",
                          return_value=(MagicMock(), False)) as resolve_mock:
            out = dom._web_click_on_page(page, "Open button")
        assert out.startswith("Clicked")
        assert kp.called
        node_arg = resolve_mock.call_args[0][1]
        assert node_arg is chosen

    def test_kernel_disabled_keeps_top_match(self):
        import tools.dom as dom

        page = MagicMock()
        page.aria_snapshot.return_value = '- button "Open" [ref=a]'
        page.url = "https://x.test/"
        with patch("config.USE_LAYA_KERNEL", False), \
             patch("core.system1.pick_element") as pick, \
             patch.object(dom, "resolve_locator",
                          return_value=(MagicMock(), False)) as resolve_mock:
            out = dom._web_click_on_page(page, "Open button")
        assert out.startswith("Clicked")
        assert not pick.called, "disabled kernel must never consult Laya"
        node = resolve_mock.call_args[0][1]
        assert node["name"] == "Open", "top lexical match must still be used"

    def test_windows_kernel_choice_flows_into_coords(self):
        import tools.dom as dom

        cands = [
            {"name": "Open", "type": "ButtonControl", "rect": (0, 0, 20, 20)},
            {"name": "Open", "type": "ButtonControl", "rect": (200, 200, 220, 220)},
        ]
        second = {"role": "button", "name": "Open", "text": "", "ref": None,
                  "bbox": (200, 200, 220, 220), "type": "ButtonControl",
                  "source": "win", "score": 0.9}
        with patch("tools.uia.uia_nodes", return_value=(cands, (1920, 1080))), \
             patch("pyautogui.size", return_value=(1920, 1080)), \
             patch.object(dom, "_kernel_pick", return_value=second) as kp:
            result, _raw, _desk = dom.windows_locate_cached("Open", op="type")
        assert result is not None and result["x"] == 210
        assert kp.call_args[0][2] == "type", "op must be forwarded to the kernel"


# ============================================================================
# Feature 7 — hardening (QA round 1 findings)
# ============================================================================

class TestHardening:
    def test_confidence_only_binary_maps_to_margin(self):
        """0.55 confidence on a binary decision == 0.10 margin -> escalate."""
        with patch.object(system1, "_predict", return_value=_laya_result(
                "check", {"choice": "A", "confidence": 0.55})):
            v = system1.check_state("Q", "yes", "no")
        assert v["escalate"] is True
        assert v["margin"] == pytest.approx(0.10)

    def test_confidence_only_multichoice_escalates(self):
        with patch.object(system1, "_predict", return_value=_laya_result(
                "element", {"choice": "A", "confidence": 0.9})):
            v = system1.pick_element("x", SHORTLIST)
        assert v["escalate"] is True and v["margin"] is None

    def test_nan_margin_escalates(self):
        answer = {"choice": "A", "probabilities": {"A": float("nan"), "B": 0.1}}
        with patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            v = system1.pick_element("x", SHORTLIST)
        assert v["escalate"] is True

    def test_garbage_shortlist_never_raises(self):
        answer = {"choice": "A", "probabilities": {"A": 0.9, "B": 0.1}}
        with patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            for bad in ([None], ["button", 5], 5, None):
                assert isinstance(system1.pick_element("x", bad), dict)
            v = system1.pick_element("x", [None, 5])
        assert v["criteria"]["A"] == "?: None"

    def test_unhashable_choice_escalates(self):
        answer = {"choice": ["A"], "probabilities": {"A": 0.9, "B": 0.1}}
        with patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            v = system1.pick_element("x", SHORTLIST)
        assert v["escalate"] is True and v["index"] is None

    def test_malformed_model_response_escalates(self):
        with patch.object(system1, "_predict", return_value=["not", "a", "dict"]):
            assert system1.pick_element("x", SHORTLIST)["escalate"] is True
            assert system1.check_state("q", "y", "n")["escalate"] is True

    def test_concurrent_logging_stays_parseable(self, tmp_path):
        import threading

        log = tmp_path / "concurrent.jsonl"
        answer = {"choice": "A", "probabilities": {"A": 0.9, "B": 0.1}}
        with patch("config.LAYA_LOG_PATH", str(log)), \
             patch.object(system1, "_predict", return_value=_laya_result("element", answer)):
            def worker():
                for _ in range(25):
                    system1.pick_element("x", SHORTLIST)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        lines = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 200
        for line in lines:
            json.loads(line)  # every appended line must parse
