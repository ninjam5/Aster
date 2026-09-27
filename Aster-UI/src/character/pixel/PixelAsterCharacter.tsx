/**
 * PixelAsterCharacter — 8-bit canvas renderer, drop-in alternative to the
 * cartoon AsterCharacter (same mood prop, same placement contract).
 *
 * Rendering is a single rAF loop over hand-crafted char-grid sprites
 * (pixelSprites.ts). Mood changes never fade: they play the old mood's exit
 * choreography then the new mood's entrance (pixelTimelines.ts) — desk pops
 * up for working, stomp-then-cross-arms for pouty, crouch-and-launch for
 * jumping, and so on. Idle loops add pixel-snapped bobbing, blinking,
 * typing hands, and floating glyph fx (♪ ? ! Z ★).
 */
import { useEffect, useRef } from "react";
import type { AsterMood } from "../sprites";
import {
  GLYPHS,
  GRID_H,
  GRID_W,
  PAL,
  PIXEL_MOODS,
  PIXEL_SPRITES,
  type Grid,
  type GlyphName,
} from "./pixelSprites";
import {
  buildTransition,
  lerpSnap,
  PIXEL_ENTER,
  type FxDraw,
  type TransitionStep,
} from "./pixelTimelines";

// Internal canvas resolution: sprite pixels × PX, plus headroom above the
// sprite for jumps and floating fx. CSS scales it into the placement box.
const PX = 10;
const HEADROOM = 70;
const CANVAS_W = GRID_W * PX;
const CANVAS_H = GRID_H * PX + HEADROOM + 10;
const OX = 0;
const OY = HEADROOM;

interface Transition {
  steps: TransitionStep[];
  start: number;
  target: AsterMood;
}

interface PixelAsterCharacterProps {
  mood: AsterMood;
  className?: string;
  /** mirror the sprite so he faces the page content */
  flip?: boolean;
  /** play the mood's entrance choreography on first mount */
  animateFirstMount?: boolean;
}

