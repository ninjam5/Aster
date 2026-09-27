---
name: aster-change-control
description: Load this BEFORE making any code change to the Aster repo, when planning a feature, adding/removing a tool, adding a config flag or ML model, removing a feature, or when asked "is this change safe / how do I ship this here". Defines how changes are classified and gated, the project's non-negotiables with the incident behind each, and the before-done checklist. Also load when reviewing someone else's change.
---

# Aster Change Control

Aster is the owner's **live daily assistant**, not a demo repo. The prime directive
is: never break the daily driver. Everything below exists to protect that.

**When NOT to use this skill:** diagnosing a failure → `aster-debugging-playbook`;
running tests → `aster-validation-and-qa`; understanding why an invariant exists →
`aster-architecture-contract`.

## Non-negotiables (each with the incident behind it)

| # | Rule | Why / incident |
|---|---|---|
| 1 | **Risky core changes ship behind a rollback flag.** Pattern: `config.USE_NATIVE_TOOL_CALLS` — the XML→native tool-calling migration kept the entire legacy path alive behind one boolean. | The 2026-05 engine migration hell (three failed architectures) taught that LLM-behavior changes can look fine and fail live. A flag turns a bad night into a one-line revert. |
| 2 | **Never touch the LLM adapter, the chat template (`Aster_Vault/gemma4-multimodal.jinja`), or the tool-calling path without harness evidence.** Baseline first, A/B after (see `aster-gemma-reliability-campaign` Phase 0). | Three failed tool-calling architectures (ChatML clash, Llava15ChatHandler echo loop, silent system-role drop) each burned days. |
| 3 | **VRAM budget gate**: any change adding/moving a model must report measured idle + active VRAM against the budgets (idle ~6 GB on E4B / ~4 GB on E2B; 12 GB ceiling). New models are lazy + ref-counted, not eager. | Owner-stated 2026-07-05. Past leaks: per-call duplicate Whisper/Kokoro loads, base64 history bloat. See `aster-vram-discipline`. |
| 4 | **No virtual environments.** Global pip only: `python -m pip install <pkg>`. | Enforced by `.github/instructions/python-global-packages.instructions.md`. |
| 5 | **Credentials only in `secrets.yaml`** (gitignored). Never in `config.py`, `self_config.yaml`, or any tracked file. Never read the real yaml files in a session — use the `.example.yaml` templates. | `self_config.yaml` is exposed verbatim to the LLM via `get_my_config`; secrets use a deliberately separate loader so they can never leak through introspection (config.py:48-56 comments). |
| 6 | **Proactive/surveillance-adjacent features default OFF** and are debounced (sustained-mood gates, cooldowns, streak guards). | Established by the emotion roadmap: mood check-ins, ambient actions, ambient audio, face emotion all shipped opt-in default OFF so Aster "never feels like it's surveilling". |
| 7 | **Persona files are owner-authored.** `Aster_Vault/System_Prompts/*.md` content and the custom voice tensors: wire them, never rewrite their substance. Never put a tool list inside a persona file. | Owner-stated. The XML tool manual is appended by code only in the legacy path. |
| 8 | **Multi-feature plans need per-feature testing checklists**, mirrored into the spec doc. | Owner-stated discipline (2026-05); see the shape in `ideas.md` and `emotions-next-steps.md`. |
| 9 | **Every optional integration fails closed.** Missing credential ⇒ `*_AVAILABLE=False` ⇒ graceful no-op, never a crash. New integrations must follow this shape. | The pattern is uniform across Spotify/Telegram/Discord/LiveKit/Google in config.py. |
| 10 | **Outward-facing actions are gated.** Anything that sends (email, calendar invites with guests) routes through the tier system + single pending-approval slot (`tools/google_auth.py send_policy`). Never add a tool that silently transmits. | "Data never leaves the machine / auditable gated tool use" is the project's load-bearing external claim (README). |

## Change taxonomy and per-class gates

### A. Adding or modifying an admin tool
The **three-edit rule** (all three or the tool half-exists):
1. Schema dict appended to `ADMIN_TOOLS` (`core/brain.py:396`) — already full
   OpenAI JSON-Schema `{"type":"function","function":{...}}` shape.
