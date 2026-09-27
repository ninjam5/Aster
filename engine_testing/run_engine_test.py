#!/usr/bin/env python3
"""
Aster Engine Test Runner

Runs a comprehensive multi-category test suite against whatever model
llama-server is currently serving and writes a timestamped report to
engine_testing/results/.

Usage:
    python engine_testing/run_engine_test.py
    python engine_testing/run_engine_test.py --model Qwen3.6-35B-A3B-UD-IQ4_XS
    python engine_testing/run_engine_test.py --category persona
    python engine_testing/run_engine_test.py --no-vision

After running, swap the GGUF in start.bat, re-run, and diff the two report files.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import sys
import time

# ── Path setup ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT       = os.path.dirname(SCRIPT_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# ── Local imports (harness and scenarios live alongside this file) ─────────────
from harness import (
    get_server_props, run_scenario, aggregate_metrics,
    check_single_tool, check_no_tool, check_tool_sequence,
    check_no_hallucination, check_cutoff_routing,
    check_persona, check_discord_relay, check_vision, check_loop_discipline,
    SYSTEM_PROMPT,
)
from scenarios import SCENARIOS

ASSETS_DIR = os.path.join(SCRIPT_DIR, "assets")
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")

CATEGORY_LABELS = {
    "single_tool":    "Single tool selection",
    "arg_precision":  "Argument extraction precision",
    "multi_step":     "Multi-step reasoning",
    "restraint":      "Restraint (no-tool)",
    "hallucination":  "Hallucination resistance",
    "cutoff_routing": "Knowledge-cutoff routing",
    "persona":        "Persona adherence",
    "two_face":       "Two-Face protocol",
    "discord_relay":  "Discord relay formatting",
    "vision":         "Multimodal / vision",
}


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def load_or_capture_screenshot() -> str | None:
    """
    Load assets/sample_screen.png as base64 JPEG.
    If the file doesn't exist, capture the current screen and save it
    so subsequent runs use the same image (reproducible comparisons).
    """
    png_path = os.path.join(ASSETS_DIR, "sample_screen.png")
    os.makedirs(ASSETS_DIR, exist_ok=True)

    if os.path.exists(png_path):
        try:
            import cv2
            img = cv2.imread(png_path)
            if img is not None:
                _, buf = cv2.imencode(".jpg", img)
                print(f"[Vision] Loaded existing screenshot from {png_path}")
                return base64.b64encode(buf).decode("utf-8")
        except Exception as exc:
            print(f"[Vision] Could not load {png_path}: {exc}")

    # Capture live screen
    try:
        from tools.vision import capture_screen_base64
        b64 = capture_screen_base64()
        if b64:
            # Save for reproducibility
            import cv2, numpy as np
            data = base64.b64decode(b64)
            arr  = np.frombuffer(data, dtype=np.uint8)
            img  = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is not None:
                cv2.imwrite(png_path, img)
                print(f"[Vision] Captured and saved screenshot → {png_path}")
            return b64
    except Exception as exc:
        print(f"[Vision] Screen capture failed: {exc}")

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-CHECK DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

def run_auto_checks(scenario: dict, result: dict) -> dict[str, dict]:
    """Run all applicable auto-checks for a scenario. Returns {check_name: verdict}."""
    cat      = scenario.get("category", "")
    verdicts: dict[str, dict] = {}

    if cat == "single_tool":
        expected_tool = scenario.get("expected_tool")
        if expected_tool:
            verdicts["tool_match"] = check_single_tool(
                result, expected_tool, scenario.get("expected_args")
            )

    elif cat == "arg_precision":
        expected_tool = scenario.get("expected_tool")
        if expected_tool:
            verdicts["tool_and_args"] = check_single_tool(
                result, expected_tool, scenario.get("expected_args"),
                strict_first=not scenario.get("allow_prereq", False),
            )

    elif cat == "multi_step":
        seq = scenario.get("expected_sequence", [])
        if seq:
            verdicts["tool_sequence"] = check_tool_sequence(result, seq)

    elif cat == "restraint":
        verdicts["no_tool"] = check_no_tool(result)

    elif cat == "hallucination":
        verdicts["no_hallucination"] = check_no_hallucination(result, scenario["prompt"])
        # Also check the correct tool was eventually called
        expected_tool = scenario.get("expected_tool")
        if expected_tool:
            verdicts["correct_tool"] = check_single_tool(result, expected_tool)

    elif cat == "cutoff_routing":
        verdicts["cutoff_routing"] = check_cutoff_routing(result)

    elif cat == "loop_trap":
        verdicts["loop_discipline"] = check_loop_discipline(result)

    elif cat == "persona":
        verdicts["persona_heuristic"] = check_persona(result, max_sentences=2)

    elif cat == "two_face":
        mode = scenario.get("mode", "butler")
        max_s = 2 if mode == "butler" else 999
        verdicts["two_face"] = check_persona(result, max_sentences=max_s)

    elif cat == "discord_relay":
        verdicts["discord_relay"] = check_discord_relay(result)

    elif cat == "vision":
        verdicts["vision_response"] = check_vision(result)

    return verdicts


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

def format_transcript(transcript: list[dict], max_chars: int = 900) -> str:
    lines = []
    for entry in transcript:
        role    = entry["role"].upper()
        content = str(entry.get("content", ""))
        if len(content) > max_chars:
            content = content[:max_chars] + "... [truncated]"
        lines.append(f"    [{role}] {content}")
    return "\n".join(lines)


def build_report(
    entries:          list[dict],
    model_name:       str,
    ctx_size:         int | str,
    agg:              dict,
    wall_elapsed:     float,
    total_text_leaks: int,
    total_json_errors:int,
    total_api_calls:  int,
) -> str:
    text_leak_rate = (total_text_leaks / max(total_api_calls, 1)) * 100
    json_err_rate  = (total_json_errors / max(total_api_calls, 1)) * 100

    # Build per-category scorecard
    cat_scores: dict[str, list] = {}
    manual_entries: list[dict]  = []

    for entry in entries:
        if "skipped" in entry or "error" in entry:
            continue
        cat = entry["scenario"].get("category", "?")
        cat_scores.setdefault(cat, []).append(entry.get("overall"))
        if any(v.get("manual") for v in entry.get("verdicts", {}).values()):
            manual_entries.append(entry)

    lines: list[str] = []

    # ── Header ─────────────────────────────────────────────────────────────────
    lines += [
        "=" * 72,
        "  ASTER ENGINE TEST REPORT",
        "=" * 72,
        f"  Model:     {model_name}",
        f"  Context:   {ctx_size}",
        f"  Timestamp: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"  Server:    localhost:8080",
        f"  Tool path: NATIVE (tools= param, role:tool feedback)",
        "",
    ]

    # ── Efficiency ─────────────────────────────────────────────────────────────
    lines += [
        "── EFFICIENCY ──────────────────────────────────────────────────────────",
        f"  Avg generation speed:       {agg['avg_gen_tok_s']:.1f} tok/s",
        f"  Avg prompt processing:      {agg['avg_prompt_tok_s']:.1f} tok/s  (0 = not reported by server)",
        f"  Avg latency per turn:       {agg['avg_latency_s']:.2f}s",
        f"  Total wall time:            {wall_elapsed:.1f}s",
        f"  Total generation tokens:    {agg['total_gen_tokens']}",
        f"  Total prompt tokens:        {agg['total_prompt_tokens']}",
        f"  Text tool-syntax leak rate: {text_leak_rate:.1f}%  ({total_text_leaks}/{total_api_calls} API calls)",
        f"  JSON decode failure rate:   {json_err_rate:.1f}%  ({total_json_errors}/{total_api_calls} API calls)  (native args)",
        "",
    ]

    # ── Scorecard ──────────────────────────────────────────────────────────────
    lines.append("── SCORECARD ───────────────────────────────────────────────────────────")
    total_pass     = 0
    total_definite = 0

    for cat, results in cat_scores.items():
        definite     = [r for r in results if r is not None]
        manual_count = len(results) - len(definite)
        passed       = sum(1 for r in definite if r)
        label        = CATEGORY_LABELS.get(cat, cat)
        suffix       = f"  (+{manual_count} manual review)" if manual_count else ""
        lines.append(f"  {label:<36} {passed}/{len(definite)} auto{suffix}")
        total_pass     += passed
        total_definite += len(definite)

    pct = (total_pass / total_definite * 100) if total_definite else 0
    lines += [
        "",
        f"  AUTO-SCORED TOTAL: {total_pass}/{total_definite}  ({pct:.0f}%)",
        "",
    ]

    # ── Detailed Results ───────────────────────────────────────────────────────
    lines.append("── DETAILED RESULTS ────────────────────────────────────────────────────")

    for entry in entries:
        scenario = entry["scenario"]
        sid      = scenario["id"]
        cat      = scenario.get("category", "?")
        prompt   = scenario["prompt"]
        mode_tag = f" [{scenario['mode'].upper()}]" if "mode" in scenario else ""

        lines += ["", f"┌─ {sid}  [{cat.upper()}{mode_tag}]"]
        lines.append(f"│  Prompt: {prompt}")

        if "skipped" in entry:
            lines += ["│  STATUS: SKIPPED (no screenshot)", "└" + "─" * 71]
            continue

        if "error" in entry:
            lines += [f"│  STATUS: ERROR — {entry['error']}", "└" + "─" * 71]
            continue

        result   = entry["result"]
        verdicts = entry.get("verdicts", {})
        overall  = entry.get("overall")

        status_str = "PASS" if overall is True else ("FAIL" if overall is False else "MANUAL-ONLY")
        lines.append(f"│  STATUS:       {status_str}")
        lines.append(f"│  Tools called: {result['tool_calls']}")
        lines.append(f"│  Rounds used:  {result['rounds_used']}")

        for idx, m in enumerate(result["all_metrics"]):
            lines.append(
                f"│  Round {idx+1}: {m['gen_tok_per_s']:.1f} tok/s  |  "
                f"{m['elapsed_s']:.2f}s  |  {m['completion_tokens']} out-tokens"
            )

        if result["text_leak_count"]:
            lines.append(f"│  ⚠  Text tool-syntax leaks: {result['text_leak_count']} occurrence(s)")
        if result["json_error_count"]:
            lines.append(f"│  ⚠  JSON decode errors: {result['json_error_count']}")
        if result.get("empty_final"):
            lines.append(f"│  ⚠  Empty final response after tool execution (apathy signal)")

        for check_name, verdict in verdicts.items():
            flag = "✓" if verdict["passed"] else ("⚠ MANUAL" if verdict.get("manual") else "✗")
            lines.append(f"│  [{flag}] {check_name}: {verdict['notes']}")

        lines.append("│  TRANSCRIPT:")
        lines.append(format_transcript(result["transcript"]))
        lines.append("└" + "─" * 71)

    # ── Manual Review Queue ────────────────────────────────────────────────────
    lines += [
        "",
        "── MANUAL REVIEW QUEUE ─────────────────────────────────────────────────",
        "  Grade the items below and return this section to Claude for a summary.",
        "  Grading scale: ✓ (correct/good) / ~ (partial/borderline) / ✗ (fail)",
        "",
    ]

    GRADE_HINTS = {
        "persona_heuristic": "Persona: correct honorific (Sir/Boss)? Dry butler wit? ≤2 sentences? Zero slang?",
        "two_face":          "Two-Face: butler mode ≤2 sentences, or technical mode dense+accurate with no filler?",
        "discord_relay":     "Discord: third-person phrasing? Boss-attributed? Correct recipient? Butler tone?",
        "vision_response":   "Vision: does the description accurately reflect the screenshot content?",
    }

    for entry in manual_entries:
        scenario = entry["scenario"]
        result   = entry["result"]
        sid      = scenario["id"]
        cat      = scenario.get("category", "?")
        mode_tag = f" [{scenario['mode'].upper()}]" if "mode" in scenario else ""

        manual_checks = {k: v for k, v in entry["verdicts"].items() if v.get("manual")}
        if not manual_checks:
            continue

        resp = (result["final_response"] or "").strip()
        if len(resp) > 700:
            resp = resp[:700] + "... [truncated]"

        lines.append(f"  ┌─ {sid}  [{cat.upper()}{mode_tag}]")
        lines.append(f"  │  Prompt:   {scenario['prompt']}")
        lines.append(f"  │  Response: {resp}")
        for check_name, verdict in manual_checks.items():
            hint = GRADE_HINTS.get(check_name, "Grade overall quality.")
            auto_note = verdict["notes"]
            lines.append(f"  │  [{check_name}]")
            lines.append(f"  │    Auto-flags:   {auto_note}")
            lines.append(f"  │    Grading hint: {hint}")
            lines.append(f"  │    Your grade:   ___")
        lines.append("  └" + "─" * 69)

    lines += ["", "END OF REPORT", ""]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

_PRESETS = {
    # Control: what config.py sends today — QAT models prefer this.
    "baseline":        {"temperature": 1.0,  "top_p": 0.95, "top_k": 64, "repeat_penalty": None,  "min_p": None},
    # Best gogi speech quality from conversation_testing sweep.
    "tight":           {"temperature": 0.7,  "top_p": 0.9,  "top_k": 40, "repeat_penalty": 1.15,  "min_p": 0.05},
    # Middle-ground variant — slightly looser than tight.
    "recommended":     {"temperature": 0.75, "top_p": 0.95, "top_k": 64, "repeat_penalty": 1.1,   "min_p": 0.05},
    # Isolates repeat_penalty only; useful to check if that alone fixes style.
    "repeat_only":     {"temperature": 1.0,  "top_p": 0.95, "top_k": 64, "repeat_penalty": 1.1,   "min_p": None},
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Aster Engine Test Suite")
    parser.add_argument("--model",    default=None, help="Override model display name in report")
    parser.add_argument("--category", default=None, help="Run only one category (e.g. 'persona')")
    parser.add_argument("--no-vision", action="store_true", help="Skip vision tests")
    parser.add_argument("--preset", default=None,
                        choices=list(_PRESETS),
                        help="Apply a named sampler preset (sets all 5 knobs at once). "
                             "Individual flags override preset values.")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Override sampling temperature (e.g. 1.0 for QAT models)")
    parser.add_argument("--top-p", type=float, default=None,
                        help="Override top-p sampling (e.g. 0.95 for QAT models)")
    parser.add_argument("--top-k", type=int, default=None,
                        help="Override top-k sampling (e.g. 64 for QAT models)")
    parser.add_argument("--repeat-penalty", type=float, default=None,
                        help="Override repeat_penalty (e.g. 1.15 for the 'tight' preset)")
    parser.add_argument("--min-p", type=float, default=None,
                        help="Override min_p token sampling floor (e.g. 0.05)")
    args = parser.parse_args()

    # Apply sampling overrides to harness globals — preset first, then individual
    # flags so explicit flags always win over the preset.
    import harness as _harness
    if args.preset:
        p = _PRESETS[args.preset]
        _harness.TEMPERATURE    = p["temperature"]
        _harness.TOP_P          = p["top_p"]
        _harness.TOP_K          = p["top_k"]
        _harness.REPEAT_PENALTY = p["repeat_penalty"]
        _harness.MIN_P          = p["min_p"]
    if args.temperature is not None:
        _harness.TEMPERATURE = args.temperature
    if args.top_p is not None:
        _harness.TOP_P = args.top_p
    if args.top_k is not None:
        _harness.TOP_K = args.top_k
    if args.repeat_penalty is not None:
        _harness.REPEAT_PENALTY = args.repeat_penalty
    if args.min_p is not None:
        _harness.MIN_P = args.min_p

    print("=" * 72)
    print("  ASTER ENGINE TEST SUITE")
    print("=" * 72)

    # Probe server
    props      = get_server_props()
    gen_props  = props.get("default_generation_settings", {})
    model_path = gen_props.get("model", "unknown")
    ctx_size   = gen_props.get("n_ctx", "?")
    model_name = args.model or os.path.basename(model_path).replace(".gguf", "") or "unknown-model"

    print(f"  Model:   {model_name}")
    print(f"  Context: {ctx_size}")
    print(f"  Server:  localhost:8080")
    print(
        f"  Sampling: temp={_harness.TEMPERATURE}"
        f"  top_p={_harness.TOP_P if _harness.TOP_P is not None else 'server default'}"
        f"  top_k={_harness.TOP_K if _harness.TOP_K is not None else 'server default'}"
        f"  repeat_penalty={_harness.REPEAT_PENALTY if _harness.REPEAT_PENALTY is not None else 'server default'}"
        f"  min_p={_harness.MIN_P if _harness.MIN_P is not None else 'server default'}"
    )
    print(f"  System prompt length: {len(SYSTEM_PROMPT):,} chars")
    print("=" * 72)

    # Vision asset
    vision_b64: str | None = None
    if not args.no_vision:
        vision_b64 = load_or_capture_screenshot()
        if vision_b64:
            print(f"  Vision asset: {len(vision_b64) // 1024} KB base64")
        else:
            print("  WARNING: No screenshot — vision tests will be skipped.")
    else:
        print("  Vision tests: DISABLED (--no-vision)")

    # Filter scenarios
    scenarios_to_run = list(SCENARIOS)
    if args.category:
        scenarios_to_run = [s for s in scenarios_to_run if s.get("category") == args.category]
        print(f"  Category filter: '{args.category}' → {len(scenarios_to_run)} scenario(s)")
    print()

    # ── Run ───────────────────────────────────────────────────────────────────
    entries:           list[dict] = []
    all_metrics_lists: list[list[dict]] = []
    total_text_leaks   = 0
    total_json_errors  = 0
    total_api_calls    = 0

    wall_start = time.perf_counter()

    for i, scenario in enumerate(scenarios_to_run):
        sid    = scenario["id"]
        cat    = scenario.get("category", "?")
        prompt = scenario["prompt"]
        mode   = f" [{scenario['mode'].upper()}]" if "mode" in scenario else ""

        print(f"[{i+1:02d}/{len(scenarios_to_run)}] {sid} ({cat}{mode})")
        print(f"  Prompt: {prompt[:90]}{'...' if len(prompt) > 90 else ''}")

        # Attach vision payload if needed
        if scenario.get("vision"):
            if vision_b64 is None or args.no_vision:
                print("  SKIPPED — no screenshot.\n")
                entries.append({"scenario": scenario, "skipped": True})
                continue
            scenario = {**scenario, "vision_b64": vision_b64}

        try:
            result = run_scenario(scenario)
        except Exception as exc:
            print(f"  ERROR: {exc}\n")
            entries.append({"scenario": scenario, "error": str(exc)})
            continue

        verdicts = run_auto_checks(scenario, result)

        # Tally native-path robustness metrics
        total_text_leaks  += result["text_leak_count"]
        total_json_errors += result["json_error_count"]
        total_api_calls   += result["rounds_used"]
        all_metrics_lists.append(result["all_metrics"])

        # Overall pass/fail — auto-only checks decide; manual-only → None
        auto_verdicts = {k: v for k, v in verdicts.items() if not v.get("manual")}
        overall: bool | None = (
            all(v["passed"] for v in auto_verdicts.values())
            if auto_verdicts else None
        )

        status = "✓ PASS" if overall is True else ("✗ FAIL" if overall is False else "⚠ MANUAL")
        last_m = result["all_metrics"][-1] if result["all_metrics"] else {}
        print(
            f"  {status}  |  Tools: {result['tool_calls']}  "
            f"|  Rounds: {result['rounds_used']}  "
            f"|  {last_m.get('gen_tok_per_s', 0):.1f} tok/s"
        )
        for check_name, verdict in verdicts.items():
            flag = "✓" if verdict["passed"] else ("?" if verdict.get("manual") else "✗")
            print(f"    [{flag}] {check_name}: {verdict['notes'][:110]}")
        print()

        entries.append({
            "scenario": scenario,
            "result":   result,
            "verdicts": verdicts,
            "overall":  overall,
        })

    wall_elapsed = time.perf_counter() - wall_start

    # ── Report ────────────────────────────────────────────────────────────────
    agg = aggregate_metrics(all_metrics_lists)
    report = build_report(
        entries, model_name, ctx_size, agg,
        wall_elapsed, total_text_leaks, total_json_errors, total_api_calls,
    )

    ts         = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = model_name.replace("/", "_").replace(" ", "_")[:60]
    os.makedirs(RESULTS_DIR, exist_ok=True)
    report_path = os.path.join(RESULTS_DIR, f"engine_report_{safe_model}_{ts}.txt")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    # Count totals for summary
    auto_passed = sum(
        1 for e in entries
        if e.get("overall") is True
    )
    auto_total = sum(
        1 for e in entries
        if e.get("overall") is not None
    )

    print("=" * 72)
    print(f"  Report: {report_path}")
    print(f"  Auto-scored: {auto_passed}/{auto_total}  "
          f"({auto_passed/auto_total*100:.0f}%)" if auto_total else "  Auto-scored: 0/0")
    print(f"  Avg generation: {agg['avg_gen_tok_s']:.1f} tok/s")
    print(f"  Total wall time: {wall_elapsed:.1f}s")
    print("=" * 72)


if __name__ == "__main__":
    main()
