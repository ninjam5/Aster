# Aster (desktop app)

A friendly desktop companion for [Aster](../Aster-localization), your personal AI assistant —
built for everyday people, not just developers.

> **Status:** Early MVP skeleton. The onboarding flow and main app shell are fully built, but
> every panel currently shows **mock data**. See [`MVP.md`](MVP.md) for the full plan and
> what's coming next.

## What's here

- A friendly first-run onboarding flow (pick your skill level, tell Aster your name, optionally
  choose a model).
- A main app shell with 8 sections covering Aster's capabilities: Home, Voice & Chat, Memory,
  Connected Socials, Skills & Automation, Notes & Reminders, Activity Log, and
  Settings.
- A dark, calm visual style shared with the Aster mobile [controller](../controller) app.

## Tech stack

Tauri 2 + React 19 + TypeScript + Vite 8 + TailwindCSS 3 + framer-motion + lucide-react.

## Getting started

```bash
npm install

# Fastest iteration loop — opens in your browser at http://localhost:5173
npm run dev

# Native window check (requires the Rust toolchain + MSVC build tools on Windows)
npm run tauri dev

# Type-check + production build
npm run build

# Run the test suite (vitest + Testing Library)
npm test
```

Currently developed and tested on **Windows**. macOS and Linux support is planned but not yet
verified — see [`MVP.md`](MVP.md#out-of-scope) for details.

To start over from the welcome screen, open **Settings → Reset onboarding** inside the app.

## Project structure

```
src/
  onboarding/    first-run flow (welcome, name, model selection / auto-detect)
  shell/         app shell (sidebar nav + body router)
  sections/      one component per nav section, mock-data driven
  mock/          placeholder data standing in for the real Aster backend
  components/ui/ shared design-system primitives
  state/         localStorage-backed user profile
  test/          vitest setup + smoke test
```

Tests live next to what they cover (`*.test.ts(x)`) and run under vitest + Testing Library
(`jsdom`). 31 tests cover the profile hook, onboarding paths, shell navigation, every section
panel, the UI primitives, and the mock-data fixtures.

## Learn more

- [`MVP.md`](MVP.md) — the full phased plan, design tokens, and what's explicitly out of scope.
- [`../Aster-localization`](../Aster-localization) — the actual Aster brain this app is a UI for.