2. `elif` dispatch branch in `execute_tool()` (`core/brain.py:1604`).
3. Implementation in the right `tools/` module + import at top of `brain.py`.

Gates: description must match the implementation (description drift is a documented
past bug class); if outward-facing → rule 10; verify registration:
`python -c "import core.brain as b; print(len(b.ADMIN_TOOLS))"` (68 as of
2026-07-05, boot log prints the count). Discord brain tools are a separate 3-tool
allowlist — do not add admin tools there.

### B. Core brain / adapter / template / memory changes
Gates: rollback flag (rule 1); harness baseline + A/B (rule 2); the full pytest
suite; respect every invariant in `aster-architecture-contract` (single adapter
return shape, messages[0], two-brain isolation, media purge).

### C. New config flag
Follow the add-a-flag checklist in `aster-config-and-flags` (both example yamls +
`_cfg()` read + consumer + optional toggle + docs). Experimental flags default to
the safe/off value.

### D. New ML model
Lazy + ref-counted (copy the `tools/audio.py` acquire/release shape), VRAM measured
before/after (rule 3), loading workarounds documented (SpeechBrain/hsemotion have
history — see `aster-failure-archaeology`).

### E. New daemon / proactive feature
Default OFF (rule 6), toggle tool + Telegram command, debounce/cooldown, respects
the brain-busy semaphore if it calls the LLM, per-feature checklist (rule 8), mock
test harness that runs without llama-server (established pattern:
`emotion-test.py`, `ambient-audio-test.py`, `face-emotion-test.py`).

### F. Frontend (Aster-UI / aster-face)
Standing bar (from `Aster-UI/CLAUDE.md`): `npm run build`, `npm run lint`, and
`npm test` must ALL pass before a change is done. Animations: smooth transitions
(~200ms), never teleport/hard-cut. Vitest count was 34 (2026-07-04).

### G. Feature removal (worked example: Karaoke, 2026-07-03)
Sweep ALL live code and ALL living docs (CLAUDE.md, summary.md, app CLAUDE.mds,
plan docs, config examples, frontend routes/tests). Deliberately do NOT rewrite
dated point-in-time logs (`engine_testing/results/*.txt`, integration logs) — they
are the project's substitute for git history (this repo has **no commits**).
Expect future greps to still hit the dated logs; that is correct.

### H. Docs changes
See `aster-docs-and-writing` (which docs are living vs frozen; update duty).

## Before-you're-done checklist (run it, don't vibe it)

```powershell
# 1. Offline test suites (no llama-server needed) — baseline 2026-07-05:
#    pytest: 118 passed, 1 known-failing (test_first_run_setup.py::TestUpdateIdentity::test_preserves_comments_and_untouched_fields)
python -m pytest tests/ -q
python emotion-test.py        # 19/19
python ambient-audio-test.py  # 23/23
python face-emotion-test.py   # 22/22

# 2. Tool registry intact (only if you touched brain.py):
python -c "import core.brain as b; print(len(b.ADMIN_TOOLS))"   # expect 68 +/- your change

# 3. Frontend bar (only if you touched Aster-UI):
cd Aster-UI; npm run build; npm run lint; npm test

# 4. VRAM gate (only if you touched models):
nvidia-smi --query-gpu=memory.used,memory.total --format=csv

# 5. No secrets in tracked files:
Select-String -Path config.py -Pattern "token|secret|api_key" -CaseSensitive:$false
#    → hits must be _secret() reads or comments only.
```

Then: update root `CLAUDE.md` if behavior changed; update the affected skill's
"Provenance" facts; per-feature checklist filled if this was a feature.

Do not mark done on a red suite. A pre-existing failure (like the known
first_run_setup one) is reported as pre-existing, with evidence — not silently
absorbed into your change.

## Provenance and maintenance

Authored 2026-07-05 against live code and owner Q&A of the same date.

- Three-edit anchors: `Select-String -Path core\brain.py -Pattern "^ADMIN_TOOLS|^def execute_tool"`
- Rollback flag: `Select-String -Path config.py -Pattern "USE_NATIVE_TOOL_CALLS"`
- Test baseline: `python -m pytest tests/ -q` (118 pass / 1 known fail on 2026-07-05)
- Frontend bar text: `Select-String -Path Aster-UI\CLAUDE.md -Pattern "standing bar"`
