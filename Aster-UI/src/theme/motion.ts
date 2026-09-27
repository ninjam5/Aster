// Shared framer-motion variants. Keep transitions calm — fades/slides on the
// same cubic-bezier easing used by the controller companion app.
//
// Reduced motion: the app wraps its tree in <MotionConfig reducedMotion="user">
// (see App.tsx), which makes every framer-motion animation defined here collapse
// to an instant, non-transformed state automatically when the OS-level
// "prefers-reduced-motion" setting is on. CSS-only animations (the ones declared
// as raw @keyframes in index.css, e.g. spin/shimmer) aren't covered by that and
// get their own `@media (prefers-reduced-motion: reduce)` override there instead.

import type { Transition, Variants } from "framer-motion";

export const easing: Transition["ease"] = [0.4, 0, 0.2, 1];

export const fade: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.35, ease: easing } },
  exit: { opacity: 0, transition: { duration: 0.25, ease: easing } },
};

export const fadeSlideUp: Variants = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.35, ease: easing } },
  exit: { opacity: 0, y: -8, transition: { duration: 0.25, ease: easing } },
};

// Horizontal slide-ins — used for chat bubbles so user/Aster messages arrive
// from opposite sides (see VoiceChat.tsx).
export const slideFromRight: Variants = {
  initial: { opacity: 0, x: 24 },
  animate: { opacity: 1, x: 0, transition: { duration: 0.3, ease: easing } },
  exit: { opacity: 0, x: 24, transition: { duration: 0.2, ease: easing } },
};

export const slideFromLeft: Variants = {
  initial: { opacity: 0, x: -24 },
  animate: { opacity: 1, x: 0, transition: { duration: 0.3, ease: easing } },
  exit: { opacity: 0, x: -24, transition: { duration: 0.2, ease: easing } },
};

export const panelTransition: Transition = { duration: 0.3, ease: easing };

// ── List entrance (stagger) ─────────────────────────────────────────────────
// Wrap a list in `staggerContainer`, give each child `staggerItem` — children
// cascade in instead of popping in all at once. Kept fast (30-50ms) and calm.

export const staggerContainer: Variants = {
  initial: {},
  animate: {
    transition: { staggerChildren: 0.04, delayChildren: 0.02 },
  },
};

export const staggerItem: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.3, ease: easing } },
  exit: { opacity: 0, y: -4, transition: { duration: 0.18, ease: easing } },
};

// ── Hover / appear helpers ──────────────────────────────────────────────────

// Spread onto `whileHover` for cards that should feel interactive/clickable.
export const cardHover = {
  y: -3,
  transition: { duration: 0.2, ease: easing },
};

// For modals / confirmation dialogs popping into view.
export const scaleIn: Variants = {
  initial: { opacity: 0, scale: 0.95 },
  animate: { opacity: 1, scale: 1, transition: { duration: 0.2, ease: easing } },
  exit: { opacity: 0, scale: 0.95, transition: { duration: 0.15, ease: easing } },
};
