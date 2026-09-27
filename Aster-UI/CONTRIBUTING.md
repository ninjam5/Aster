# Contributing to Aster (desktop app)

Thanks for your interest in Aster! This is the consumer desktop app — a friendly front-end for
the [Aster assistant](../Aster-localization). It's an early MVP: the onboarding flow and main
shell are built, and every panel currently shows **mock data** (see [`MVP.md`](MVP.md)).

## Prerequisites

- **Node.js 20+** and npm.
- For the native window only: the **Rust toolchain** + platform build tools (on Windows, the
  MSVC "Desktop development with C++" workload). Not required for web-mode development.

## Local development

```bash
npm install
npm run dev      # browser at http://localhost:5173 — the fast iteration loop
npm test         # vitest + Testing Library
npm run lint     # eslint
npm run build    # tsc -b && vite build (type-check + production bundle)
npm run tauri dev  # native window (needs Rust + MSVC)
```

Please make sure `npm run build`, `npm run lint`, and `npm test` all pass before opening a PR.

## Project layout

| Path | What |
|---|---|
| `src/onboarding/` | First-run flow (welcome → name → model / auto-detect). |
| `src/shell/` | App shell: sidebar nav + body router. |
| `src/sections/` | One component per nav section (mock-data driven). |
| `src/components/ui/` | Shared design-system primitives. |
| `src/state/` | `localStorage`-backed user profile. |
| `src/mock/` | Placeholder data — the seam where real backend data plugs in later. |
| `src-tauri/` | Tauri (Rust) shell. |

## Conventions

- **TypeScript everywhere**, React function components + hooks.
- **Tailwind** for styling, using the design tokens in `tailwind.config.js` (don't hard-code
  hex colors in components — add/extend a token).
- **Tests live next to code** as `*.test.ts(x)` and run under vitest.
- Keep mock modules in `src/mock/` obviously-fake and shaped exactly like the future real data,
  so swapping them out stays a contained change.

## Scope reminder

Backend wiring, voice/LiveKit integration, and cross-platform packaging are **future phases** —
see [`MVP.md`](MVP.md#future-phases-post-mvp). PRs that keep the mock seam clean are easiest to
review.
