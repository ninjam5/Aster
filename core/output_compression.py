"""
Tool-output compression — truncates or summarizes oversized tool results
before they enter conversation history, to slow long-context degradation on
the Q4_0-quantized 128k KV cache.

Per-tool policy: short/UI/dict results pass through untouched; verbose
file/listing tools are head/tail-truncated with a tombstone; `research` is
LLM-summarized (with a truncation fallback on any failure). Compressing after
normalization in the loop (str(tool_result), post audio-tag extraction) keeps
one choke point regardless of execute_tool()'s heterogeneous str|dict|None
return type.

The summarizer (core.brain._execute_gemma_completion) is injected via
set_summarizer() at brain.py import time rather than imported directly, to
avoid a core.brain <-> core.output_compression import cycle.

See .claude/skills/aster-gemma-reliability-campaign.
"""
from __future__ import annotations

from typing import Callable, Optional

import config

# Injected by core/brain.py at import time: set_summarizer(_execute_gemma_completion)
_summarizer: Optional[Callable[..., dict]] = None


def set_summarizer(fn: Callable[..., dict]) -> None:
    """Register the completion function used by the 'summarize' policy.
    Expected signature: fn(messages, temperature=..., n_predict=...) -> dict
    (same shape as core.brain._execute_gemma_completion)."""
    global _summarizer
    _summarizer = fn


# Policy table — char thresholds (~4 chars/token). Tools not listed, or
# whose result isn't a plain string, pass through untouched.
_POLICY = {
    "read_local_file":        {"kind": "head_tail", "threshold": 6000, "head": 4000, "tail": 1000, "arg_key": "file_path"},
    "list_directory_tree":    {"kind": "head",       "threshold": 4000, "head": 4000},
    "get_notes":               {"kind": "head",       "threshold": 4000, "head": 4000},
    "list_running_processes": {"kind": "head",       "threshold": 3000, "head": 3000},
    "research":                {"kind": "summarize", "threshold": 6000, "head": 6000},
}


def _truncate_head_tail(text: str, head: int, tail: int, path: str) -> str:
    omitted = len(text) - head - tail
    tombstone = f"\n[... {omitted} chars omitted from {path} — re-read with a narrower request if needed]\n"
    return text[:head] + tombstone + text[-tail:]


def _truncate_head(text: str, head: int) -> str:
    omitted = len(text) - head
    return text[:head] + f"\n[... {omitted} more characters omitted]\n"


def _summarize(text: str, fallback_head: int) -> str:
    if _summarizer is None:
        return _truncate_head(text, fallback_head)
    try:
        prompt = (
            "Summarize the following research material for an assistant that must "
            "answer a user's question from it. Preserve all URLs, numbers, dates, "
            "and proper names verbatim. Be concise.\n\n" + text
        )
        msg = _summarizer(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            n_predict=300,
        )
        summary = (msg.get("content") or "").strip()
        if not summary:
            raise ValueError("summarizer returned empty content")
        return f"[Summarized from {len(text)} chars]\n{summary}"
    except Exception as e:
        print(f"[Aster Reliability] research summarization failed, falling back to truncation: {e}")
        return _truncate_head(text, fallback_head)


def compress_tool_output(tool_name: str, text: str, arguments: dict | None = None) -> str:
    """Apply this tool's compression policy to `text`. No-op unless
    config.TOOL_OUTPUT_COMPRESSION is True; never raises (falls back to the
    original text on any error)."""
    if not config.TOOL_OUTPUT_COMPRESSION:
        return text
    if not isinstance(text, str):
        return text
    policy = _POLICY.get(tool_name)
    if not policy or len(text) <= policy["threshold"]:
        return text

    try:
        kind = policy["kind"]
        if kind == "head_tail":
            arg_key = policy.get("arg_key")
            path = (arguments or {}).get(arg_key) if arg_key else None
            return _truncate_head_tail(text, policy["head"], policy["tail"], path or "the file")
        if kind == "head":
            return _truncate_head(text, policy["head"])
        if kind == "summarize":
            return _summarize(text, policy["head"])
        return text
    except Exception as e:
        print(f"[Aster Reliability] output compression failed for {tool_name}: {e}")
        return text
