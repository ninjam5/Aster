---
name: aster-docs-and-writing
description: Load this when reading or updating any Aster documentation - deciding which doc to trust, which docs must be updated after a change, writing a feature spec or checklist, or making external claims about the project. Contains the trust-level map (which docs are living vs frozen vs stale), the karaoke-removal lesson, house style, templates, and pre-release claims discipline. Triggers - "update the docs", "is this doc accurate", "AGENTS.md says...", "write a spec", "README claims".
---

# Aster Docs and Writing

This repo has **no git history** — documentation IS the historical record, which
makes doc discipline unusually load-bearing here.

**When NOT to use this skill:** the change itself → `aster-change-control`; settled
technical history → `aster-failure-archaeology`.

## Trust-level map (verified 2026-07-05; Qwen-swap notes 2026-09-27)

### LIVING (must be updated when behavior changes)
| Doc | Role |
|---|---|
| root `CLAUDE.md` | THE working reference — richest, most current. Updated 2026-09-27 for the Qwen 3.6 engine swap (engine, model file, native tool calling). Update after any behavior change. Remaining stale spots as of 2026-07-05: the "smart_click Florence-2 description drift" gotcha (fixed in code), the "`awareness_mode: false` ignored" gotcha (awareness.py:29 now reads config), devmode line ref (687, not 668) |
| root `AGENTS.md` | General agent guidance; **rewritten 2026-09-27** for the Qwen swap (native OpenAI tool calling, BeeLlama engine, Qwen model, 69 tools). Keep current |
| `Aster-UI/CLAUDE.md`, `Aster-UI/README.md`, `Aster-UI/CONTRIBUTING.md`, `aster-face/README.md` | Frontend references — same duty |
| `self_config.example.yaml` / `secrets.example.yaml` | Public config templates — every new knob lands here first. Known stale spot: the awareness_mode comment still says "currently ignored" |
| `README.md` | External-facing (program-application framing) — see claims discipline below |
| `.claude/skills/*/SKILL.md` | This library — each has re-verification one-liners in its Provenance section |

### PLAN/SPEC (living while their work is open)
`aster-opensource-plan.md` (release phases + all-tools checklist),
`packaging.md` (launcher design, open questions), `emotions-next-steps.md`
(emotion roadmap — per-feature status/checklists), `Personality-systems.md`,
`ideas.md` (feature backlog with [IMPLEMENTED]/[DONE] markers),
`ideas-for-backend.md`, `open-jarvis.md` (graded external-idea mining).

### BASELINE (dated snapshot, partially stale — cite with the date)
`summary.md` — "State of the System" **2026-09-27** (engine section revised for the
Qwen 3.6 35B-A3B swap; original baseline 2026-05-20). Still the best source for
VRAM tables, frontend detail, per-tool tables. The engine/tool-calling/VRAM sections
are current; older prose may still cite the Gemma-era engine. Intervention threshold
in config is 60 s.

### STALE — do not trust (kept only as a caution)
None as of 2026-09-27: root `AGENTS.md` was rewritten in the Qwen swap (native OpenAI
tool calling, 69 tools, `secrets.yaml`, BeeLlama engine) and moved to LIVING above.
Any doc still claiming "XML-based ReAct" / "39 tools" / "hardcoded credentials in
config.py" predates that rewrite — check its date before trusting it. (The
`.github/instructions/summary-workflow.instructions.md` file referenced by old docs
still **does not exist**; only `python-global-packages.instructions.md` is present.)

### FROZEN point-in-time logs (NEVER rewrite — they are the git-history substitute)
`checklist_test.md` (2026-05-15 migration), `aster-ui-integration-log.md`,
`Aster-ui-integration.md`, `Aster-UI/polish-work.md`,
`engine_testing/results/*.txt`, `Aster_Vault/conversation-tests/*`.

## The living-vs-frozen rule (the karaoke lesson, 2026-07-03)

