"""emotion-test.py — standalone mock test for mood-triggered features (Ideas 1 & 2).

Runs WITHOUT llama-server. It stubs the LLM/delivery seams (the brain call and
Telegram/LiveKit push) and drives synthetic mood streaks + quiet/active timing to
verify:

  * Idea 2 (tools/mood_actions.py)  — inline ambient-action OFFERS
  * Idea 1 (tools/awareness.py)     — proactive mood check-ins
  * the shared streak-guard + touch-cooldown coordination
  * the opt-in toggles

Usage:  python emotion-test.py        (exit code 0 = all passed)
"""

import os
import sys
import tempfile
import time

# ── Import config with emotion disabled so the eager text-model load is skipped,
#    then re-enable the feature flags we need for the logic under test. ──────────
import config
config.EMOTION_ENABLED = False
import tools.emotion_recognition as er          # noqa: E402
import tools.mood_actions as ma                 # noqa: E402
import tools.awareness as aw                    # noqa: E402

# Re-enable + opt-in, redirect the durable log to a temp file, set initiative.
config.EMOTION_ENABLED = True
config.MOOD_TREND_ENABLED = True
config.MOOD_ACTIONS_ENABLED = True
config.MOOD_CHECKIN_ENABLED = True
config.MOOD_SUSTAIN_TURNS = 3
config.MOOD_ACTIONS_COOLDOWN = 0.0             # isolate streak logic (cooldown tested explicitly)
config.MOOD_CHECKIN_QUIET_SECONDS = 90
config.MOOD_TOUCH_COOLDOWN = 120
config.INITIATIVE_LEVEL = 2                     # mood_checkin capability = True at >= 2
config.MOOD_LOG_PATH = os.path.join(tempfile.mkdtemp(), "emotion_log.jsonl")

# ── Stub the delivery seam so no real brain / Telegram / LiveKit is touched. ────
_pushed: list[str] = []
aw._push_nudge = lambda nudge: _pushed.append(nudge)
# Force budget available by default (overridden per-case where needed).
_budget_ok = {"v": True}
aw._budget_remaining = lambda: _budget_ok["v"]


# ── Test harness ───────────────────────────────────────────────────────────────
_results: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    _results.append((bool(cond), label))
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")


def reset(mood_seq=(), quiet_seconds: float | None = None, budget: bool = True) -> None:
    """Reset all mood state, then optionally build a streak + set last-msg timing."""
    er._mood_window.clear()
    er._streak_id = 0
    er._streak_mood = None
    er._last_touch = None
    ma._last_action_streak = -1
    ma._last_action_time = 0.0
    aw._last_checkin_streak = -1
    aw._recent_user_msgs.clear()
    _pushed.clear()
    _budget_ok["v"] = budget
    for m in mood_seq:
        er.log_mood_turn(m, f"ctx {m}")
    if quiet_seconds is not None:
        # Simulate Mohamed's most recent message being `quiet_seconds` ago.
        aw._recent_user_msgs.append((time.time() - quiet_seconds, 10, False))


# ── Idea 2 — inline ambient-action offers ──────────────────────────────────────
print("\nIdea 2 — mood_actions.maybe_mood_action_nudge()")

reset(("sad", "sad", "sad"))
n = ma.maybe_mood_action_nudge()
check(n is not None and "calm" in n.lower() and "volume" in n.lower(),
      "sad streak -> offers calming music + volume")

reset(("frustrated", "frustrated", "frustrated"))
n = ma.maybe_mood_action_nudge()
check(n is not None and ("distract" in n.lower() or "close" in n.lower()),
      "frustrated streak -> offers to close distraction")

reset(("happy", "happy", "happy"))
n = ma.maybe_mood_action_nudge()
check(n is not None and "hype" in n.lower(),
      "happy streak -> offers hype playlist")

reset(("neutral", "neutral", "neutral"))
check(ma.maybe_mood_action_nudge() is None, "neutral streak -> no offer")

reset(("sad", "sad"))
check(ma.maybe_mood_action_nudge() is None, "only 2 sad (not sustained) -> no offer")

