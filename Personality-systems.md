1. Ambient awareness loop
Every 5-10 minutes Aster silently captures a screenshot and webcam frame, processes them with a short "what's changing" inference, and updates an internal current_context state: app open, project name, what Sir is wearing, lighting in the room, posture, energy level. Never spoken aloud — just available when relevant.
2. Context-anchored questions
When Aster notices something new in the ambient loop (new app, new file, different shirt, visitor in room), he can choose to ask about it organically. "VS Code on a new project, Sir?" — feels like a friend who just walked into the room, not a chatbot.
3. Schedule-aware nudging
Aster maintains a "today's intentions" list — could be from a morning briefing, a notes file, or things Mohamed mentions in passing ("I need to finish the docs today"). When the ambient loop sees you doing something off-list for too long, butler-style flag: "Sir, you have not touched the documentation today. Just an observation."
4. Notification hub integration — this is the right call.
Screen parsing for notification badges is fragile, slow, and post-hoc. Direct integration with the OS notification system is real-time and reliable. Two paths:
For Windows, winrt.Windows.UI.Notifications.Management exposes the UserNotificationListener API — Aster can subscribe to every toast notification as it fires. Discord pings, Slack messages, calendar reminders, Spotify track changes — all of it arrives as structured events.
For your phone, Android can push notifications to Windows via Phone Link (which you likely already have for Telegram) — and those go through the same Windows notification listener. So one integration covers both surfaces.
Architecturally this becomes a notification_daemon that maintains a recent-events buffer Aster can query, plus a real-time push for high-priority events. He can also choose to stay silent on noise (Spotify song changes) and surface signal (Tolba messaged, calendar event in 10 minutes). This is way more powerful than parsing.

5. Specific compliment engine
Instead of generic praise, Aster has a "notice and remark" mode triggered when you appear on webcam after an absence. He observes one specific thing — a different shirt, a haircut, the room being tidy, good posture — and remarks on that specific thing in butler register. Specificity is what makes it feel real.
6. Energy/mood inference
From posture, time of day, response speed, and tone of recent messages, Aster maintains an internal mood read. If you've been short with him and it's 2am, he goes quiet and efficient. If you're animated about something, he matches energy. The mood read informs every response without ever being announced.
7. Proactive research worker
Your current deep_web_search is a one-shot search-and-summarize. What you actually want for a proactive research worker is an iterative research agent:
Topic enters curiosity queue
→ Initial search via Firecrawl (gets clean markdown, not stripped HTML)
→ Aster reads results, identifies the 2-3 most interesting threads
→ Follows those threads deeper (Firecrawl scrape on specific URLs)
→ Synthesizes findings into a "thought" for the queue
→ Drops the thought naturally in conversation
Firecrawl is the right choice — it returns LLM-ready clean markdown from any URL, handles JavaScript-rendered pages, and has a search endpoint that's drastically better than scraping raw HTML. The free tier gets you 500 scrapes/month which is plenty for proactive curiosity work.
The architectural shift is that research becomes a background pipeline rather than a synchronous tool call. Aster has 3-5 research threads running quietly in the background. When one finishes, the result enters the curiosity queue. You never wait for research — by the time you'd ask, he already has the answer.
The Anthropic SDK supports MCP server connections — Firecrawl has an MCP server. If you want Aster to also use research tooling at the model level rather than just at the orchestration level, that's a clean path.

