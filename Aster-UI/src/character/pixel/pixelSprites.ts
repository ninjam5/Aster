/**
 * Pixel Aster — hand-crafted 8-bit sprite data.
 *
 * Every pose is a 26×34 char grid (one char per palette colour, '.' =
 * transparent), derived from one BASE standing sprite via patch operations —
 * no image assets. The keyframe grids at the bottom exist only for the
 * choreographed transitions in pixelTimelines.ts (e.g. bare-headed Aster for
 * the "pull headphones from the back pocket" gag).
 */
import type { AsterMood } from "../sprites";

export const GRID_W = 26;
export const GRID_H = 34;

export type Grid = string[][];

export const PAL: Record<string, string> = {
  K: "#1c2333", // outline
  H: "#7c4a24", // hair
  h: "#9a6234", // hair highlight
  S: "#f7c9a2", // skin
  s: "#ee9f7d", // blush / skin shade
  B: "#4173ab", // hoodie
  b: "#2e5583", // hoodie shade
  W: "#f4f6fa", // white (tee, highlights, shoe)
  P: "#ccd7e6", // pants
  p: "#a9b8cd", // pants shade
  D: "#23415f", // headphones dark
  d: "#4a7ab3", // headphones light
  C: "#8a5a35", // desk wood
  c: "#6e4527", // desk wood shade
  L: "#2a2f3a", // laptop shell
  G: "#9fd8ff", // screen glow
  R: "#b0603f", // mouth interior
};

const BASE = [
  "........KKKKKKKKKK........", // 0
  ".......KHHHHHHHHHHK.......", // 1
  "......KHHhHHHHHHhHHK......", // 2
  ".....KDDDDDDDDDDDDDDK.....", // 3  headphone band top
  "...KDDHHHHHHHHHHHHHHDDK...", // 4  band curves down the sides
  "...KDDHhHHHHHHHHHHhHDDK...", // 5
  "...KDDHHHHHHHHHHHHHHDDK...", // 6
  "..KKKHHHHHHHHHHHHHHHHKKK..", // 7  cup tops
  ".KDDKHHHHHHHHHHHHHHHHKDDK.", // 8
  ".KDdKHHSSSSSSSSSSSSHHKdDK.", // 9
  ".KDdKSSSKWSSSSSSKWSSSKdDK.", // 10 eyes top (highlight)
  ".KDdKSSSKKSSSSSSKKSSSKdDK.", // 11 eyes bottom
  ".KDDKSssSSSSSSSSSSssSKDDK.", // 12 blush
  "..KKKSSSSSSKRRKSSSSSSKKK..", // 13 mouth
  "....KSSSSSSSSSSSSSSSSK....", // 14 chin
  ".....KKKSSSSSSSSSSKKK.....", // 15
  ".......KKKSSSSSSKKK.......", // 16 neck
  ".....KBBBBBBBBBBBBBBK.....", // 17 shoulders
  "....KBBBBBBWWWWBBBBBBK....", // 18 white tee at the collar
  "...KBBKBBBBBWWBBBBBKBBK...", // 19
  "...KBBKBBBBBWWBBBBBKBBK...", // 20
  "...KBBKBBBBBbbBBBBBKBBK...", // 21 zip below tee
  "...KBBKBbbbBbbBbbbBKBBK...", // 22 pockets
  "...KBBKBbbbBbbBbbbBKBBK...", // 23
  "...KSSKBBBBBbbBBBBBKSSK...", // 24 hands
  "...KSSKbbbbbbbbbbbbKSSK...", // 25 hem
  "....KKKPPPPPPPPPPPPKKK....", // 26 pants top
  "......KPPPPPKKPPPPPK......", // 27 legs split
  "......KPPPPK..KPPPPK......", // 28
  "......KPPPPK..KPPPPK......", // 29
  "......KPpPPK..KPPpPK......", // 30
  "......KPPPPK..KPPPPK......", // 31
  ".....KWWWWWK..KWWWWWK.....", // 32 shoes
  ".....KKKKKKK..KKKKKKK.....", // 33
];

