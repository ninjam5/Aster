"""
LoopGuard — per-turn detector for degenerate agentic tool-call loops.

Two failure shapes it catches, neither guarded against anywhere else in the
codebase before this module:
  1. Identical-call budget: the same tool called with the same arguments too
     many times in one turn (a hash-budget generalization of the smart_click-
     only repeat guard already in core/brain.py's dispatch loop).
  2. Ping-pong: alternating between exactly two distinct calls (A-B-A-B) with
     no progress.

One instance per user turn — NOT a module singleton. process_user_input() is
entered from multiple threads (CLI, Telegram, LiveKit, Discord's own mini-
loop), so a per-turn instance constructed alongside the loop's other per-turn
state (tools_executed, failed_tools, gui_click_counts, ...) is thread-confined
by construction and resets automatically. No locking required.

See .claude/skills/aster-gemma-reliability-campaign.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import NamedTuple

from core import instrumentation

# watch_screen polls the screen every 10s by design — its repeated calls are
# expected, not a loop. Never blocked; budget is much larger.
DEFAULT_POLLING_TOOLS = frozenset({"watch_screen"})

# smart_click already carries its own screen-diff-aware repeat warning
# (core/brain.py dispatch loop) and legitimate UI flows (wizard "Next"
# buttons, retrying after a state change) can look identical on args alone.
# LoopGuard escalates warnings for it but never blocks.
DEFAULT_WARN_ONLY_TOOLS = frozenset({"smart_click"})

# Search/lookup tools where models tend to permute query arguments ("project
# proposal" -> "proposal" -> "this week") to bypass exact-argument hashing
# when a target doesn't exist. Tracked both per-tool (regardless of args) and
# across the whole search family per turn.
DEFAULT_SEARCH_FAMILY_TOOLS = frozenset({
    "research",
    "search_emails",
    "recall_memory",
    "list_directory_tree",
    "get_notes",
    "list_my_contacts",
    "summarize_unread_emails",
})


class Verdict(NamedTuple):
    action: str        # "allow" | "warn" | "block"
    message: str        # text to append to the tool result ("" for allow)
    call_hash: str
    count: int


def _hash_call(tool_name: str, arguments: dict) -> str:
    try:
        args_repr = json.dumps(arguments, sort_keys=True, default=str)
    except Exception:
        args_repr = str(arguments)
    raw = f"{tool_name}|{args_repr}".encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:12]


class LoopGuard:
    """Tracks tool calls within one turn and flags degenerate repetition.

    `enforce=False` (sensor-only / LOOP_GUARD_ENABLED off) still records
    `repeat_call` instrumentation events but every verdict is downgraded to
    "allow" — the tool-call loop's behavior is completely unchanged, only the
    measurement continues.
    """

    def __init__(
        self,
        enforce: bool = True,
        warn_at: int = 3,
        block_at: int = 4,
        polling_tools: frozenset[str] = DEFAULT_POLLING_TOOLS,
        warn_only_tools: frozenset[str] = DEFAULT_WARN_ONLY_TOOLS,
        search_family_tools: frozenset[str] = DEFAULT_SEARCH_FAMILY_TOOLS,
        search_tool_warn_at: int = 3,
        search_tool_block_at: int = 4,
        search_family_warn_at: int = 5,
        search_family_block_at: int = 6,
        pingpong_window: int = 6,
    ):
        self.enforce = enforce
        self.warn_at = warn_at
        self.block_at = block_at
        self.polling_tools = polling_tools
        self.warn_only_tools = warn_only_tools
        self.search_family_tools = search_family_tools
        self.search_tool_warn_at = search_tool_warn_at
        self.search_tool_block_at = search_tool_block_at
        self.search_family_warn_at = search_family_warn_at
        self.search_family_block_at = search_family_block_at
        self._counts: dict[str, int] = {}
        self._tool_counts: dict[str, int] = {}
        self._search_family_count: int = 0
        self._history: deque[str] = deque(maxlen=pingpong_window)
        self._pingpong_warned = False
        self._pingpong_logged = False

    def _detect_pingpong(self) -> bool:
        """True if the last 4 recorded calls alternate A-B-A-B between exactly
        two distinct hashes."""
        if len(self._history) < 4:
            return False
        a, b, c, d = list(self._history)[-4:]
        return a == c and b == d and a != b

    def check(self, tool_name: str, arguments: dict) -> Verdict:
        """Record one call and return the verdict for it. Call exactly once
        per tool invocation, before execute_tool()."""
        call_hash = _hash_call(tool_name, arguments)
        count = self._counts[call_hash] = self._counts.get(call_hash, 0) + 1
        tool_count = self._tool_counts[tool_name] = self._tool_counts.get(tool_name, 0) + 1
        is_search_family = tool_name in self.search_family_tools
        if is_search_family:
            self._search_family_count += 1
        self._history.append(call_hash)
        pingpong = self._detect_pingpong()

        # Sensor: log at the warn/block thresholds regardless of enforcement,
        # so the live baseline keeps accruing even with the flag off.
        if count in (self.warn_at, self.block_at):
            instrumentation.record_event(
                "repeat_call", tool=tool_name, call_hash=call_hash, count=count,
            )
        if pingpong and not self._pingpong_logged:
            self._pingpong_logged = True
            instrumentation.record_event(
                "repeat_call", tool=tool_name, call_hash=call_hash, count=count, pingpong=True,
            )

        if not self.enforce:
            return Verdict("allow", "", call_hash, count)

        is_polling   = tool_name in self.polling_tools
        is_warn_only = tool_name in self.warn_only_tools
        can_block    = not is_polling and not is_warn_only
        effective_warn_at = max(self.warn_at, 15) if is_polling else self.warn_at

        action, message = "allow", ""

        if pingpong:
            if not self._pingpong_warned:
                self._pingpong_warned = True
                action = "warn"
                message = (
                    f"[LoopGuard] WARNING: you are alternating between two calls "
                    f"(most recently {tool_name}) with no progress. Break the cycle — "
                    f"try a different approach, or report your status to the user."
                )
            elif can_block:
                action = "block"
                message = (
                    f"[LoopGuard] BLOCKED: you are stuck alternating between two calls "
                    f"(most recently {tool_name}) with no progress. This call was not "
                    f"executed. Choose a different approach entirely, or report your "
                    f"status to the user."
                )
            else:
                action = "warn"
                message = (
                    f"[LoopGuard] WARNING: still alternating between two calls "
                    f"(most recently {tool_name}) with no progress. Stop and try "
                    f"something else, or tell the user what is blocking you."
                )
        elif can_block and count >= self.block_at:
            action = "block"
            message = (
                f"[LoopGuard] BLOCKED: identical call #{count} to {tool_name} this turn. "
                f"The result will not change. Stop calling tools and report your status "
                f"or ask the user a clarifying question."
            )
        elif can_block and is_search_family and tool_count >= self.search_tool_block_at:
            action = "block"
            message = (
                f"[LoopGuard] BLOCKED: search exhaustion (#{tool_count} calls to {tool_name} "
                f"this turn). Do NOT try more query variations — stop calling search/lookup "
                f"tools and respond directly to the user with what you found or ask ONE "
                f"clarifying question."
            )
        elif can_block and is_search_family and self._search_family_count >= self.search_family_block_at:
            action = "block"
            message = (
                f"[LoopGuard] BLOCKED: search budget exhausted ({self._search_family_count} "
                f"lookup calls this turn). Do NOT call any more search/lookup tools — "
                f"respond directly to the user and ask ONE clarifying question for any "
                f"missing information."
            )
        elif count >= effective_warn_at:
            action = "warn"
            message = (
                f"[LoopGuard] WARNING: attempt #{count} calling {tool_name} with the same "
                f"arguments this turn. If this has not worked, STOP repeating it — try "
                f"something else or tell the user what is blocking you."
            )
        elif is_search_family and tool_count >= self.search_tool_warn_at:
            action = "warn"
            message = (
                f"[LoopGuard] WARNING: search attempt #{tool_count} calling {tool_name} this "
                f"turn. If previous queries returned no results, do NOT keep trying query "
                f"variations — stop searching and report your findings or ask the user ONE "
                f"clarifying question."
            )
        elif is_search_family and self._search_family_count >= self.search_family_warn_at:
            action = "warn"
            message = (
                f"[LoopGuard] WARNING: {self._search_family_count} lookup/search calls "
                f"executed this turn. Stop searching and either answer with what you found "
                f"or ask the user ONE clarifying question."
            )

        if action != "allow":
            instrumentation.record_event(
                f"loop_guard_{action}", tool=tool_name, call_hash=call_hash,
                count=count, pingpong=pingpong,
            )

        return Verdict(action, message, call_hash, count)
