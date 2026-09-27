---
description: "Use when implementing new features, fixing bugs, refactoring, adding endpoints, editing core modules, or making any significant code changes in the Aster project. Covers the summary.md workflow for context-efficient development."
name: "Summary.md Source of Truth"
applyTo: "**"
---

# Summary.md — Source of Truth (Hard Rule)

`summary.md` is the project's single source of truth for architecture, file inventory, and feature catalog.

**Hard rule: An outdated `summary.md` is worse than no `summary.md` at all.** Every code change must be reflected in `summary.md` before the task is considered complete. This is not optional.

## Before Starting Work

1. Read `summary.md` first — it contains the full folder structure, file descriptions, and feature list.
2. Use it to identify which specific files to read next, instead of scanning the whole workspace.
3. Only deep-read files relevant to your current task. `summary.md` tells you where everything lives.

## After EVERY Change — Non-Negotiable

Update `summary.md` **immediately** after completing any of the following. Do not batch updates. Do not defer to "later." The update is part of the change, not a follow-up.

| Change Type | What to Update in `summary.md` |
|---|---|
| **Add a feature** | Append a row to the relevant features table. List new files in Appendix C. |
| **Remove or deprecate a feature** | Delete or strike the entry. Mark as deprecated with reason. |
| **Create, rename, or move a file** | Update Appendix C file inventory with new path, line count, and purpose. |
| **Change configuration** | Update Section 1.4 config tables (model params, engine, context window, etc.). |
| **Alter architecture** | Update the relevant section. Add a subsection if the change introduces a new pattern (e.g., dual-engine, polyfill). |
| **Fix a bug or WIP item** | Mark the corresponding Section 4 item as ✅ FIXED with resolution summary. |
| **Add a new bug or WIP item** | Append to Section 4 with location, description, and severity. |
| **Change VRAM budget** | Update Section 3.3 table with new estimates. |
| **Change tool registry** | Update Appendix A tool table. |
| **Change thread architecture** | Update Appendix B. |

## Enforcement Checklist

Before marking any task complete, verify:

- [ ] `summary.md` reflects the **current state** of the codebase
- [ ] No stale line numbers, file paths, or parameter values remain
- [ ] New files appear in Appendix C
- [ ] Deprecated components are marked, not just deleted from docs
- [ ] Config changes are reflected in Section 1.4

## Keeping It Accurate

- If you notice stale information while reading `summary.md`, correct it even if your task didn't cause the drift.
- Keep entries concise — one line per file or feature row is enough.
- When in doubt, update. A slightly over-documented `summary.md` is always better than a stale one.