/* ── grid helpers ── */

export interface GridPatch {
  r: number;
  c: number;
  s: string;
}

export function parseGrid(rows: string[]): Grid {
  return rows.map((r) => (r || "").padEnd(GRID_W, ".").slice(0, GRID_W).split(""));
}
export function blankGrid(): Grid {
  return Array.from({ length: GRID_H }, () => ".".repeat(GRID_W).split(""));
}
export function cloneGrid(g: Grid): Grid {
  return g.map((r) => r.slice());
}
/** writes each patch string at (r, c); '_' leaves the pixel as-is */
export function patchGrid(g: Grid, edits: GridPatch[]): Grid {
  const out = cloneGrid(g);
  for (const { r, c, s } of edits) {
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (ch === "_") continue;
      const cc = c + i;
      if (out[r] && cc >= 0 && cc < GRID_W) out[r][cc] = ch;
    }
  }
  return out;
}
export function wipeGrid(g: Grid, r0: number, r1: number, c0: number, c1: number): void {
  for (let r = r0; r <= r1; r++)
    for (let c = c0; c <= c1; c++) g[r][c] = ".";
}
/** non-transparent pixels of `top` win */
export function overlayGrid(bottom: Grid, top: Grid): Grid {
  const out = cloneGrid(bottom);
  for (let r = 0; r < GRID_H; r++)
    for (let c = 0; c < GRID_W; c++)
      if (top[r][c] !== ".") out[r][c] = top[r][c];
  return out;
}
/** copy with rows r0..r1 shifted horizontally by d (head tilts) */
export function shiftRowsH(g: Grid, r0: number, r1: number, d: number): Grid {
  const out = cloneGrid(g);
  for (let r = r0; r <= r1; r++) {
    const row = ".".repeat(GRID_W).split("");
    for (let c = 0; c < GRID_W; c++) {
      const cc = c + d;
      if (g[r][c] !== "." && cc >= 0 && cc < GRID_W) row[cc] = g[r][c];
    }
    out[r] = row;
  }
  return out;
}
function shiftDown(g: Grid, n: number): Grid {
  const out = blankGrid();
  for (let r = 0; r < GRID_H - n; r++) out[r + n] = g[r].slice();
  return out;
}

const BASE_G = parseGrid(BASE);

/* ── faces ── */

const SMILE: GridPatch[] = [{ r: 13, c: 11, s: "SKKS" }];
const HAPPY_FACE: GridPatch[] = [
  { r: 10, c: 8, s: "SKS" }, { r: 10, c: 15, s: "SKS" },
  { r: 11, c: 8, s: "KSK" }, { r: 11, c: 15, s: "KSK" },
  { r: 13, c: 9, s: "KWWWWK" }, { r: 13, c: 15, s: "S" },
];
const CALM_FACE: GridPatch[] = [
  { r: 10, c: 8, s: "SS" }, { r: 10, c: 16, s: "SS" },
  { r: 11, c: 8, s: "KKK" }, { r: 11, c: 15, s: "KKK" },
  { r: 13, c: 11, s: "SKKS" },
];
const ANGRY_FACE: GridPatch[] = [
  { r: 9,  c: 8,  s: "K" },  { r: 9,  c: 17, s: "K" },
  { r: 10, c: 8,  s: "SK" }, { r: 10, c: 16, s: "KS" },
  { r: 11, c: 8,  s: "KK" }, { r: 11, c: 16, s: "KK" },
  { r: 12, c: 6,  s: "ss" }, { r: 12, c: 18, s: "ss" },
  { r: 12, c: 12, s: "KK" },
  { r: 13, c: 11, s: "K__K" }, { r: 13, c: 12, s: "SS" },
];

/** neutral standing pose — the hub every transition passes through */
export const NEUTRAL = patchGrid(BASE_G, SMILE);

/* ── arms raised in front of the ears (celebrations, pressing phones on) ── */

