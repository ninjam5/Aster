# Polish Work Log — Visual Polish Pass

Autonomous animation/micro-interaction/visual-identity pass over the MVP skeleton, executed
panel-by-panel with `npm run build && npm run lint && npm test` run (and passing) after every
panel. Final state: **build ✅ · lint ✅ · 37/37 tests ✅** (was 36 before this pass — one net
new test added in Settings).

## 1. Animation foundation

**`src/theme/motion.ts`** (rewritten):
- Removed nothing silently — `slideHorizontal` was genuinely dead, so it was **replaced** by two
  directional variants (`slideFromRight`, `slideFromLeft`) and wired into VoiceChat's chat
  bubbles (user messages slide from the right, Aster's from the left) instead of being deleted
  outright.
- Added `staggerContainer` / `staggerItem` — the standard framer-motion "orchestrated children"
  pair (`staggerChildren: 0.04`, `delayChildren: 0.02`). Every panel's root is now a
  `motion.div` using these so content cascades in on mount instead of popping in at once.
- Added `cardHover` — a `whileHover` target (`y: -3`) used on genuinely interactive cards
  (Socials, Skills).
- Added `scaleIn` — used for the Settings confirmation modal and small "connected" checkmark
  pop-ins.
- Kept `fade`, `fadeSlideUp`, `panelTransition` as before.

**Reduced motion guard:**
- `src/App.tsx` now wraps the whole tree in `<MotionConfig reducedMotion="user">` — every
  framer-motion animation in the app automatically respects the OS-level
  `prefers-reduced-motion` setting with zero per-component logic.
- `src/index.css` adds a matching `@media (prefers-reduced-motion: reduce)` block that disables
  the plain-CSS keyframe animations (`aster-pulse` — now unused, see below — `aster-shimmer`,
  `aster-glow`, `aster-progress`, `animate-spin`), since those sit outside framer-motion's reach.

**New design tokens** (`tailwind.config.js`): `social-spotify` (#1DB954), `social-discord`
(#5865F2), `social-telegram` (#29A9EB) — brand colors used only by Connected Socials. Every
other new color need (stat-card tones, source badges, status dots) was met with Tailwind's
built-in palette (`emerald`, `sky`, `violet`, `amber`), consistent with the existing
`Chip.tsx` precedent — no other new hex was introduced.

## 2 & 3. Per-panel entrance + visual identity

| Panel | Entrance | Visual identity work |
|---|---|---|
| Dashboard | stagger (header → 4 stat cards → activity list) | Icon + color-coded tone per stat (status=emerald/Activity, memories=sky/Brain, socials=violet/Share2, uptime=amber/Clock); numeric values count up from 0 on mount (`useCountUp`, leading-integer regex so "2 / 3" and "3h 42m" partially animate, "Online" renders as-is) |
| Voice & Chat | stagger (header → chat card → orb card) | **Presence orb rebuilt**: 3 concentric `motion.div` rings (blur glow / mid / core) each breathing at a different rate via independent `animate`+`transition` loops; idle = slow (3.2s/2.6s/2s), on-call = fast + wider amplitude (1.6s/1.3s/1s). Chat bubbles now slide in directionally (`slideFromRight` for user, `slideFromLeft` for Aster) inside an `AnimatePresence`, with `layout` for smooth reflow |
| Memory | stagger + per-row | Source badge per fact: icon + color by `source` (conversation=blue/MessageCircle, discord=`social-discord`/MessageSquare, manual=emerald/PenLine). Add/forget rows animate height+opacity via `AnimatePresence`/`layout`. Empty↔list cross-fades |
| Connected Socials | stagger | Brand identity per platform (icon + border/glow tinted by the new tokens); `whileHover={cardHover}` lift; connect/disconnect animates a `scaleIn` checkmark in/out alongside the existing Chip tone change |
| Karaoke | stagger + per-row | Shimmering album-art placeholder block (`animate-aster-shimmer`); decorative looping progress bar under "now playing" driven by `NOW_PLAYING.durationSec` (pauses when `playing` is false); queue rows use `layout` so filtering reflows/reorders smoothly; empty state upgraded to the shared `EmptyState` component |
| Skills | stagger | Icon per skill (Eye/Target/Radar/Hand/ShieldAlert); status dot — glowing (`animate-aster-glow`) when enabled, muted when not; `whileHover={cardHover}`; one-line "why off" hint reusing the existing `description` field (no new mock data added) when a skill is disabled |
| Notes & Reminders | stagger + per-row | Notes get a document icon motif (FileText badge); reminders get a clock/timer icon, and **timer**-kind reminders (the "currently counting" ones) get a small `animate-pulse` dot to read as active, vs. **alarm**-kind (scheduled, no pulse); note add animates in (height+fade) |
| Activity Log | stagger | Simulated live stream: a new line appends every 4s from a small `STREAM_POOL` (added to `src/mock/activity.ts`, same `ActivityEntry` shape, fully client-side — no network calls), each fading in; autoscroll-to-bottom on new entries; terminal aesthetic (`bg-[#0c0c0c]`, monospace) unchanged |
| Settings | stagger | "Reset onboarding" now opens a `scaleIn` confirmation modal (backdrop fade + icon + explicit copy) instead of resetting immediately; section renamed "Reset" → "Danger zone"; spacing tightened (`mb-5` between fields) |

## 4. Micro-interactions

- **Button** (`components/ui/Button.tsx`): added `hover:scale-[1.015]` alongside the existing
  `active:scale-[0.98]` (now `0.97`) — every button in the app gets this for free.
- **Card** (`components/ui/Card.tsx`): added an opt-in `interactive` prop (CSS-only hover lift +
  border/shadow) — used on Dashboard's stat cards.
- **Interactive cards** (Socials, Skills): wrapped in `motion.div whileHover={cardHover}` per the
  explicit instruction to use the new variant, rather than Card's CSS-only `interactive` prop.
- **Toggle** (`components/ui/Toggle.tsx`): knob is now a `motion.span` with `layout` (spring-like
  smooth slide) instead of a CSS `translate-x` transition.
- **Sidebar nav** (`components/ui/NavItem.tsx`): the active-indicator bar is now a
  `motion.span` with `layoutId="nav-active-indicator"` — framer-motion's shared-layout
  animation slides it between nav items instead of a hard class swap (the classic
  "tabs underline" pattern; no `AnimatePresence` wrapper needed for this to work).

## 5. Empty states

- **Memory** and **Karaoke** empty states now use the shared `EmptyState` component wrapped in a
  `fade` cross-fade against the populated view (was already using `EmptyState` for Memory;
  Karaoke's plain "No songs match" paragraph was upgraded to match).
- **Notes**: skipped intentionally — there is no delete action on notes (only add), so the list
  can never reach zero items via user interaction in this mock. Adding dead empty-state code for
  an unreachable state wasn't worth the noise; flagging here per the "skip and note" instruction.

## Tests updated, and why

All updates follow the constraint: fix timing with `findBy*`/`waitFor`, never remove the
animation.

- **`src/test/setup.ts`**: stubbed `window.scrollTo`/`Element.prototype.scrollTo`/
  `scrollIntoView` — jsdom doesn't implement them, and framer-motion's `height: "auto"`
  measurement (plus the new Activity Log autoscroll) was spamming "Not implemented" stderr noise.
  Stubbing them is silent and doesn't change any test's assertions.
- **Memory** (`Memory filters facts by search query`, `Memory adds a new fact and forgets it
  again`): both now `await waitFor(...)` around the assertion that an item is gone, since
  removed rows animate out (height collapse) before unmounting instead of disappearing
  instantly.
- **Karaoke** (`Karaoke search filters the queue`, `Karaoke shows an empty state when nothing
  matches`): same reason — filtered-out rows animate out, and the empty/list views cross-fade,
  so both now wait instead of asserting synchronously.
- **Settings** (`Settings reset button calls back` → replaced/split): the reset button no
  longer calls `onResetOnboarding` directly — it opens a confirmation modal first. Replaced the
  one test with two: confirming actually calls back, and cancelling does not (plus the modal
  closes). This is a genuine behavior change requested by this pass, not just an animation
  timing fix.

## Notes / minor known loose ends

- `aster-pulse` (the original single-ring CSS pulse) is now **unused** — the orb is fully
  framer-motion-driven — but left defined in `index.css` rather than deleted, since removing
  unused CSS wasn't part of this pass's scope and it's harmless dead weight (a few bytes of
  gzip'd CSS).
- `Memory.tsx`, `Notes.tsx`, and `Karaoke.tsx` each define a near-identical local
  `rowVariants`/height-collapse variant rather than sharing one from `theme/motion.ts`. Worth
  hoisting into a shared `collapseRow` export in a future pass; not done here to avoid touching
  already-green panels late in the sequence.
- No panel was skipped — all 9 from the brief got both entrance animation and an identity pass.
