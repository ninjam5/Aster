#!/usr/bin/env python3
"""Print Aster's live admin-tool registry (name + first line of description).

Offline-safe (no llama-server needed) but SLOW (~1 min): importing core.brain
loads the face-recognition models. Run from the repo root:

    python .claude/skills/aster-diagnostics-and-tooling/scripts/dump_tool_registry.py
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, REPO)
os.chdir(REPO)  # brain/config expect repo-root-relative paths

import core.brain as b  # noqa: E402  (heavy import by design)

print(f"\n{len(b.ADMIN_TOOLS)} admin tools registered:\n")
for i, t in enumerate(b.ADMIN_TOOLS, 1):
    fn = t.get("function", {})
    desc = (fn.get("description") or "").strip().splitlines()
    first = desc[0][:90] if desc else ""
    print(f"{i:3}. {fn.get('name', '?'):28} {first}")