function armsUpGrid(faceEdits: GridPatch[]): Grid {
  const g = cloneGrid(BASE_G);
  wipeGrid(g, 18, 25, 3, 5);
  wipeGrid(g, 18, 25, 20, 22);
  wipeGrid(g, 7, 12, 1, 3);
  wipeGrid(g, 7, 12, 22, 24);
  const arm: GridPatch[] = [
    { r: 8,  c: 2, s: "KK" },   { r: 8,  c: 22, s: "KK" },
    { r: 9,  c: 1, s: "KSSK" }, { r: 9,  c: 21, s: "KSSK" },
    { r: 10, c: 1, s: "KSSK" }, { r: 10, c: 21, s: "KSSK" },
    { r: 11, c: 1, s: "KBBK" }, { r: 11, c: 21, s: "KBBK" },
    { r: 12, c: 1, s: "KBBK" }, { r: 12, c: 21, s: "KBBK" },
    { r: 13, c: 1, s: "KBBK" }, { r: 13, c: 21, s: "KBBK" },
    { r: 14, c: 1, s: "KBBK" }, { r: 14, c: 21, s: "KBBK" },
    { r: 15, c: 1, s: "KBBK" }, { r: 15, c: 21, s: "KBBK" },
    { r: 16, c: 1, s: "KBBK" }, { r: 16, c: 21, s: "KBBK" },
    { r: 17, c: 2, s: "KBBB" }, { r: 17, c: 20, s: "BBBK" },
  ];
  return patchGrid(patchGrid(g, arm), faceEdits);
}

/* ── furniture: the pop-up desk for the working scene ── */

const DESK_BASE_EDITS: GridPatch[] = [
  { r: 23, c: 1, s: "KCCCCCCCCCCCCCCCCCCCCCCCK" },
  { r: 24, c: 1, s: "KccccccccccccccccccccccK" },
  { r: 25, c: 3, s: "KccccccccccccccccccK" },
  { r: 26, c: 3, s: "KccccccccccccccccccK" },
  { r: 27, c: 3, s: "KccccccccccccccccccK" },
  { r: 28, c: 3, s: "KccccccccccccccccccK" },
  { r: 29, c: 3, s: "KccccccccccccccccccK" },
  { r: 30, c: 3, s: "KccccccccccccccccccK" },
  { r: 31, c: 3, s: "KKKKKKKKKKKKKKKKKKKK" },
];
const LID_CLOSED: GridPatch[] = [{ r: 22, c: 7, s: "KLLLLLLLLLLK" }];
const LID_HALF: GridPatch[] = [
  { r: 20, c: 8, s: "KLLLLLLLLK" },
  { r: 21, c: 8, s: "KLLLLLLLLK" },
  { r: 22, c: 7, s: "KLLLLLLLLLLK" },
];
const LID_OPEN: GridPatch[] = [
  { r: 16, c: 8, s: "KKKKKKKKKK" },
  { r: 17, c: 8, s: "KLLLLLLLLK" },
  { r: 18, c: 8, s: "KLLLGGLLLK" },
  { r: 19, c: 8, s: "KLLLLLLLLK" },
  { r: 20, c: 8, s: "KLLLLLLLLK" },
  { r: 21, c: 8, s: "KLLLLLLLLK" },
  { r: 22, c: 7, s: "KKKKKKKKKKKK" },
];
const MUG: GridPatch[] = [
  { r: 21, c: 21, s: "KWWK" },
  { r: 22, c: 21, s: "KWWK" },
];

export const DESK_CLOSED = patchGrid(patchGrid(blankGrid(), DESK_BASE_EDITS), LID_CLOSED);
export const DESK_HALF   = patchGrid(patchGrid(blankGrid(), DESK_BASE_EDITS), LID_HALF);
export const DESK_OPEN   = patchGrid(patchGrid(blankGrid(), DESK_BASE_EDITS), LID_OPEN);
export const DESK_FULL   = patchGrid(DESK_OPEN, MUG);
export const MUG_G       = patchGrid(blankGrid(), MUG);

/* ── transition keyframe grids ── */

