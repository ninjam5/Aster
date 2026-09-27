---
name: aster-validation-and-qa
description: Load this when running tests, deciding whether a change is proven, adding tests for new code, interpreting a red suite, or asked "how do I test this", "what counts as evidence", "run the test suite", "is there a benchmark for X". Contains the full test inventory with counts measured 2026-07-05 (including one known-failing test), the evidence standards, and the how-to-add-tests patterns.
---

# Aster Validation and QA

**When NOT to use this skill:** a test fails and you're diagnosing the code →
`aster-debugging-playbook`; measurement tooling and scripts →
`aster-diagnostics-and-tooling`; whether the change may ship →
`aster-change-control`.

## Evidence standards (the house rules)

1. **Logic changes** → mocked-LLM pytest. No llama-server, no network, no models.
2. **ML-adjacent logic** (fusion rules, EMA smoothing, gates) → standalone mock
   harnesses that fake the classifier outputs (`emotion-test.py` pattern).
3. **Live behavior** → a per-feature manual checklist, written BEFORE shipping and
   mirrored into the spec doc (owner discipline; shape established in `ideas.md`
   and `emotions-next-steps.md`).
4. **Model behavior** (prompts, sampling, model swaps, tool reliability) → the
   harness batteries against a running llama-server; compared to a frozen
   baseline, never eyeballed.
5. **Frontend** → the standing bar: `npm run build` + `npm run lint` + `npm test`
   all green (Aster-UI/CLAUDE.md).
6. A pre-existing failure is REPORTED as pre-existing with evidence — never
   silently absorbed, never silently "fixed" as a side effect.

## Test inventory (counts measured 2026-07-05)

### Offline — no llama-server, safe anytime

| Suite | Covers | Run | Measured result |
|---|---|---|---|
| `tests/` (6 files: test_migration, test_mood_memory, test_intervention, test_sentiment, test_first_run_setup, test_config_cleanup) | REST adapter + native/XML tool parsing, mood-trend logic, `classify_window`, `classify_sentiment`, setup wizard, config hygiene | `python -m pytest tests/ -q` | **118 passed, 1 FAILED** |
| Known failure | `test_first_run_setup.py::TestUpdateIdentity::test_preserves_comments_and_untouched_fields` | — | Pre-existing on 2026-07-05; treat as known-red baseline until fixed |
| `emotion-test.py` | Mood-trigger features (Ideas 1 & 2 gates, streaks, cooldowns, wording) | `python emotion-test.py` | **19/19** |
| `ambient-audio-test.py` | Tier-1b mapping, EMA, owner gate, call-pause, disabled no-op | `python ambient-audio-test.py` | **23/23** |
| `face-emotion-test.py` | Tier-2 mapping, EMA, confidence gate, fusion policy | `python face-emotion-test.py` | **22/22** |

Single test: `pytest tests/test_migration.py -v -k "test_name"`.

### Frontend (Aster-UI)

```powershell
cd Aster-UI
npm test          # vitest — 34 tests (2026-07-04 count)
npm run build     # tsc + vite bundle check
npm run lint
```
All three green = the standing bar. Single file:
`npx vitest run src/onboarding/OnboardingFlow.test.tsx`.

### Requires a running llama-server

| Battery | What it measures | Run | Caveat |
|---|---|---|---|
| `engine_testing/run_engine_test.py` | Tool selection, arg precision, multi-step, restraint, hallucination resistance, cutoff routing (auto-scored) + persona/two-face/Discord-relay/vision (manual grading) → timestamped report in `engine_testing/results/` | `python engine_testing/run_engine_test.py` (options: `--model NAME`, `--category persona`, `--no-vision`) | **Exercises the LEGACY XML path only** — harness.py never sends `tools=`. There is NO automated native-path battery yet (first obligation of `aster-gemma-reliability-campaign`) |
| `conversation_testing.py` | Persona speaking style across sampling presets (side-by-side matrix) | `python conversation_testing.py [--presets …] [--prompts …] [--persona jarvis]` | Output is human-judged by design (style has no auto-metric yet); writes md+json to `Aster_Vault/conversation-tests/` |

### Golden / frozen inventory

- `engine_testing/results/*.txt` — June 2026 model sweep (E2B-QAT, E4B-QAT,
  E4B-q4km, 12B variants). Latest (2026-06-24): auto **32/32**, 90.2 tok/s,
  4.98 s/turn, 0% XML mangle on 84 calls. THE baselines for any model/template
  change. Never rewrite them.
- `Aster_Vault/conversation-tests/` — style-sweep records.
- Manual regression batteries: `aster-opensource-plan.md` "Testing Checklist — All
  58 Tools" (NOTE: predates Gmail/Calendar; live count is 68) and
  `checklist_test.md` (2026-05-15 migration checklist, historical).

## How to add tests

### Pattern 1 — mocked-LLM pytest (from `tests/test_migration.py`)

The whole suite mocks `requests.post` and fabricates OpenAI-shaped responses:

```python
from unittest.mock import MagicMock, patch

def _make_mock_response(message: dict):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": message}]}
    mock_resp.raise_for_status.return_value = None
    return mock_resp

# native tool-call message shape:
msg = {"role": "assistant", "content": None,
       "tool_calls": [{"id": "call_1", "type": "function",
                       "function": {"name": "get_current_time", "arguments": "{}"}}]}
with patch("core.brain.requests.post", return_value=_make_mock_response(msg)):
    ...  # exercise the code path
```

Test BOTH paths when touching tool calling (native `tool_calls` and legacy XML
strings through `_legacy_xml_parse`).

### Pattern 2 — standalone mock harness (for ML-adjacent logic)

Copy the shape of `emotion-test.py`/`face-emotion-test.py`: stub the classifier
functions with canned label/score outputs, drive the real gate/fusion/EMA code,
print `[PASS]/[FAIL]` per check and a final `N/N checks passed`. Zero model
downloads, zero llama-server. Every new opt-in feature ships with one (all four
emotion tiers did).

### Pattern 3 — per-feature manual checklist

`### Feature N — <name>` with its own checkbox list, one observable behavior per
box, including the negative cases (cooldown blocks, disabled = hard no-op, quiet
hours). Mirror it into the spec doc. Examples to copy: `ideas.md` §2/§3/§7,
`emotions-next-steps.md` per-tier checklists.

## Acceptance-threshold discipline

Behavioral thresholds are config levers with documented tradeoffs, never magic
numbers: `EMOTION_MIN_CONFIDENCE` (0.5; raise to 0.6 iff energetic-speech false
positives annoy), ECAPA `SIMILARITY_THRESHOLD` 0.35 / `LEARN_THRESHOLD` 0.50 /
`MIN_LEARN_SECONDS` 2.5, `WAKE_WORD_THRESHOLD` 0.5. Changing one requires either a
labeled mini-set or a logged week of outcomes — one anecdote is not evidence
(method: `aster-research-methodology` recipe d).

## Provenance and maintenance

Authored 2026-07-05; all counts measured that day on the live machine.

- Re-measure pytest: `python -m pytest tests/ -q` (baseline 118 pass / 1 fail)
- Re-measure harnesses: `python emotion-test.py; python ambient-audio-test.py; python face-emotion-test.py`
- Vitest count: `cd Aster-UI; npm test`
- Legacy-only harness still true: `Select-String -Path engine_testing\harness.py -Pattern "_legacy_xml_parse|tools="`
- Latest golden report: `Get-ChildItem engine_testing\results | Sort-Object LastWriteTime | Select-Object -Last 1`