export function PixelAsterCharacter({
  mood: targetMood,
  className = "",
  flip = false,
  animateFirstMount = false,
}: PixelAsterCharacterProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const moodRef = useRef<AsterMood>(targetMood);
  const trRef = useRef<Transition | null>(null);
  const idleStartRef = useRef(0);
  const mountedRef = useRef(false);

  // schedule the choreographed switch whenever the mood prop changes
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      if (animateFirstMount) {
        trRef.current = {
          steps: PIXEL_ENTER[targetMood],
          start: performance.now(),
          target: targetMood,
        };
      }
      return;
    }
    if (trRef.current) {
      // fast-forward an in-flight switch so timelines never overlap
      moodRef.current = trRef.current.target;
      trRef.current = null;
    }
    if (targetMood === moodRef.current) return;
    trRef.current = {
      steps: buildTransition(moodRef.current, targetMood),
      start: performance.now(),
      target: targetMood,
    };
  }, [targetMood, animateFirstMount]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return; // jsdom / detached — render nothing

    let raf = 0;
    idleStartRef.current = performance.now();

    const drawGrid = (g: Grid, dx: number, dy: number) => {
      for (let r = 0; r < g.length; r++) {
        const row = g[r];
        for (let c = 0; c < row.length; c++) {
          const col = PAL[row[c]];
          if (!col) continue;
          ctx.fillStyle = col;
          ctx.fillRect(OX + (c + dx) * PX, OY + (r + dy) * PX, PX, PX);
        }
      }
    };
    const drawGlyph = (name: string, gx: number, gy: number, color: string, scale: number) => {
      const bmp = GLYPHS[name as GlyphName];
      if (!bmp) return;
      const s = scale * (PX * 0.75);
      ctx.fillStyle = color;
      for (let r = 0; r < bmp.length; r++)
        for (let c = 0; c < bmp[r].length; c++)
          if (bmp[r][c]) ctx.fillRect(gx + c * s, gy + r * s, s, s);
    };
    const drawPixel = (c: number, r: number, color: string) => {
      ctx.fillStyle = color;
      ctx.fillRect(OX + c * PX, OY + r * PX, PX, PX);
    };
    const fxDraw: FxDraw = {
      ctx, ox: OX, oy: OY, px: PX,
      glyph: drawGlyph, pixel: drawPixel, grid: drawGrid,
    };

    const drawShadow = (dy: number) => {
      const shW = (11 - Math.min(8, Math.abs(Math.min(0, dy)))) * PX;
      ctx.fillStyle = "rgba(0,0,0,.35)";
      ctx.beginPath();
      ctx.ellipse(CANVAS_W / 2, OY + GRID_H * PX + 4, shW, PX * 0.9, 0, 0, Math.PI * 2);
      ctx.fill();
    };

    const renderTransition = (now: number) => {
      const tr = trRef.current!;
      const el = now - tr.start;
      let acc = 0;
      let step: TransitionStep | null = null;
      let p = 0;
      for (const s of tr.steps) {
        if (el < acc + s.ms) { step = s; p = (el - acc) / s.ms; break; }
        acc += s.ms;
      }
      if (!step) { // timeline done → idle in the target mood
        moodRef.current = tr.target;
        idleStartRef.current = now;
        trRef.current = null;
        renderIdle(now);
        return;
      }
      const dx = Array.isArray(step.dx) ? lerpSnap(step.dx[0], step.dx[1], p) : (step.dx ?? 0);
      const dy = Array.isArray(step.dy) ? lerpSnap(step.dy[0], step.dy[1], p) : (step.dy ?? 0);

      drawShadow(dy);
      if (step.shake) {
        ctx.save();
        ctx.translate(Math.round((Math.random() * 2 - 1) * step.shake),
                      Math.round((Math.random() * 2 - 1) * step.shake));
      }
      drawGrid(step.g, dx, dy);
      if (step.desk) {
        const ddy = step.deskDy ? lerpSnap(step.deskDy[0], step.deskDy[1], p) : 0;
        drawGrid(step.desk, 0, ddy);
      }
      if (step.fx) step.fx(p, fxDraw);
      if (step.shake) ctx.restore();
    };

    const renderIdle = (now: number) => {
      const t = (now - idleStartRef.current) / 1000;
      const mood = moodRef.current;
      const m = PIXEL_MOODS[mood];

      const bob = Math.round(Math.sin(t * m.bobHz * Math.PI * 2) * m.bobAmp);
      const jumpY = m.jump ? -Math.abs(Math.sin(t * m.bobHz * Math.PI)) * 6 : 0;
      const dy = m.jump ? Math.round(jumpY) : bob > 0 ? 0 : bob; // bob only lifts
      drawShadow(dy);

      const dx = m.sway ? Math.round(Math.sin(t * 0.9 * Math.PI * 2)) : 0;
      drawGrid(PIXEL_SPRITES[mood], dx, dy);

      // blink (standard open-eye idle)
      if (mood === "confused" && (t % 3.4) > 3.25) {
        for (const c of [8, 9, 16, 17]) {
          ctx.fillStyle = PAL.S;
          ctx.fillRect(OX + (c + dx) * PX, OY + (10 + dy) * PX, PX, PX);
          ctx.fillStyle = PAL.K;
          ctx.fillRect(OX + (c + dx) * PX, OY + (11 + dy) * PX, PX, PX);
        }
      }

      // typing hands + screen flicker + coffee steam (working)
      if (m.fx === "type") {
        const k = Math.floor(t * 6) % 2;
        ctx.fillStyle = PAL.S;
        ctx.fillRect(OX + (9 + k) * PX,  OY + 22 * PX, PX, PX);
        ctx.fillRect(OX + (15 - k) * PX, OY + 22 * PX, PX, PX);
        if (Math.floor(t * 3) % 3 === 0) {
          ctx.fillStyle = PAL.G;
          ctx.fillRect(OX + 12 * PX, OY + 19 * PX, PX, PX);
        }
        const sy = Math.floor(t * 2) % 3;
        ctx.fillStyle = "rgba(244,246,250,.5)";
        ctx.fillRect(OX + 22 * PX, OY + (19 - sy) * PX, PX * 0.7, PX * 0.7);
      }

      // floating glyph fx
      if (m.fx && m.fx !== "type") {
        const specs: Record<string, [string, number]> = {
          note: ["#a5caf4", 3], q: ["#e8b04a", 1], bang: ["#e86a5a", 1],
          z: ["#a5caf4", 3], star: ["#ffd977", 3], bulb: ["#ffd977", 1],
        };
        const [color, count] = specs[m.fx];
        const celebrating = mood === "success" || mood === "jumping";
        for (let i = 0; i < count; i++) {
          const phase = (t * 0.55 + i / count) % 1;
          const fy = (celebrating ? OY - PX * 1.2 : OY + PX) - phase * 44;
          const fxp = OX + (mood === "music" ? (i % 2 ? 0.8 : 21.2)
                            : celebrating ? 5 + i * 7
                            : 21.5) * PX
                    + Math.sin(phase * 6 + i) * 4;
          ctx.globalAlpha = phase < 0.15 ? phase / 0.15 : (1 - phase) * 1.2;
          drawGlyph(m.fx, fxp, fy, color, m.fx === "bang" || m.fx === "bulb" ? 1.2 : 0.9);
          ctx.globalAlpha = 1;
        }
      }

      // drool (tired)
      if (m.drool) {
        const p = (t * 0.5) % 1;
        ctx.globalAlpha = 0.8;
        drawGlyph("drop", OX + 14.4 * PX, OY + 13.8 * PX + p * 8, "#9fd8ff", 0.55);
        ctx.globalAlpha = 1;
      }
    };

    const paint = (now: number) => {
      ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);
      if (trRef.current) renderTransition(now);
      else renderIdle(now);
    };
    const frame = (now: number) => {
      paint(now);
      raf = requestAnimationFrame(frame);
    };
    // paint once synchronously — in a throttled/background window rAF can be
    // frozen indefinitely, and a static Aster beats an invisible one
    paint(performance.now());
    raf = requestAnimationFrame(frame);

    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className={`relative ${className}`} data-pixel-aster>
      <canvas
        ref={canvasRef}
        width={CANVAS_W}
        height={CANVAS_H}
        role="img"
        aria-label={`Aster (${targetMood})`}
        data-mood={targetMood}
        className="absolute inset-0 h-full w-full select-none"
        style={{
          imageRendering: "pixelated",
          objectFit: "contain",
          objectPosition: "bottom",
          transform: flip ? "scaleX(-1)" : undefined,
        }}
      />
    </div>
  );
}