// bare head (headphones still "in his back pocket")
const NOPHONES_ROWS: GridPatch[] = [
  { r: 3,  c: 0, s: ".....KHHHHHHHHHHHHHHK....." },
  { r: 4,  c: 0, s: "....KHHHHHHHHHHHHHHHHK...." },
  { r: 5,  c: 0, s: "....KHHhHHHHHHHHHHhHHK...." },
  { r: 6,  c: 0, s: "....KHHHHHHHHHHHHHHHHK...." },
  { r: 7,  c: 0, s: "....KHHHHHHHHHHHHHHHHK...." },
  { r: 8,  c: 0, s: "....KHHHHHHHHHHHHHHHHK...." },
  { r: 9,  c: 0, s: "....KHHSSSSSSSSSSSSHHK...." },
  { r: 10, c: 0, s: "....KSSSKWSSSSSSKWSSSK...." },
  { r: 11, c: 0, s: "....KSSSKKSSSSSSKKSSSK...." },
  { r: 12, c: 0, s: "....KSssSSSSSSSSSSssSK...." },
  { r: 13, c: 0, s: "....KSSSSSSSKKSSSSSSSK...." },
];
export const NEUTRAL_NP = patchGrid(NEUTRAL, NOPHONES_ROWS);

// reaching to the back pocket (right arm swings behind, hand hidden)
export const REACH_BACK = (() => {
  const g = cloneGrid(NEUTRAL_NP);
  wipeGrid(g, 19, 25, 20, 22);
  return patchGrid(g, [
    { r: 19, c: 20, s: "BK" },
    { r: 20, c: 20, s: "BK" },
    { r: 21, c: 20, s: "K" },
  ]);
})();

// holding the headphones up beside his head
export const HOLD_PHONES = (() => {
  const g = cloneGrid(NEUTRAL_NP);
  wipeGrid(g, 19, 25, 20, 22);
  return patchGrid(g, [
    { r: 19, c: 19, s: "KBBK" },
    { r: 18, c: 21, s: "BBK" },
    { r: 17, c: 21, s: "BBK" },
    { r: 16, c: 22, s: "BK" },
    { r: 15, c: 22, s: "BK" },
    { r: 14, c: 22, s: "BK" },
    { r: 13, c: 22, s: "BK" },
    { r: 12, c: 22, s: "BK" },
    { r: 11, c: 22, s: "SK" },
    { r: 10, c: 22, s: "SK" },
    // little headphones held above the hand
    { r: 6, c: 21, s: "DDD" },
    { r: 7, c: 20, s: "D" }, { r: 7, c: 24, s: "D" },
    { r: 8, c: 20, s: "d" }, { r: 8, c: 24, s: "d" },
    { r: 9, c: 20, s: "d" }, { r: 9, c: 24, s: "d" },
  ]);
})();

export const PHONES_PRESS   = armsUpGrid(SMILE);
export const PHONES_PRESS_H = armsUpGrid(HAPPY_FACE);
export const STRETCH        = armsUpGrid(CALM_FACE);

// crouch — body dropped 2px, legs bent wide (jump wind-up / landing)
export const CROUCH = (() => {
  const g = blankGrid();
  const src = shiftDown(NEUTRAL, 2);
  for (let r = 0; r < 28; r++) g[r] = src[r].slice();
  return patchGrid(g, [
    { r: 28, c: 5, s: "KPPPPPK..KPPPPPK" },
    { r: 29, c: 5, s: "KPPPPK....KPPPPK" },
    { r: 30, c: 4, s: "KWWWWK......KWWWWK" },
    { r: 31, c: 4, s: "KKKKKK......KKKKKK" },
  ]);
})();

// pouty stomp: left leg raised, angry face
export const STOMP_UP = (() => {
  const g = patchGrid(NEUTRAL, ANGRY_FACE);
  wipeGrid(g, 27, 33, 5, 12);
  return patchGrid(g, [
    { r: 27, c: 6, s: "KPPPPPK" },
    { r: 28, c: 6, s: "KPPPPPK" },
    { r: 29, c: 4, s: "KWWWWK" },
    { r: 30, c: 4, s: "KKKKKK" },
  ]);
})();
export const STOMP_DOWN = patchGrid(NEUTRAL, ANGRY_FACE);

