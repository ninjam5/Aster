# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Aster-UI is the desktop companion app for **Aster**, a personal AI assistant whose actual brain
lives in the sibling repo `../Aster-localization` (local LLM, voice, vision, memory, 69 admin
tools, etc.). This app is the friendly desktop front-end for non-technical users, alongside the
existing terminal/Telegram interfaces.

**Current status: backend-connected.** Onboarding and the main app shell are fully built, and
every one of the 8 section panels is wired to a real endpoint on
`../Aster-localization/tools/face_server.py` (`:8000`): Dashboard (`useStats`), Memory, Notes,
Skills, Socials, and Settings via `asterGet`/`asterPost`/`asterPatch` (`src/api/*.ts`),
ActivityLog via a live WebSocket (`/api/logs`), and VoiceChat via a fetch-based chat stream
(`useChatStream`) plus real LiveKit calling (`useLiveKitCall`). Most panels keep their matching
`src/mock/*.ts` fixture as an **offline fallback** (used only when face_server.py isn't
reachable), not as the primary data source. See `MVP.md` / `agent-work.md` for project history —
note both documents describe an earlier mock-only phase and are no longer fully accurate about
current wiring.

## Commands

```bash
npm install

npm run dev          # fastest iteration loop — plain browser at http://localhost:5173
npm run tauri dev    # native window (requires Rust toolchain + MSVC "Desktop development
                      # with C++" workload on Windows)
npm run build         # tsc -b && vite build — type-check + production bundle
npm run lint          # eslint .
npm test              # vitest run — full suite
npm run test:watch    # vitest in watch mode
```

Run a single test file: `npx vitest run src/onboarding/OnboardingFlow.test.tsx`
Run a single test by name: `npx vitest run -t "switches the body when a nav item is clicked"`

Before considering any change done, `npm run build`, `npm run lint`, and `npm test` must all
pass — this is the standing bar used throughout this project's history (see `agent-work.md`).

## Architecture

### Top-level state machine

`src/App.tsx` is the entire router: it reads `useProfile()` and renders either
`OnboardingFlow` or `AppShell` based on `profile.onboardingComplete`, cross-faded with
framer-motion's `AnimatePresence`. There is no routing library — both onboarding steps and the
main shell's active section are plain local React state.

### State persistence

`src/state/useProfile.ts` is the only piece of persisted state: `{ name, techLevel, model,
onboardingComplete }` stored in `localStorage` under `aster.profile.v1`, with try/catch guards
around storage access. `resetOnboarding()` clears it and is what Settings' "Reset onboarding"
button calls to return a user to the Welcome screen.

### Onboarding flow (`src/onboarding/`)

`OnboardingFlow.tsx` orchestrates a strict step sequence, each step a separate component,
transitions done with framer-motion fades (variants live in `src/theme/motion.ts`):

```
WelcomeStep (Basic vs Advanced choice)
  → NameStep
    → Advanced: ModelStep (dropdown over src/mock/models.ts) → complete
    → Basic:    DetectingStep (simulated ~1.4s "detecting hardware" timer,
                 auto-picks AUTO_DETECTED_MODEL, skips ModelStep entirely) → complete
```

The Basic path **must never show** the model-selection screen — this branch distinction is
covered explicitly in `OnboardingFlow.test.tsx` and is easy to accidentally break when editing
the flow.

### Main shell (`src/shell/`)

`AppShell.tsx` lays out a strict **1:3 CSS grid** (`grid-cols-[minmax(220px,1fr)_3fr]`):
`Sidebar` (left nav, 9 sections, lucide icons, user chip) and `BodyRouter` (right, cross-fades
the active section panel via `AnimatePresence mode="wait"`).

Because of `mode="wait"`, the incoming panel only mounts after the outgoing one's exit animation
finishes — tests that switch sections must use `await screen.findByText(...)` (async), not a
synchronous `getByText`, or they'll race the animation. (`AppShell.test.tsx` demonstrates this.)

### Section panels (`src/sections/`)

One component per nav item (`Dashboard`, `VoiceChat`, `Memory`, `Socials`, `Skills`,
`Notes`, `ActivityLog`, `Settings`), each mapping to a real Aster capability. Memory and Notes
persist real add/remove edits through `tools/face_server.py` (`/api/memory`, `/api/notes`).
VoiceChat's message list is local `useState` seeded fresh each session by design (chat history
isn't meant to survive a reload) — that one is intentionally session-only, not a backend gap.

