"""Structured tool-result envelope (reliability campaign round 2).

Tools migrated to return ToolResult(ok, text) give the brain's
failure-blind-claim guard an EXACT success/failure signal instead of the
legacy "result starts with FAILED/Error" prefix regex. Unmigrated tools keep
returning plain strings — core.brain.execute_tool_ex() normalizes those with
the regex heuristic, so migration is incremental and the LLM-facing text is
unchanged either way (migrated failures keep their FAILED prefix because the
tool schemas document it).
"""
from typing import NamedTuple


class ToolResult(NamedTuple):
    ok: bool
    text: str

    def __str__(self) -> str:  # defensive: str(tool_result) sites see the text
        return self.text


def tool_ok(text) -> ToolResult:
    return ToolResult(True, str(text))


def tool_fail(text) -> ToolResult:
    return ToolResult(False, str(text))