// big yawn (eyes shut, mouth wide open)
export const YAWN = patchGrid(BASE_G, [
  { r: 10, c: 8, s: "SS" }, { r: 10, c: 16, s: "SS" },
  { r: 11, c: 8, s: "KK" }, { r: 11, c: 16, s: "KK" },
  { r: 12, c: 10, s: "KRRRRK" },
  { r: 13, c: 10, s: "KRRRRK" },
  { r: 14, c: 11, s: "KKKK" },
]);

// hand rising toward the chin (thinking, mid-pose)
export const THINK_MID = (() => {
  const g = cloneGrid(NEUTRAL);
  wipeGrid(g, 21, 25, 20, 22);
  for (let r = 21; r <= 25; r++) g[r][19] = "K";
  return patchGrid(g, [
    { r: 19, c: 19, s: "KBBK" },
    { r: 20, c: 19, s: "KSSK" },
  ]);
})();

/* ── idle mood sprites ── */

const CALM = patchGrid(BASE_G, CALM_FACE);

const ALERT = patchGrid(BASE_G, [
  { r: 9,  c: 8, s: "KK______KK" },
  { r: 10, c: 8, s: "KW______KW" },
  { r: 11, c: 8, s: "KK______KK" },
  { r: 13, c: 11, s: "SKRKS" },
]);

const MUSIC = patchGrid(BASE_G, HAPPY_FACE);

const THINKING = (() => {
  const g = patchGrid(BASE_G, [
    { r: 10, c: 8, s: "KK" }, { r: 10, c: 16, s: "KK" },
    { r: 11, c: 8, s: "SS" }, { r: 11, c: 16, s: "SS" },
    { r: 13, c: 10, s: "SSKKSS" },
  ]);
  wipeGrid(g, 20, 25, 20, 22);
  for (let r = 18; r <= 25; r++) g[r][19] = "K";
  return patchGrid(g, [
    { r: 19, c: 19, s: "KBBK" },
    { r: 18, c: 20, s: "BBK" },
    { r: 17, c: 20, s: "BBK" },
    { r: 16, c: 20, s: "BSK" },
    { r: 15, c: 20, s: "SSK" },
    { r: 14, c: 20, s: "SK" },
  ]);
})();

const CONFUSED = (() => {
  const g = patchGrid(BASE_G, [{ r: 13, c: 10, s: "SKKS" }]);
  wipeGrid(g, 7, 12, 22, 24);
  wipeGrid(g, 20, 25, 20, 22);
  for (let r = 18; r <= 25; r++) g[r][19] = "K";
  return patchGrid(g, [
    { r: 19, c: 19, s: "KBBK" },
    { r: 18, c: 21, s: "BBK" },
    { r: 17, c: 21, s: "BBK" },
    { r: 16, c: 22, s: "BK" },
    { r: 15, c: 22, s: "BK" },
    { r: 14, c: 22, s: "BK" },
    { r: 13, c: 22, s: "BK" },
    { r: 12, c: 22, s: "BK" },
    { r: 11, c: 22, s: "SK" },
    { r: 10, c: 22, s: "SK" },
    { r: 9,  c: 21, s: "SS" },
    { r: 8,  c: 21, s: "SS" },
    { r: 7,  c: 21, s: "K" },
  ]);
})();

const TIRED = patchGrid(BASE_G, [
  { r: 10, c: 8, s: "SS" }, { r: 10, c: 16, s: "SS" },
  { r: 11, c: 8, s: "KK" }, { r: 11, c: 16, s: "KK" },
  { r: 12, c: 8, s: "sS" }, { r: 12, c: 16, s: "Ss" },
  { r: 13, c: 11, s: "KRRK" },
]);

