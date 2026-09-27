/**
 * Pixel Aster — choreographed mood-transition timelines.
 *
 * Every mood switch plays the old mood's EXIT steps, then the new mood's
 * ENTER steps — never a fade. Each step shows one keyframe grid for `ms`
 * milliseconds, optionally with pixel-snapped motion (dx/dy ranges), a
 * screen shake, a furniture overlay (the pop-up desk), and a procedural
 * fx overlay (dust, speed lines, sound waves…).
 *
 * The flagship: entering "working" — headphones come off (they were in his
 * back pocket, cartoon logic), he reaches back, pulls them out, presses
 * them on, the desk pops up from the floor, the laptop lid flips open in
 * stages, and the coffee mug drops in from above.
 */
import type { AsterMood } from "../sprites";
import {
  CROUCH,
  DESK_CLOSED,
  DESK_HALF,
  DESK_OPEN,
  HOLD_PHONES,
  MUG_G,
  NEUTRAL,
  NEUTRAL_NP,
  PHONES_PRESS,
  PHONES_PRESS_H,
  PIXEL_SPRITES,
  REACH_BACK,
  STOMP_DOWN,
  STOMP_UP,
  STRETCH,
  THINK_MID,
  YAWN,
  shiftRowsH,
  type Grid,
} from "./pixelSprites";

/** Geometry handed to fx overlays so they can draw in sprite space. */
export interface FxDraw {
  ctx: CanvasRenderingContext2D;
  ox: number;
  oy: number;
  px: number;
  glyph: (name: string, gx: number, gy: number, color: string, scale: number) => void;
  pixel: (c: number, r: number, color: string) => void;
  grid: (g: Grid, dx: number, dy: number) => void;
}

export interface TransitionStep {
  g: Grid;
  ms: number;
  dx?: number | [number, number];
  dy?: number | [number, number];
  shake?: number;
  desk?: Grid;
  deskDy?: [number, number];
  fx?: (p: number, d: FxDraw) => void;
}

export const lerpSnap = (a: number, b: number, p: number) => Math.round(a + (b - a) * p);

/* ── procedural fx overlays (p = 0..1 within the step) ── */

const fxSigh = (p: number, d: FxDraw) => {
  d.ctx.globalAlpha = 1 - p;
  d.glyph("puff", d.ox + 17 * d.px + p * 14, d.oy + 12 * d.px - p * 8, "#c8d2e0", 0.5);
  d.ctx.globalAlpha = 1;
};
const fxHmph = (p: number, d: FxDraw) => {
  d.ctx.globalAlpha = 1 - p;
  d.glyph("puff", d.ox + 18 * d.px + p * 12, d.oy + 13 * d.px - p * 4, "#e8eaed", 0.6);
  d.glyph("puff", d.ox + 5 * d.px - p * 12, d.oy + 13 * d.px - p * 4, "#e8eaed", 0.5);
  d.ctx.globalAlpha = 1;
};
const fxDust = (p: number, d: FxDraw) => {
  d.ctx.globalAlpha = (1 - p) * 0.9;
  const spread = 2 + p * 5;
  for (const side of [-1, 1]) {
    d.pixel(12.5 + side * spread, 32, "#8a8f9a");
    d.pixel(12.5 + side * (spread + 1.5), 31.4, "#6f747e");
  }
  d.ctx.globalAlpha = 1;
};
const fxLines = (p: number, d: FxDraw) => {
  d.ctx.globalAlpha = (1 - p) * 0.8;
  d.ctx.fillStyle = "#e8eaed";
  for (const c of [2, 23]) {
    d.ctx.fillRect(d.ox + c * d.px, d.oy + (8 + p * 16) * d.px, d.px * 0.5, d.px * 2.2);
    d.ctx.fillRect(d.ox + (c + 1.4) * d.px, d.oy + (14 + p * 14) * d.px, d.px * 0.5, d.px * 1.6);
  }
  d.ctx.globalAlpha = 1;
};
const fxCupPulse = (p: number, d: FxDraw) => {
  const on = Math.floor(p * 4) % 2 === 0;
  if (!on) return;
  d.ctx.globalAlpha = 0.85;
  d.pixel(0.2, 9, "#7fb2e8"); d.pixel(0.2, 10, "#7fb2e8");
  d.pixel(24.8, 9, "#7fb2e8"); d.pixel(24.8, 10, "#7fb2e8");
  d.ctx.globalAlpha = 0.45;
  d.pixel(-0.9, 8.4, "#7fb2e8"); d.pixel(-0.9, 10.6, "#7fb2e8");
  d.pixel(25.9, 8.4, "#7fb2e8"); d.pixel(25.9, 10.6, "#7fb2e8");
  d.ctx.globalAlpha = 1;
};
const fxBulbFlicker = (p: number, d: FxDraw) => {
  const seq = [0.25, 0, 0.7, 0.25, 1, 1];
  const a = seq[Math.min(seq.length - 1, Math.floor(p * seq.length))];
  if (a <= 0) return;
  d.ctx.globalAlpha = a;
  d.glyph("bulb", d.ox + 21.5 * d.px, d.oy - 0.5 * d.px, "#ffd977", 1.2);
  d.ctx.globalAlpha = 1;
};
const fxMugDrop = (p: number, d: FxDraw) => {
  d.grid(MUG_G, 0, lerpSnap(-7, 0, p));
};
const fxBangPop = (p: number, d: FxDraw) => {
  d.ctx.globalAlpha = Math.min(1, p * 3);
  d.glyph("bang", d.ox + 21.5 * d.px, d.oy + 0.5 * d.px, "#e86a5a", 1.4);
  d.ctx.globalAlpha = 1;
};