When a feature is removed or changed: sweep ALL living docs and specs, but
deliberately leave the frozen logs describing the old state. Consequence you must
expect: greps hit frozen logs for things that no longer exist (karaoke is the
canonical example). Check the file's class before "fixing" a stale mention.

## Update duty after a change

1. Root `CLAUDE.md` — the relevant subsystem section and/or Known Gotchas.
2. `summary.md` — only for architectural shifts (it's a baseline, not a changelog).
3. The owning spec doc — tick/extend its per-feature checklist.
4. Example yamls — any new knob, with a comment.
5. This skill library — any Provenance fact your change invalidates.

## House style (derived from root CLAUDE.md — the best exemplar)

- Dense reference prose; **gotcha-first** framing ("**Gotcha:** …" callouts).
- File:line anchors for everything claimable (`core/brain.py:324`).
- Tables for inventories; status markers `[IMPLEMENTED]`/`[DONE]`/`✅ BUILT
  (date)` on backlog items.
- Date-stamp volatile facts inline ("as of 2026-07-05").
- Per-feature testing checklists in specs (owner discipline — see template below).
- Bold the load-bearing nouns, not decoration.

## Templates

### Feature spec section (mirrors emotions-next-steps.md's shape)
```markdown
## <Feature> — <status>
**Aim.** <one paragraph — the felt behavior, not the mechanism>
**How it works.** <pipeline, gates, config keys, files touched>
**What shipped.** <files/functions/config/toggles>
**Testing checklist:**
- [ ] <observable behavior 1>
- [ ] <negative case: disabled → hard no-op>
- [ ] <negative case: cooldown/quiet-hours blocks>
**Possible derailments.** <pre-registered failure modes + the lever for each>
```

### Failure-archaeology entry
```markdown
## <Battle name> (<date>)
**Symptom:** … **Root cause:** … **Evidence:** <files/reports/measurements>
**Status:** SETTLED | OPEN | LIVE — <the rule or fence it produced>
```

### CLAUDE.md gotcha line
```markdown
- **<Thing> <verb-phrase>** — <one-sentence trap>, <where>, <what to do instead>.
```

### Removal sweep checklist
```markdown
- [ ] tools/ module + ADMIN_TOOLS schema + execute_tool branch + import
- [ ] config.py constants + both example yamls
- [ ] face_server endpoints + UI sections/routes/tests/mocks
- [ ] CLAUDE.md, summary.md, app CLAUDE.mds, spec docs, skills library
- [ ] frozen logs: LEFT ALONE (note the expected future grep hits)
```

## External claims discipline (pre-release)

- The load-bearing public claim is **"fully local — nothing leaves the machine"**
  (README). Any integration that transmits data must be flagged loudly in review
  and gated (see `aster-change-control` rule 10). Gmail/Calendar/Spotify/Telegram
  are owner-initiated integrations of the owner's own accounts — the claim means
  no third-party AI/telemetry backends.
- **Only measured numbers get published.** Grounded today (2026-09-27): Qwen 3.6
  35B-A3B + 60k ctx + KVarN ≈ 11.5–11.9 GB / 12.3 GB; the swap's harness battery
  (31/37 auto @ temp 1.0, 27/37 @ temp 0.7). NOT grounded: other model footprints
  (never recorded — packaging.md says so explicitly).
- Unshipped work is labeled open/candidate; the release plan's beta step
  ("do not skip") is the reproducibility bar before any public "it works"
  (`aster-opensource-plan.md` Step 6).

## Provenance and maintenance

Authored 2026-07-05.

- AGENTS.md current? `Select-String -Path AGENTS.md -Pattern "Qwen|native OpenAI"`
- Instructions dir contents: `Get-ChildItem .github\instructions`
- CLAUDE.md gotcha drift: `Select-String -Path CLAUDE.md -Pattern "Florence|awareness_mode"` then verify each against code
- Doc inventory: `Get-ChildItem -File *.md | Select-Object Name`