const POUTY = (() => {
  const g = patchGrid(BASE_G, ANGRY_FACE);
  wipeGrid(g, 20, 25, 3, 5);
  wipeGrid(g, 20, 25, 20, 22);
  return patchGrid(g, [
    { r: 20, c: 5, s: "KBBBBBBBBSSBBBBK" },
    { r: 21, c: 5, s: "KBSSBBBBBBBBBBBK" },
    { r: 22, c: 5, s: "KKKKKKKKKKKKKKKK" },
    { r: 20, c: 13, s: "K" },
    { r: 21, c: 9,  s: "K" },
  ]);
})();

const SUCCESS = armsUpGrid(HAPPY_FACE);

const JUMPING = (() => {
  const g = armsUpGrid(HAPPY_FACE);
  for (let r = 27; r <= 33; r++) g[r] = ".".repeat(GRID_W).split("");
  return patchGrid(g, [
    { r: 27, c: 6, s: "KPPPPPPPPPPPPK" },
    { r: 28, c: 5, s: "KPPPPK....KPPPPK" },
    { r: 29, c: 4, s: "KPPPK........KPPPK" },
    { r: 30, c: 3, s: "KPPPK..........KPPPK" },
    { r: 31, c: 2, s: "KWWWK............KWWWK" },
    { r: 32, c: 2, s: "KKKKK............KKKKK" },
  ]);
})();

// working idle = neutral char behind the fully-set desk
const WORKING = overlayGrid(NEUTRAL, DESK_FULL);

export const PIXEL_SPRITES: Record<AsterMood, Grid> = {
  calm: CALM,
  working: WORKING,
  alert: ALERT,
  music: MUSIC,
  success: SUCCESS,
  pouty: POUTY,
  jumping: JUMPING,
  thinking: THINKING,
  confused: CONFUSED,
  tired: TIRED,
};

/* ── tiny pixel glyph bitmaps for the floating fx ── */

export type GlyphName = "note" | "q" | "bang" | "z" | "star" | "bulb" | "drop" | "puff";

function parseGlyph(rows: string[]): boolean[][] {
  return rows.map((r) => r.split("").map((c) => c === "X"));
}
export const GLYPHS: Record<GlyphName, boolean[][]> = {
  note: parseGlyph(["..X.", "..XX", "..X.", "XXX.", "XXX."]),
  q:    parseGlyph([".XX.", "X..X", "..X.", "....", "..X."]),
  bang: parseGlyph([".X.", ".X.", ".X.", "...", ".X."]),
  z:    parseGlyph(["XXX", "..X", ".X.", "X..", "XXX"]),
  star: parseGlyph(["..X..", ".XXX.", "XXXXX", ".XXX.", "X...X"]),
  bulb: parseGlyph([".XX.", "XXXX", "XXXX", ".XX.", ".XX."]),
  drop: parseGlyph([".X.", "XXX", "XXX"]),
  puff: parseGlyph([".XX.", "XXXX", ".XX."]),
};

/* ── idle animation parameters per mood ── */

export interface PixelMoodSpec {
  bobAmp: number;
  bobHz: number;
  fx: GlyphName | "type" | null;
  sway?: boolean;
  jump?: boolean;
  drool?: boolean;
}

export const PIXEL_MOODS: Record<AsterMood, PixelMoodSpec> = {
  calm:     { bobAmp: 1, bobHz: 0.55, fx: null },
  working:  { bobAmp: 0, bobHz: 0,    fx: "type" },
  alert:    { bobAmp: 0, bobHz: 0,    fx: "bang" },
  music:    { bobAmp: 2, bobHz: 1.1,  fx: "note", sway: true },
  success:  { bobAmp: 2, bobHz: 1.6,  fx: "star" },
  pouty:    { bobAmp: 1, bobHz: 0.4,  fx: null },
  jumping:  { bobAmp: 5, bobHz: 1.4,  fx: "star", jump: true },
  thinking: { bobAmp: 1, bobHz: 0.5,  fx: "bulb" },
  confused: { bobAmp: 1, bobHz: 0.5,  fx: "q" },
  tired:    { bobAmp: 1, bobHz: 0.3,  fx: "z", drool: true },
};
