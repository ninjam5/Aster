"""
Aster Engine Test Harness
LLM client, mocked ReAct loop, and auto-scoring helpers.

Imports core.brain to reuse the live system prompt, ADMIN_TOOLS, and native
tool-call extraction so tests run inside Aster's REAL machinery — not a
re-implementation.

Native-only since 2026-07: production runs native OpenAI-format tool calling
(config.USE_NATIVE_TOOL_CALLS defaults True) and this harness now exercises
only that path — it sends `tools=ADMIN_TOOLS` and reads structured
`message["tool_calls"]`, exactly like core.brain's real agentic loop. The
legacy XML-in-text path is NOT tested here anymore; its comparator is the
frozen reports in engine_testing/results/ (pre-2026-07) plus production's own
rollback flag (`runtime.use_native_tool_calls: false` in self_config.yaml).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Callable

# ── Add repo root to sys.path so core.brain is importable ────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Import live Aster machinery ───────────────────────────────────────────────
try:
    from core.brain import (
        ADMIN_TOOLS,
        _extract_native_tool_call,
        _claims_tool_execution,
        _claims_knowledge_cutoff,
        messages as _brain_messages,
    )
    from core.loop_guard import LoopGuard
    SYSTEM_PROMPT: str = _brain_messages[0]["content"]
    print("[Harness] core.brain imported — live system prompt and tools active.")
except Exception as _e:
    print(f"\n[Harness] FATAL: Could not import core.brain:\n  {_e}")
    print("  Ensure all Aster dependencies are installed (pip install -r requirements.txt).")
    print("  llama-server does NOT need to be running for the import — only for test calls.\n")
    raise SystemExit(1)

# ── Production constants (must match brain.py) ────────────────────────────────
SERVER_URL          = "http://localhost:8080"
MAX_TOOL_ROUNDS     = 15
TEMPERATURE         = 0.7
TOP_P:          float | None = None   # None = let server default apply
TOP_K:          int   | None = None   # None = let server default apply
REPEAT_PENALTY: float | None = None   # None = let server default apply
MIN_P:          float | None = None   # None = let server default apply

import requests


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER PROBE
# ═══════════════════════════════════════════════════════════════════════════════

def get_server_props() -> dict:
    """Fetch model metadata from llama-server /props endpoint."""
    try:
        r = requests.get(f"{SERVER_URL}/props", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# LLM CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

def llm_call(
    messages: list[dict],
    temperature: float = TEMPERATURE,
    n_predict: int = 3000,
) -> tuple[dict, dict]:
    """
    POST to llama-server /v1/chat/completions with native tool calling enabled.

    Mirrors _execute_gemma_completion() in brain.py — same payload shape (incl.
    `tools`/`tool_choice="auto"`), same trailing-assistant-strip (Gemma Jinja
    template incompatibility) — but without the debug spam.

    Returns:
        message     — full choices[0]["message"] dict (role/content/tool_calls),
                      same shape _execute_gemma_completion returns
        metrics     — {prompt_tokens, completion_tokens, elapsed_s,
                       gen_tok_per_s, prompt_tok_per_s}
    """
    # Mirror brain.py: strip trailing assistant message before sending
    # (Gemma Jinja template incompatibility with assistant prefill)
    msgs = list(messages)
    if msgs and msgs[-1].get("role") == "assistant":
        msgs = msgs[:-1]

    # Inject <image> marker into multimodal messages (matches brain.py:66-80)
    for msg in msgs:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if not any(p.get("type") == "image_url" for p in content):
            continue
        for part in content:
            if part.get("type") == "text":
                if not part["text"].startswith("<image>"):
                    part["text"] = "<image>\n" + part["text"]
                break
        else:
            content.append({"type": "text", "text": "<image>"})

    payload = {
        "model": "gemma",
        "messages": msgs,
        "temperature": temperature,
        "max_tokens": n_predict,
        "stream": False,
        "tools": ADMIN_TOOLS,
        "tool_choice": "auto",
    }
    if TOP_P is not None:
        payload["top_p"] = TOP_P
    if TOP_K is not None:
        payload["top_k"] = TOP_K
    if REPEAT_PENALTY is not None:
        payload["repeat_penalty"] = REPEAT_PENALTY
    if MIN_P is not None:
        payload["min_p"] = MIN_P

    t0 = time.perf_counter()
    try:
        r = requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json=payload,
            timeout=180,
        )
        elapsed = time.perf_counter() - t0
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.Timeout:
        raise RuntimeError("LLM call timed out after 180s")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Cannot reach llama-server at {SERVER_URL} — is it running?")
    except Exception as exc:
        raise RuntimeError(f"LLM call failed: {exc}")

    message = data["choices"][0]["message"]
    if message.get("content") is not None:
        message["content"] = message["content"].strip()

    usage = data.get("usage", {})
    prompt_tokens     = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # Prefer llama-server's own timings (more accurate than wall-clock)
    timings        = data.get("timings", {})
    gen_tok_per_s  = (timings.get("predicted_per_second")
                      or (completion_tokens / elapsed if elapsed > 0 else 0.0))
    prompt_tok_per_s = timings.get("prompt_per_second") or 0.0

    metrics = {
        "prompt_tokens":     prompt_tokens,
        "completion_tokens": completion_tokens,
        "elapsed_s":         elapsed,
        "gen_tok_per_s":     gen_tok_per_s,
        "prompt_tok_per_s":  prompt_tok_per_s,
    }
    return message, metrics


# ═══════════════════════════════════════════════════════════════════════════════
# MOCKED REACT LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def run_scenario(scenario: dict) -> dict:
    """
    Run a single test scenario through a mocked ReAct loop — native tool calling.

    The loop replicates the production brain loop's native branch (brain.py
    ~3136-3301):
      - same MAX_TOOL_ROUNDS (15)
      - same temperature (0.7 harness default; production uses config.LLM_TEMPERATURE)
      - `tools=ADMIN_TOOLS`, `tool_choice="auto"` sent on every call (no XML
        tool manual, no fake-assistant [INST] reminder — native mode needs neither)
      - uses the real _extract_native_tool_call() parser
      - tool results fed back as {"role": "tool", "tool_call_id": ...} messages,
        exactly like brain.py:3290-3295
      - real tools are replaced by mock_results strings/callables

    Returns a result dict:
        transcript        — list[{role, content}] for every round (human-readable)
        tool_calls        — ordered list of tool names called
        tool_call_records — ordered list of {"name", "arguments"} dicts (structured,
                             used by scoring instead of re-parsing the transcript)
        final_response     — last plain-text model output
        all_metrics        — list of per-round metric dicts
        text_leak_count     — times the model emitted tool-call-shaped text
                              (<tool_call> / <|tool_call) while tool_calls was
                              EMPTY — the native-mode analogue of XML tag mangling:
                              the grammar wasn't engaged and the model narrated
                              syntax as prose instead
        json_error_count    — times _extract_native_tool_call hit a JSON decode
                              error in the native tool_calls[0].function.arguments
        empty_final         — True if the loop ended with no tool calls AND an
                              empty final_response after at least one tool ran
                              (the lab-side apathy signal)
        rounds_used         — total API calls made
        last_message        — the final raw response_msg dict (role/content/tool_calls),
                              passed to check_no_hallucination to mirror production's
                              _claims_tool_execution(..., message=response_msg) signature
    """
    mock_results: dict    = scenario.get("mock_results", {})
    prompt: str           = scenario["prompt"]
    vision_b64: str | None = scenario.get("vision_b64")

    # Build the initial user message
    if vision_b64:
        user_content: Any = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{vision_b64}"}},
            {"type": "text", "text": prompt},
        ]
    else:
        user_content = prompt

    # Conversation history for this scenario (fresh per run)
    history = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    transcript: list[dict]  = [{"role": "user", "content": prompt}]
    tool_calls_made: list[str] = []
    tool_call_records: list[dict] = []
    all_metrics: list[dict]    = []
    text_leak_count  = 0
    json_error_count = 0
    final_response   = ""
    any_tool_ran     = False
    last_message: dict = {}
    # Mirror production: per-turn LoopGuard exactly as brain.py wires it
    # (block → verdict message becomes the tool result; warn → appended).
    loop_guard = LoopGuard(enforce=True)
    loop_guard_events: list[dict] = []
    executed_call_counts: dict[str, int] = {}   # identical (name, args) → executions

    for _round in range(MAX_TOOL_ROUNDS):
        eval_msgs = list(history)

        response_msg, metrics = llm_call(eval_msgs)
        all_metrics.append(metrics)
        last_message = response_msg
        content = response_msg.get("content") or ""

        tool_payload, _cleaned = _extract_native_tool_call(response_msg)

        # Native-mode leak signal: the model emitted tool-call-shaped text
        # instead of engaging the tools grammar (tool_calls empty).
        if not response_msg.get("tool_calls") and re.search(r"<tool_call>|<\|tool_call", content):
            text_leak_count += 1

        if tool_payload:
            tool_name = (tool_payload.get("name") or "").strip()
            tool_args = tool_payload.get("arguments") or {}

            # JSON decode failure — mirror brain.py's native self-correction path
            if tool_payload.get("json_error"):
                json_error_count += 1
                history.append({
                    "role": "assistant", "content": content,
                    "tool_calls": response_msg.get("tool_calls"),
                })
                history.append({"role": "user", "content": (
                    "[System note: Your tool call contained a JSON formatting error. "
                    "Please ensure argument values are correctly formatted and try again.]"
                )})
                transcript.append({"role": "assistant", "content": f"[JSON_ERROR] {content}"})
                continue

            # Record the tool call
            tool_calls_made.append(tool_name)
            tool_call_records.append({"name": tool_name, "arguments": tool_args})
            call_repr = json.dumps({"name": tool_name, "arguments": tool_args})
            transcript.append({"role": "assistant", "content": f"<tool_call>{call_repr}</tool_call>"})

            # LoopGuard check before execution — same order as brain.py
            _lg_verdict = loop_guard.check(tool_name, tool_args)
            if _lg_verdict.action != "allow":
                loop_guard_events.append({
                    "action": _lg_verdict.action, "tool": tool_name, "count": _lg_verdict.count,
                })

            if _lg_verdict.action == "block":
                tool_result = _lg_verdict.message
            else:
                # Execute mock
                if callable(mock_results.get(tool_name)):
                    tool_result = mock_results[tool_name](tool_args)
                elif tool_name in mock_results:
                    tool_result = mock_results[tool_name]
                else:
                    tool_result = f"[Mock: {tool_name} executed successfully with args {tool_args}]"
                executed_call_counts[_lg_verdict.call_hash] = (
                    executed_call_counts.get(_lg_verdict.call_hash, 0) + 1
                )
                if _lg_verdict.action == "warn":
                    tool_result = f"{tool_result} {_lg_verdict.message}"

            any_tool_ran = True
            history.append({
                "role": "assistant", "content": response_msg.get("content"),
                "tool_calls": response_msg.get("tool_calls"),
            })
            history.append({
                "role": "tool",
                "tool_call_id": tool_payload.get("tool_call_id", ""),
                "content": str(tool_result),
            })
            transcript.append({"role": "system",  "content": f"[Tool result for {tool_name}]: {tool_result}"})

        else:
            # Plain-text response — end of agentic loop
            final_response = content
            transcript.append({"role": "assistant", "content": content})
            history.append({"role": "assistant",   "content": content})
            break

    # Apathy signal: at least one tool executed this turn, but the loop produced
    # no final text (either an empty plain-text exit or MAX_TOOL_ROUNDS exhausted
    # without ever reaching one).
    empty_final = any_tool_ran and not final_response.strip()

    return {
        "transcript":        transcript,
        "tool_calls":        tool_calls_made,
        "tool_call_records": tool_call_records,
        "final_response":    final_response,
        "all_metrics":       all_metrics,
        "text_leak_count":   text_leak_count,
        "json_error_count":  json_error_count,
        "empty_final":       empty_final,
        "rounds_used":       len(all_metrics),
        "last_message":      last_message,
        "loop_guard_events": loop_guard_events,
        "executed_call_counts": executed_call_counts,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-SCORING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_BANNED_SLANG = re.compile(
    r"\b(lmao|lol|ngl|fr\b|bruh|bro|fam|vibe|slay|bet|gm|gn|imo|irl|omg|wtf|smh|lowkey|highkey|goated|bussin)\b",
    re.IGNORECASE,
)
_SENTENCE_END  = re.compile(r"(?<=[.!?])\s+")
_HONORIFIC     = re.compile(r"\b(Sir|Boss)\b")


def _extract_first_tool_args(result: dict, tool_name: str) -> dict:
    """Pull the argument dict from the first matching structured tool call record."""
    for record in result.get("tool_call_records", []):
        if record.get("name") == tool_name:
            return record.get("arguments", {})
    return {}


def check_single_tool(
    result: dict,
    expected_tool: str,
    expected_args: dict | None = None,
    strict_first: bool = True,
) -> dict:
    """
    Pass if the correct tool was called, with args matching expected_args.

    strict_first=True (default): expected_tool must be the FIRST call.
    strict_first=False: expected_tool may appear anywhere in the call list
      (use for scenarios where a sensible prereq tool call is acceptable).
    """
    called  = result["tool_calls"]
    if strict_first:
        tool_ok = bool(called) and called[0] == expected_tool
        got_str = called[0] if called else "none"
    else:
        tool_ok = expected_tool in called
        got_str = str(called) if called else "none"

    args_ok  = True
    arg_notes: list[str] = []

    if tool_ok and expected_args:
        actual_args = _extract_first_tool_args(result, expected_tool)
        for key, expected_val in expected_args.items():
            actual_val = actual_args.get(key)
            if callable(expected_val):
                ok = expected_val(actual_val)
            elif isinstance(expected_val, str):
                ok = expected_val.lower() in str(actual_val or "").lower()
            else:
                ok = (actual_val == expected_val)
            if not ok:
                args_ok = False
                arg_notes.append(f"'{key}': expected {expected_val!r}, got {actual_val!r}")

    passed = tool_ok and args_ok
    notes  = (
        f"Tool {'✓' if tool_ok else '✗'} "
        f"(expected={expected_tool}, got={got_str})."
    )
    if expected_args:
        notes += f"  Args {'✓' if args_ok else '✗'}."
        if arg_notes:
            notes += "  Mismatches: " + "; ".join(arg_notes)
    return {"passed": passed, "notes": notes, "manual": False}


def check_loop_discipline(result: dict) -> dict:
    """loop_trap scenarios: mock results are engineered to tempt re-calling.

    Pass iff (a) no identical (name, args) call was EXECUTED more than 3 times
    (LoopGuard blocks the 4th, so >3 executions means the guard failed or was
    bypassed via slightly-varied args grinding), and (b) the model recovered
    with a non-empty final answer instead of looping until MAX_TOOL_ROUNDS.
    """
    counts = result.get("executed_call_counts", {})
    max_repeat = max(counts.values(), default=0)
    repeat_ok  = max_repeat <= 3
    final_ok   = bool(result.get("final_response", "").strip())
    blocks     = [e for e in result.get("loop_guard_events", []) if e["action"] == "block"]
    passed = repeat_ok and final_ok
    notes = (
        f"Max identical executions {'✓' if repeat_ok else '✗'} ({max_repeat} ≤ 3).  "
        f"Non-empty final answer {'✓' if final_ok else '✗'}."
    )
    if blocks:
        notes += f"  LoopGuard blocked {len(blocks)} call(s) — model needed the guard."
    return {"passed": passed, "notes": notes, "manual": False}


def check_no_tool(result: dict) -> dict:
    called = result["tool_calls"]
    return {
        "passed": not called,
        "notes":  f"No tool emitted: {'✓' if not called else '✗ (called: ' + str(called) + ')'}",
        "manual": False,
    }


def check_tool_sequence(result: dict, expected_sequence: list[str]) -> dict:
    """Pass if expected_sequence appears as a subsequence of actual tool calls."""
    called = result["tool_calls"]
    idx    = 0
    for tool in called:
        if idx < len(expected_sequence) and tool == expected_sequence[idx]:
            idx += 1
    passed = (idx == len(expected_sequence))
    return {
        "passed": passed,
        "notes":  f"Sequence {'✓' if passed else '✗'}: expected={expected_sequence}, got={called}",
        "manual": False,
    }


def check_no_hallucination(result: dict, user_prompt: str) -> dict:
    """
    Pass if:
      (a) a real <tool_call> was emitted, AND
      (b) the final response does NOT narrate the action without a tool call.

    Note: _claims_tool_execution is only run when no tool was called. Post-tool
    confirmation text ("The screenshot has been captured, Sir.") legitimately
    contains action words and must not be flagged as hallucination.
    """
    tool_called = bool(result["tool_calls"])
    if tool_called:
        hal = False
    else:
        hal = _claims_tool_execution(
            user_prompt, result["final_response"], message=result.get("last_message")
        )
    passed = tool_called and not hal
    return {
        "passed": passed,
        "notes":  (
            f"Tool called: {'✓' if tool_called else '✗'}  "
            f"Hallucination detected: {'✗ YES' if hal else '✓ NO'}"
        ),
        "manual": False,
    }


def check_cutoff_routing(result: dict) -> dict:
    """Pass if 'research' was called and the response does NOT cite a knowledge cutoff."""
    called_research = "research" in result["tool_calls"]
    resp            = result["final_response"]
    cutoff          = _claims_knowledge_cutoff(resp)
    passed          = called_research and not cutoff
    return {
        "passed": passed,
        "notes":  (
            f"research called: {'✓' if called_research else '✗'}  "
            f"Cutoff refusal: {'✗ YES' if cutoff else '✓ NO'}"
        ),
        "manual": False,
    }


def check_persona(result: dict, max_sentences: int = 2) -> dict:
    """
    Heuristic persona check:
      - Honorific present ("Sir" or "Boss")
      - Sentence count within budget
      - No banned slang
    Also flagged for manual review (wit / register).
    """
    resp           = (result["final_response"] or "").strip()
    sentences      = [s for s in _SENTENCE_END.split(resp) if s.strip()]
    sentence_count = len(sentences)
    has_honorific  = bool(_HONORIFIC.search(resp))
    slang_match    = _BANNED_SLANG.search(resp)
    sentence_ok    = (sentence_count <= max_sentences) if max_sentences < 999 else True
    passed         = has_honorific and sentence_ok and not slang_match
    return {
        "passed": passed,
        "notes":  (
            f"Honorific: {'✓' if has_honorific else '✗'}  "
            f"Sentences: {sentence_count}"
            + (f"/{max_sentences} {'✓' if sentence_ok else '✗'}" if max_sentences < 999 else " (uncapped)")
            + f"  Slang: {'✗ ' + slang_match.group() if slang_match else '✓ none'}"
        ),
        "manual": True,
    }


def check_discord_relay(result: dict) -> dict:
    """
    Pass if send_discord_message was called with:
      - target_name present
      - message contains a Boss/Sir/Mohamed attribution
    Flagged for manual tone review.
    """
    called  = result["tool_calls"]
    tool_ok = "send_discord_message" in called
    if not tool_ok:
        return {"passed": False, "notes": "send_discord_message not called.", "manual": True}

    args         = _extract_first_tool_args(result, "send_discord_message")
    has_target   = bool(args.get("target_name"))
    msg          = str(args.get("message", "")).lower()
    has_attrib   = any(w in msg for w in ("boss", "sir", "mohamed"))
    has_3rd_pers = any(w in msg for w in ("has asked", "wanted", "wish", "inform", "convey", "asked me", "would like"))
    passed       = has_target and has_attrib
    return {
        "passed": passed,
        "notes":  (
            f"target_name: {'✓' if has_target else '✗'}  "
            f"Boss attribution: {'✓' if has_attrib else '✗'}  "
            f"Third-person phrasing: {'✓' if has_3rd_pers else '? check manually'}"
        ),
        "manual": True,
    }


def check_vision(result: dict) -> dict:
    """Pass if the response is substantive (≥8 words). Flagged for manual accuracy review."""
    resp    = (result["final_response"] or "").strip()
    words   = len(resp.split())
    passed  = words >= 8
    return {
        "passed": passed,
        "notes":  f"Response length: {words} words {'✓' if passed else '✗ (suspiciously short)'}",
        "manual": True,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# METRICS AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_metrics(all_scenario_metrics: list[list[dict]]) -> dict:
    gen_speeds:    list[float] = []
    prompt_speeds: list[float] = []
    latencies:     list[float] = []
    total_gen    = 0
    total_prompt = 0

    for scenario_metrics in all_scenario_metrics:
        for m in scenario_metrics:
            if m["gen_tok_per_s"] > 0:
                gen_speeds.append(m["gen_tok_per_s"])
            if m["prompt_tok_per_s"] > 0:
                prompt_speeds.append(m["prompt_tok_per_s"])
            latencies.append(m["elapsed_s"])
            total_gen    += m["completion_tokens"]
            total_prompt += m["prompt_tokens"]

    avg = lambda lst: sum(lst) / len(lst) if lst else 0.0
    return {
        "avg_gen_tok_s":     avg(gen_speeds),
        "avg_prompt_tok_s":  avg(prompt_speeds),
        "avg_latency_s":     avg(latencies),
        "total_gen_tokens":  total_gen,
        "total_prompt_tokens": total_prompt,
    }
