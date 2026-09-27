---
description: "Use when implementing new features, fixing bugs, refactoring, adding endpoints, editing core modules, or making any significant code changes. Covers documentation-first approach to ensure correct syntax and API usage."
name: "Read Documentation First"
applyTo: "**"
---

# Documentation-First Development

**Hard rule**: Always read relevant documentation before applying code changes.

## Before Any Code Change

1. **Read `summary.md`** — It is the project's source of truth. It tells you where everything lives, what the current architecture is, and what's already been tried.
2. **Read official docs** — Check the library/framework's official documentation for current syntax, API signatures, and usage patterns.
3. **Verify syntax** — Ensure the code you're writing uses up-to-date syntax (not deprecated patterns).

## After Any Code Change

4. **Update `summary.md`** — See `summary-workflow.instructions.md` for the full checklist. This is a hard rule, not a suggestion.

## Research Checklist

- [ ] `summary.md` consulted for current architecture state
- [ ] Official library/framework docs reviewed
- [ ] Current API signatures verified (not relying on memory)
- [ ] Deprecated patterns avoided
- [ ] `summary.md` updated to reflect the change

## Why This Matters

- APIs change between versions — cached knowledge may be outdated
- `summary.md` encodes team decisions, conventions, and past migration results
- Reading first prevents debugging wrong assumptions later
- An outdated `summary.md` is worse than no `summary.md` at all