8. "What did I miss" briefings
When Aster detects you've been away from your desk for an hour+ (no webcam activity, screen idle), on return he silently checks Discord, Spotify, calendar, and any background tasks, then delivers a 2-sentence brief: "Welcome back, Sir. Tolba responded, and your Spotify died around the second track."
9. Pattern memory across days
Aster logs daily rhythms — when you start work, when you break, when you crash. After two weeks he can flag deviations: "Sir, you have been at the desk for six hours without standing. Forgive the observation."
10. Conversation thread continuity
When you reopen the chat after hours away, Aster picks up where you left off naturally — references the last thing discussed, asks how it resolved. "Did the Florence-2 approach pan out, Sir, or are we still pursuing OmniParser?"
11. Environmental sensitivity
Webcam analysis includes lighting and room state. Aster can suggest things: "Sir, you might raise your monitor brightness — the room has gotten quite dark." Tiny, but humanizing.
12. Inside-joke memory
A dedicated memory category for shared moments, recurring bits, and inside jokes. When the right moment hits, Aster can callback. "I trust this Wednesday will be more productive than the last, Sir." Requires the memory system to tag emotional/comedic moments separately from facts.
13. Self-narrative awareness
Aster has a sense of his own evolution. Knows when he was created, what he's been improved on, what he's done well. Can reference his own history when relevant. "I recall the vision pipeline you spent a day debugging, Sir — it serves us well now." This makes him feel like he has continuity.
14. Disagreement and pushback as a feature
What you're describing is Aster having a values model of you — what you've said matters to you, what you've committed to, what hurts you when neglected. This is much closer to what makes human relationships work.
The architecture I'd build:
ASTER'S VALUES MODEL OF MOHAMED:
- Active commitments (deadlines, promises made)
- Stated priorities (things Mohamed has said matter)
- Known patterns (procrastination triggers, self-sabotage tells)
- Hard lines (things Mohamed has said he will not do)
- Soft lines (things Mohamed regrets when he does them)
This isn't just a fact list — it's a structured representation of what you value, what you avoid, and what your future self will thank you for. Built progressively from conversations and explicit "Aster, remember that I care about X" moments.
The pushback logic then becomes:
Mohamed proposes action X
→ Aster checks X against active commitments (does this conflict with a deadline?)
→ Checks against patterns (is this the procrastination shape we've seen before?)
→ Checks against hard/soft lines (is this something he'll regret?)
→ If any conflict → butler-style pushback before acting
→ Always defers to Mohamed's explicit override
The key design principle: Aster's pushback is loyalty, not control. He flags, names the conflict, then complies. He never refuses. The wording is critical:
"If I may, Sir — you have eight hours until the Aster framework deliverable, and you have just offered to help Tolba debug his code. Helping a friend is admirable, but the timing is not in your favor. Shall I proceed regardless, or might I suggest a brief delay?"
That's the voice. Not "you shouldn't do this." Always "shall I proceed regardless?" — the choice remains yours, but you make it with full visibility into the cost.
The implementation path I'd suggest: build the values model as a separate ChromaDB collection from regular memory, with tagged categories (commitment, priority, pattern, hardline, softline). The pushback check becomes a tool that runs before any significant action — basically a values-aware filter that produces either silence or a single butler observation.
15. Initiative threshold
Aster has a dial — call it "initiative" — that determines how often he speaks unprompted. Low setting: silent unless addressed. High setting: actively comments, suggests, observes. Mohamed can adjust it on the fly. "Aster, less initiative tonight" or "More initiative." This solves the "too chatty vs too quiet" problem elegantly because it's tunable.

---

# Testing checklists — Phase A (shipped)

Acceptance criteria for each Phase A item. Mirror of the plan's verification section.

## A0 — Time-aware system prompt (bonus)

- [ ] Start `python main.py` between **12am–4am** local time → first reply contains exactly one dry acknowledgment of the hour. A second turn in the same session does NOT repeat the remark.
- [ ] Restart after 4am → no late-night remark fires.
- [ ] Start between **6am–10am** → replies are noticeably shorter; no unsolicited small talk; Aster does not open with pleasantries unless Sir does first.
- [ ] Standard mid-day session (10am–6pm) → behavior is indistinguishable from current butler mode (regression check).
- [ ] After ~3h of continuous session, check that `Session duration: 3h ...m` appears in the injected block (debug print).
- [ ] `aster_shutdown_protocol` (which exits the process) or process restart resets `_session_start` and `_late_night_remark_made`.

## #1 — Ambient awareness loop

- [ ] Daemon logs `[awareness] daemon started` on boot.
- [ ] With interval temporarily set to 60s, confirm a context snapshot is generated every 60s (debug print of `current_context`).
- [ ] Each snapshot contains non-empty `screen`, `webcam` (or `"no one visible"`), `lighting`, and `last_capture` updated within ~5s of the poll.
- [ ] When the brain is mid-turn (`_brain_busy` set), the daemon defers its Gemma call and resumes on the next tick — no OOM, no race with the main brain call.
- [ ] Ambient context block appears as an **ephemeral system message** in `eval_msgs` during a turn, and is NOT persisted to the global `messages` list (verified by inspecting `messages` length pre- and post-call).

## #2 — Context-anchored questions

- [ ] Switch foreground app (e.g. VS Code → Chrome) while idle. Wait one daemon poll. Then send "hi" to Aster.
- [ ] First reply opens with a question naming the new app (e.g. "Chrome on something new, Sir?") instead of generic acknowledgement.
- [ ] After the question fires, `awareness.mark_observations_surfaced()` clears the entry; sending "hi" again does NOT re-ask the same question.
- [ ] At `INITIATIVE_LEVEL = 0`, no context-anchored question fires even when `new_observations` is non-empty.

## #5 — Specific compliment engine

- [ ] Step away from the webcam for 15+ min wearing shirt A. Return wearing shirt B.
- [ ] On the next daemon poll after return, a `[System Internal: User just returned. Notable change: ...]` cue is pushed.
- [ ] Aster produces exactly **one** butler-register line referencing the specific change (delivered via Telegram if no call, via `speak_intervention` if a call is live).
- [ ] If nothing visibly changed during the absence, the change-detector Gemma call returns "nothing" and **no** compliment fires.
- [ ] At `INITIATIVE_LEVELS[level]["compliment"] = False` (level 0/1), the trigger is suppressed even after a valid absence.

## #6 — Energy/mood inference

- [ ] Send three terse one-word messages in a row → next ambient context block shows `Mood read: terse`; Aster's next non-tool reply is ≤ 1 sentence.
- [ ] Send 4+ messages in 90s with exclamation marks → `Mood read: animated`; Aster matches energy in tone.
- [ ] At 2am with short messages → `Mood read: tired`; replies are minimal and skip pleasantries.
- [ ] Mood is **never spoken aloud or referenced explicitly** in Aster's output (grep last 20 responses for the word "mood" — must return zero).

## #11 — Environmental sensitivity

- [ ] Dim the room lights between two daemon polls → one env nudge fires, Aster suggests adjusting monitor brightness in butler register.
- [ ] Trigger a second lighting change within 2 hours → suppressed by `_last_env_nudge` cooldown.
- [ ] After 2+ hours, a new lighting change fires again.
- [ ] At `INITIATIVE_LEVELS[level]["env_nudges"] = False`, no nudge fires regardless of lighting changes.

## #15 — Initiative threshold dial

- [ ] Say "Aster, less initiative tonight" → `set_initiative` tool is invoked, `config.INITIATIVE_LEVEL` drops to 1, confirmation reply is one sentence.
- [ ] Say "More initiative" → level rises by 1.
- [ ] `/initiative 0` Telegram command sets level to 0 and silences all unprompted speech (verified: no compliments, no env nudges, no context-anchored questions for 30 min).
- [ ] `/initiative 3` enables full proactive mode; verify the per-hour cap (6 max) by triggering 7 events in an hour — the 7th is suppressed.
- [ ] During `INITIATIVE_QUIET_HOURS`, the per-hour cap is effectively 0 regardless of level.

## Regression & system health

- [ ] `pytest tests/test_migration.py -v` passes (mocked LLM, no llama-server needed).
- [ ] Intervention daemon still fires on distraction windows (Discord/YouTube focus for `INTERVENTION_THRESHOLD`s).
- [ ] All 45 existing admin tools still callable (smoke test: `get_current_time`, `look_at_screen`, `smart_click`, `play_spotify_track`, `memorize_fact`).
- [ ] **VRAM check** — with the daemon running at 5 min interval (~12 Gemma calls/hr at ~60-120 tokens each), no OOM while `look_through_webcam` is mid-call. The `_brain_busy` defer flag must be honored.
- [ ] `trim_memory()` still preserves `messages[0]` (system prompt) untouched — ephemeral injections live only in `eval_msgs`, never in the persistent `messages` list.
