# Stage 1 — DOM Motor (perception + shortlist + execution; no Laya)

**Status:** CODE COMPLETE — live-system QA pending (2026-09-25).
**Enabled on this install** via `self_config.yaml` (`automation.dom_motor: true`,
owner request 2026-09-25); process restart required to pick it up.
**Flags:** `USE_DOM_MOTOR` (code default **false**), `DOM_MOTOR_SEND_POLICY`
(`confirm`), `DOM_MOTOR_SHORTLIST_K` (18), `DOM_MOTOR_MIN_SCORE` (0.55),
`BROWSER_CDP_PORT` (9222)

## What was built

| Piece | File | Notes |
|---|---|---|
| Motor core | `tools/dom.py` | aria snapshot parser, structural filter, lexical+role rank, K-cap shortlist, send gate, web + Windows backends |
| Windows source | `tools/uia.py::uia_nodes()` | same walk as `uia_locate`, raw candidates for the shortlist |
| Locate pre-track | `tools/vision.py` (`locate_ui_element_ex`) | Track -1 when the flag is on; returns None → legacy tracks unchanged |
| Web execution | `core/brain.py` `smart_click`/`smart_type` | web fast path before pyautogui; `confirm_send` schema param for the send gate |
| Browser launch | `tools/system.py::open_application` | Chromium-family names get `--remote-debugging-port` + `--remote-allow-origins=*` when the motor is on |
| Tests | `tests/test_dom.py` | 43 tests, 7 feature classes |
| Harness | `dom-motor-test.py` | 24 synthetic checks, no browser/models |

Safety: model output never becomes selectors/JS/coordinates; nodes are
re-resolved by role+accessible name at execution time; refs are never used for
execution; send-like clicks are refused until `confirm_send=true`.

## Predicted numbers (to falsify in live QA)

| Metric | Prediction | Measured |
|---|---|---|
| Fixture task pass rate | ≥ 95% | pending live QA |
| filter+rank latency, 500 nodes | < 20 ms | pending (pure-CPU, no model) |
| Web locate (snapshot + rank) | 100–300 ms | pending live QA |
| UIA locate via motor | **CORRECTED:** walk-budget-bound, 0.3–2 s typical / up to ~4 s with sparse-retry; was wrongly predicted 150–500 ms | 1.85 s shortlist / 3.16 s raw walk (subagent, live) |
| Idle VRAM change | 0 | 0 (no model loaded) |
| Ambiguous "Open" fixture | 2 candidates surfaced, `AMBIGUOUS` note on click | unit-tested |

## QA checklist (run every time)

> Browser setup note (QA round 2, F1): Chrome ≥136 ignores
> `--remote-debugging-port` on the default profile unless `--user-data-dir` is
> passed explicitly. `open_application` now passes the real profile dir, so
> **fully close Chrome/Edge before launching** it for a live run (profile
> lock); otherwise the launch hands off to the running instance and the port
> stays closed (`[DOM] CDP attach unavailable …; retry in 30s` is the tell).

```powershell
python engine_testing/qa/run_stage.py 1
python dom-motor-test.py          # 24/24 expected
python -m pytest tests/test_dom.py -v
```

Offline suites are the gate for code changes. Live checks require the owner's
desktop + a Chromium started with the debug port:

- [ ] fixture index.html: `smart_click("Submit button")` → returns `FAILED — BLOCKED`
      (default `confirm` policy), then `smart_click("Submit button", confirm_send=true)`
      → status becomes `submitted`
- [ ] fixture index.html: `smart_click("the search bar")` + `smart_type(...)` → field filled
- [ ] fixture ambiguous "Open": click reports `AMBIGUOUS`
- [ ] background browser (not focused): `smart_click` falls through to UIA/OCR, never clicks Chrome
- [ ] File Explorer: `smart_click("New folder button")` resolves via `dom-win`
- [ ] Notepad/Calculator: locate + type path still works
- [ ] flag OFF (default): behavior byte-identical to pre-change (fallback untouched)

## Exit criteria