# Debounce: same streak fires at most once.
reset(("sad", "sad", "sad"))
first = ma.maybe_mood_action_nudge()
second = ma.maybe_mood_action_nudge()
check(first is not None and second is None, "same streak -> offered once, not twice")

# A genuinely new streak (different mood) fires again.
er.log_mood_turn("frustrated", "ctx"); er.log_mood_turn("frustrated", "ctx"); er.log_mood_turn("frustrated", "ctx")
check(ma.maybe_mood_action_nudge() is not None, "new distinct streak -> offers again")

# Cooldown blocks even a new streak when within the window.
reset(("sad", "sad", "sad"))
config.MOOD_ACTIONS_COOLDOWN = 9999
check(ma.maybe_mood_action_nudge() is not None, "cooldown test: first offer fires")
er.log_mood_turn("happy", "ctx"); er.log_mood_turn("happy", "ctx"); er.log_mood_turn("happy", "ctx")
check(ma.maybe_mood_action_nudge() is None, "cooldown blocks a new streak within the window")
config.MOOD_ACTIONS_COOLDOWN = 0.0


# ── Idea 1 — proactive check-ins ────────────────────────────────────────────────
print("\nIdea 1 — awareness._maybe_trigger_mood_checkin()")

reset(("sad", "sad", "sad"), quiet_seconds=200)
aw._maybe_trigger_mood_checkin()
check(len(_pushed) == 1, "sad streak + quiet -> one check-in pushed")
check(len(_pushed) == 1 and "company" in _pushed[0].lower(),
      "sad check-in text is a gentle company offer")

reset(("happy", "happy", "happy"), quiet_seconds=200)
aw._maybe_trigger_mood_checkin()
check(len(_pushed) == 1 and "energy" in _pushed[0].lower(),
      "happy streak + quiet -> celebrate nudge")

reset(("sad", "sad", "sad"), quiet_seconds=10)        # still actively chatting
aw._maybe_trigger_mood_checkin()
check(len(_pushed) == 0, "sad streak but ACTIVE (not quiet) -> no check-in")

reset(("sad", "sad", "sad"), quiet_seconds=200, budget=False)
aw._maybe_trigger_mood_checkin()
check(len(_pushed) == 0, "budget exhausted -> no check-in")

reset(("sad", "sad", "sad"), quiet_seconds=200)
aw._maybe_trigger_mood_checkin()
aw._maybe_trigger_mood_checkin()
check(len(_pushed) == 1, "same streak -> checks in at most once")

reset(("neutral", "neutral", "neutral"), quiet_seconds=200)
aw._maybe_trigger_mood_checkin()
check(len(_pushed) == 0, "neutral streak -> no check-in")


# ── Shared guard — the two systems don't double-nudge ───────────────────────────
print("\nShared streak-guard")

reset(("sad", "sad", "sad"), quiet_seconds=200)
offer = ma.maybe_mood_action_nudge()        # Idea 2 fires + records a touch
aw._maybe_trigger_mood_checkin()            # Idea 1 should be suppressed by touch cooldown
check(offer is not None and len(_pushed) == 0,
      "Idea 2 just fired -> Idea 1 suppressed within touch cooldown")


# ── Toggles off ─────────────────────────────────────────────────────────────────
print("\nToggles (opt-out)")

config.MOOD_ACTIONS_ENABLED = False
reset(("sad", "sad", "sad"))
check(ma.maybe_mood_action_nudge() is None, "MOOD_ACTIONS_ENABLED=False -> no offer")
config.MOOD_ACTIONS_ENABLED = True

config.MOOD_CHECKIN_ENABLED = False
reset(("sad", "sad", "sad"), quiet_seconds=200)
aw._maybe_trigger_mood_checkin()
check(len(_pushed) == 0, "MOOD_CHECKIN_ENABLED=False -> no check-in")
config.MOOD_CHECKIN_ENABLED = True


# ── Summary ─────────────────────────────────────────────────────────────────────
passed = sum(1 for ok, _ in _results if ok)
total = len(_results)
print(f"\n{'=' * 60}\n{passed}/{total} checks passed")
if passed != total:
    print("FAILURES:")
    for ok, label in _results:
        if not ok:
            print(f"  - {label}")
    sys.exit(1)
print("All mood-trigger checks passed.")
sys.exit(0)
