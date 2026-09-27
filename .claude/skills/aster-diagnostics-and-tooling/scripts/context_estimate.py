#!/usr/bin/env python3
"""Estimate token usage the way Aster's trim_memory does (len(content)//4).

Mirrors core/memory.py:_estimate_tokens and prints the budget math
(budget = 90% of runtime.context_window, default 131072).

Usage (repo root):
    python .claude/skills/aster-diagnostics-and-tooling/scripts/context_estimate.py <file>
    echo "some text" | python .claude/skills/aster-diagnostics-and-tooling/scripts/context_estimate.py
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def read_n_ctx():
    """Read runtime.context_window from self_config.yaml if present, else 131072
    (config.py's default). Reads ONLY that one key; never prints other content."""
    try:
        import yaml
        with open(os.path.join(REPO, "self_config.yaml"), encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return int(data.get("runtime", {}).get("context_window", 131072))
    except Exception:
        return 131072


def estimate_tokens(text: str) -> int:
    # exact mirror of core/memory.py:_estimate_tokens for a single content blob
    return len(text) // 4


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
            text = f.read()
        src = sys.argv[1]
    else:
        text = sys.stdin.read()
        src = "<stdin>"

    n_ctx = read_n_ctx()
    budget = int(n_ctx * 0.9)   # mirror of trim_memory's default max_tokens
    est = estimate_tokens(text)
    print(f"source:            {src}")
    print(f"chars:             {len(text)}")
    print(f"estimated tokens:  {est}   (len//4 heuristic — same as trim_memory)")
    print(f"N_CTX:             {n_ctx}")
    print(f"trim budget (90%): {budget}")
    print(f"fraction of budget: {est / budget * 100:.1f}%")
    print("note: code/JSON tokenize denser than 4 chars/token — real usage is likely HIGHER than this estimate.")


if __name__ == "__main__":
    main()