- [x] 43/43 unit tests + 24/24 harness checks green
- [x] full pytest suite green (380 total)
- [ ] live fixture matrix ≥ 95% (owner-present run)
- [ ] `locator_eval` A/B captured for stage 2
- [x] subagent QA iterations complete (rounds 1–2 + final), findings recorded below

## QA findings — subagent round 1 (applied 2026-09-25)

Adversarial review found one flag-on blocker and several highs; all real ones
fixed in `tools/dom.py`, `tools/system.py`, `tools/uia.py`, `tools/vision.py`:

| # | Finding | Resolution |
|---|---|---|
| BLOCKER | Failed CDP attach leaked the Playwright driver and set a permanent failure flag — the entire web motor died for the process | `_attach_impl` stops the driver on failure and rate-limits retries (`_WEB_RETRY_S=30`); regression tests added |
| HIGH | Playwright sync API is thread-affine; execute_tool runs on different surface threads, so the cached singleton silently broke after the first thread | All Playwright work now runs on one dedicated `dom-web` worker thread with a job queue; public wrappers marshal through `_web_call` |
| HIGH | Web fast path could click a *background* browser tab while the owner was in another app | `_active_page_impl` returns only the page where `document.hasFocus()` is true; otherwise None → foreground UIA/OCR fall through |
| HIGH | `open_application` CDP launch used shell `start`, could not detect failure, and skipped the .lnk path | Resolves the exe via registry App Paths (`winreg`), launches `[exe, args]` without a shell, returns True only on actual start |
| MED | `USE_PIXEL_FALLBACK` had zero consumers | Stage 2 wires it |
| MED | BLOCKED send result wasn't matched by the failure guard | Now returns `FAILED — BLOCKED: ...` |
| MED | `is_send_like` missed Resend/Forward/Reply; invalid policy silently disabled the gate | Words added; `_send_policy()` validates and fails closed |
| MED | No Windows/launch/flag coverage; one tautological assert | 15 new tests (43 total) incl. lifecycle, focus gate, walk reuse, launch |
| MED | UIA walk ran twice per locate (motor + Track 0) | `windows_locate_cached` hands raw candidates to `uia_locate_candidates`; one walk per call |
| LOW | `[ref=…]` inside a quoted name could win over the real ref | Parser only reads refs after the quoted name |
| LOW | `_WEB_LOCK` lazy init racy | Module-level lock |

Docs drift found + fixed: harness count (24), fixture checklist gates `Submit`
correctly, `dom-win` added to the source contract. Docs sweep (CLAUDE.md /
summary.md / AGENTS.md / self_knowledge) deferred to Stage 4 per plan.

Independent verification after fixes:
`pytest tests/test_dom.py -q` → **43 passed** · `dom-motor-test.py` → **24/24**
· full suite → **330 passed** · `ADMIN_TOOLS` → 68 (unchanged).

## Live E2E — amazon.eg (2026-09-25) ✅

Command: `python engine_testing/e2e_dom_motor.py run --query ball`
(real Chromium over CDP, production `tools/dom.py` path, Laya kernel enabled).

| Step | Result |
|---|---|
| Open amazon.eg | real homepage, no bot challenge |
| `web_type("search box","ball")` | typed into `searchbox "Search Amazon.eg"`, score 1.05 |
| `web_click("Go")` | **Laya picked `#0 Go` (margin 0.79)** → Playwright click succeeded |
| Results | 60 cards |
| First result | *"Rubber Small Round Ball… Kids"* — **EGP 285.00** |

Artifact: `engine_testing/qa/artifacts/e2e_amazon_result.json`.

Two live defects found and fixed during the run:
1. `_web_click_on_page` tried a hidden keyboard-shortcut link
   (`Search, ALT, forward slash`) that scored high lexically → added
   `_is_visible` pre-check and made a failed click advance to the next ranked
   candidate instead of aborting (force-click no longer attempted).
2. Suite independence: with the kernel enabled live, tests were reading live
   config. Added `tests/conftest.py` pinning all automation knobs, and pinned
   the threshold in `system1-test.py`.

Note: llama-server was **down** during this run, so the main-LLM planning leg was
replaced by a scripted plan; the motor + Laya decisions are the parts measured.