### Aster character (`src/character/`)

`AsterMoodProvider` (one WebSocket to face_server's `/api/logs`) resolves a single
`AsterMood` (10 moods) that drives every character on screen; `AsterCompanion` places him
per-section (`placement.ts`), and the Dashboard hero card shows the scene image instead.
Two interchangeable renderers share the mood/placement contract, chosen in Settings →
Appearance (`useCharacterStyle`, localStorage `aster.characterStyle.v1`):

- **Pixel (default)** — `src/character/pixel/`: hand-crafted 26×34 char-grid sprites
  (`pixelSprites.ts`, no image assets), rendered on a canvas by `PixelAsterCharacter.tsx`.
  Mood switches are **choreographed, never faded**: each mood has ENTER/EXIT keyframe
  timelines in `pixelTimelines.ts` (e.g. entering "working" plays the
  headphones-from-back-pocket gag, then the desk pops up and the laptop lid flips open).
  The canvas paints one frame synchronously on mount so a throttled/background window
  still shows a static Aster.
- **Cartoon** — `AsterCharacter.tsx`: the original layered-PNG puppet with CSS
  choreography (`character.css`, `useMoodTransition`).

Dev affordance: `?asterMood=<mood>` pins the mood for inspecting poses and transitions.
jsdom has no canvas 2D context — `src/test/setup.ts` stubs `getContext` to return null and
the pixel component skips its rAF loop, so component tests assert structure, while grid
and timeline data are tested directly (`pixel.test.tsx`).

### Mock data layer (`src/mock/`)

Each file exports fixtures typed against the shared `src/types.ts` interfaces (e.g.
`ChatMessage`, `MemoryFact`, `Song`). The backend swap this was originally designed for has
already happened — every panel fetches real data from `tools/face_server.py` first. Mock
fixtures now serve as the **offline fallback** each panel falls back to if that fetch fails
(e.g. `Socials.tsx`'s `.catch(() => {/* keep mock as fallback */})`), and as fixtures for tests.
Keep new mock data obviously fake and typed this way — don't let a panel reach into a mock
file's shape ad hoc.

### Design tokens

`tailwind.config.js` defines the dark theme shared with the companion `controller` mobile app:
`background` `#111111`, `surface-card` `#1E1E1E`, `surface-elevated` `#2A2A2A`, `accent-blue`
`#5B7FA6` (primary actions / active nav), `primary` `#a5caf4`, plus `on-surface` /
`on-surface-variant` text tokens. Always use these tokens (or extend them) rather than
hard-coding hex colors in components. Font is Inter; card radius `12px`, input/button radius
`8px`; motion easing is `cubic-bezier(0.4,0,0.2,1)`.

### Tauri shell (`src-tauri/`)

Single `main` window only (1100×720, min 900×600) — there is intentionally no secondary
always-on-top "mini" window like the older face-widget app this was inspired by.
`vite.config.ts` sets `server.watch.ignored: ["**/src-tauri/**"]` — without this, Vite's file
watcher throws `EBUSY` on Windows when cargo writes to `src-tauri/target` during `tauri dev`.

## Testing

Tests live next to the code they cover (`*.test.ts(x)`), run under vitest + Testing Library in
`jsdom` (config in `vitest.config.ts`, setup in `src/test/setup.ts`, which clears `localStorage`
and unmounts after each test). When testing anything that crosses an `AnimatePresence
mode="wait"` boundary (onboarding step changes, section switches), prefer `findBy*`/`waitFor`
over synchronous queries.

## Explicit non-goals for this phase

Per `MVP.md` §3/§10, still accurate: no macOS/Linux packaging (Windows-only assumptions haven't
been audited), no real hardware/model auto-detection in onboarding (`DetectingStep` is a
simulated ~1.4s timer, not real detection), no auth/multi-profile/telemetry/auto-update.

**No longer accurate**, since it was superseded once the backend was wired up: `MVP.md`'s
original claim of "no real backend/LiveKit/llama-server calls" — see "What this is" above. Real
network calls into a section panel are now the norm, not something to avoid.