/* ── entrance choreographies ── */

export const PIXEL_ENTER: Record<AsterMood, TransitionStep[]> = {
  calm: [
    { g: NEUTRAL, ms: 120 },
    { g: STRETCH, ms: 340, dy: -1 },              // big stretch…
    { g: NEUTRAL, ms: 160 },
    { g: PIXEL_SPRITES.calm, ms: 240, fx: fxSigh }, // …and a happy sigh
  ],
  working: [
    { g: NEUTRAL_NP,   ms: 150 },                 // phones off —
    { g: REACH_BACK,   ms: 260 },                 // reach to the back pocket
    { g: HOLD_PHONES,  ms: 300 },                 // pull them out
    { g: PHONES_PRESS, ms: 260 },                 // press onto the ears
    { g: NEUTRAL,      ms: 150 },                 // set!
    { g: NEUTRAL, ms: 320, desk: DESK_CLOSED, deskDy: [13, 0] }, // desk pops up
    { g: NEUTRAL, ms: 150, desk: DESK_HALF },     // lid lifts…
    { g: NEUTRAL, ms: 160, desk: DESK_OPEN },     // …screen on
    { g: NEUTRAL, ms: 200, desk: DESK_OPEN, fx: fxMugDrop }, // coffee arrives
  ],
  alert: [
    { g: NEUTRAL, ms: 90 },
    { g: PIXEL_SPRITES.alert, ms: 150, dx: 1, dy: -1, shake: 2, fx: fxBangPop }, // jolt!
    { g: PIXEL_SPRITES.alert, ms: 160, shake: 1, fx: fxBangPop },
    { g: PIXEL_SPRITES.alert, ms: 150, fx: fxBangPop },
  ],
  music: [
    { g: PHONES_PRESS_H, ms: 280, fx: fxCupPulse }, // hands on the cups,
    { g: PHONES_PRESS_H, ms: 260, fx: fxCupPulse }, // beat kicks in
    { g: PIXEL_SPRITES.music, ms: 170 },
  ],
  success: [
    { g: CROUCH, ms: 150 },                                        // wind up…
    { g: PIXEL_SPRITES.success, ms: 210, dy: [-1, -5], fx: fxLines }, // leap!
    { g: PIXEL_SPRITES.success, ms: 150, dy: [-5, 0] },
  ],
  pouty: [
    { g: STOMP_UP,   ms: 180 },                        // foot up…
    { g: STOMP_DOWN, ms: 180, shake: 1, fx: fxDust },  // STOMP.
    { g: PIXEL_SPRITES.pouty, ms: 240, fx: fxHmph },   // arms cross. hmph.
  ],
  jumping: [
    { g: CROUCH, ms: 150 },
    { g: PIXEL_SPRITES.jumping, ms: 230, dy: [0, -8], fx: fxLines },
    { g: PIXEL_SPRITES.jumping, ms: 170, dy: [-8, 0] },
  ],
  thinking: [
    { g: NEUTRAL,   ms: 110 },
    { g: THINK_MID, ms: 180 },                             // hand rises…
    { g: PIXEL_SPRITES.thinking, ms: 220 },                // …to the chin
    { g: PIXEL_SPRITES.thinking, ms: 460, fx: fxBulbFlicker }, // bulb sputters on
  ],
  confused: [
    { g: shiftRowsH(NEUTRAL, 0, 15, -1), ms: 180 },  // head tilts left…
    { g: shiftRowsH(NEUTRAL, 0, 15, 1),  ms: 180 },  // …then right…
    { g: PIXEL_SPRITES.confused, ms: 220 },          // …scratch head
  ],
  tired: [
    { g: YAWN, ms: 320 },                     // biiig yawn
    { g: YAWN, ms: 260, dy: 1 },
    { g: PIXEL_SPRITES.tired, ms: 220, dy: 1 }, // slump
    { g: PIXEL_SPRITES.tired, ms: 150 },
  ],
};

/* ── exit choreographies (quick, so switches stay snappy) ── */

export const PIXEL_EXIT: Record<AsterMood, TransitionStep[]> = {
  calm:  [{ g: NEUTRAL, ms: 150 }],  // eyes open
  music: [{ g: NEUTRAL, ms: 150 }],  // groove stops
  alert: [{ g: NEUTRAL, ms: 140 }],
  working: [
    { g: NEUTRAL, ms: 140, desk: DESK_HALF },    // lid folds…
    { g: NEUTRAL, ms: 140, desk: DESK_CLOSED },  // …closed
    { g: NEUTRAL, ms: 240, desk: DESK_CLOSED, deskDy: [0, 13] }, // desk sinks away
    { g: NEUTRAL, ms: 110 },
  ],
  pouty: [
    { g: STOMP_DOWN, ms: 150 },  // arms drop
    { g: NEUTRAL,    ms: 130 },  // face relaxes
  ],
  success: [{ g: CROUCH, ms: 120 }, { g: NEUTRAL, ms: 120 }], // land
  jumping: [{ g: CROUCH, ms: 120 }, { g: NEUTRAL, ms: 120 }],
  thinking: [{ g: THINK_MID, ms: 140 }, { g: NEUTRAL, ms: 120 }], // hand drops
  confused: [{ g: NEUTRAL, ms: 140 }],
  tired: [
    { g: PIXEL_SPRITES.tired, ms: 110, dy: 1 },
    { g: NEUTRAL, ms: 90, dy: -1 },  // startled perk-up
    { g: NEUTRAL, ms: 130 },
  ],
};

export function buildTransition(from: AsterMood, to: AsterMood): TransitionStep[] {
  return [...PIXEL_EXIT[from], ...PIXEL_ENTER[to]];
}
