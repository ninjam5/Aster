import { motion } from "framer-motion";

export type FaceState = "idle" | "connecting" | "connected" | "asleep";

/** Conversational mood — drives the reactor colour (ideas.md #4). */
export type Sentiment = "calm" | "working" | "alert" | "music" | "success";

type Palette = {
  ring: string;
  coreInner: string;
  coreMid: string;
  coreOuter: string;
  glow: [number, number, number];
};

const PALETTE: Record<Sentiment, Palette> = {
  calm: {
    ring: "#4fe3da",
    coreInner: "#eafffd",
    coreMid: "#5cf0e6",
    coreOuter: "#2bb7c4",
    glow: [70, 224, 214],
  },
  working: {
    ring: "#f5b53d",
    coreInner: "#fff4d6",
    coreMid: "#f5b53d",
    coreOuter: "#c97e1a",
    glow: [245, 181, 61],
  },
  alert: {
    ring: "#ff5a5a",
    coreInner: "#ffe0e0",
    coreMid: "#ff5a5a",
    coreOuter: "#b22b2b",
    glow: [255, 90, 90],
  },
  music: {
    ring: "#eafffd",
    coreInner: "#ffffff",
    coreMid: "#eafffd",
    coreOuter: "#bfe9e6",
    glow: [235, 255, 253],
  },
  success: {
    ring: "#46e08c",
    coreInner: "#daffe9",
    coreMid: "#46e08c",
    coreOuter: "#1f9e5c",
    glow: [70, 224, 140],
  },
};

type AsterFaceProps = {
  state: FaceState;
  /** 0..1 smoothed amplitude of Aster's voice — drives the core pulse. */
  level: number;
  /** conversational mood — drives the colour palette. */
  sentiment?: Sentiment;
  /** rendered pixel size (square). */
  size?: number;
};

// ── pixel reactor geometry ────────────────────────────────────────────────────
const G = 28; // grid units
const C = 14; // center

type P = { x: number; y: number };

/** Pixels lying on an arc-ring. segs>0 carves it into evenly spaced segments. */
function ring(r: number, thick: number, segs: number, gap: number): P[] {
  const pts: P[] = [];
  for (let y = 0; y < G; y++) {
    for (let x = 0; x < G; x++) {
      const dx = x + 0.5 - C;
      const dy = y + 0.5 - C;
      const d = Math.hypot(dx, dy);
      if (Math.abs(d - r) > thick / 2) continue;
      if (segs > 0) {
        let a = Math.atan2(dy, dx) / (Math.PI * 2);
        if (a < 0) a += 1;
        if ((a * segs) % 1 < gap) continue;
      }
      pts.push({ x, y });
    }
  }
  return pts;
}

type RingDef = { pts: P[]; dir: number; period: number };

// computed once — radii kept tight so the rings hug the core
const RINGS: RingDef[] = [
  { pts: ring(9.6, 1.9, 6, 0.36), dir: 1, period: 1.0 }, // outer segmented
  { pts: ring(7.3, 0.95, 28, 0.5), dir: -1, period: 0.62 }, // tick band
  { pts: ring(5.3, 1.5, 3, 0.42), dir: 1, period: 1.45 }, // inner segmented
];

const COLOR_TRANSITION = { duration: 0.45, ease: "easeInOut" } as const;

export default function AsterFace({
  state,
  level,
  sentiment = "calm",
  size = 280,
}: AsterFaceProps) {
  const lvl = Math.max(0, Math.min(1, level));

  const asleep = state === "asleep";

  // mood colours — asleep overrides everything to a desaturated grey
  const pal = PALETTE[sentiment] ?? PALETTE.calm;
  const ringFill = asleep ? "#4a5568" : pal.ring;
  const [gr, gg, gb] = asleep ? [74, 85, 104] : pal.glow;
  const shimmer = sentiment === "music" && !asleep;

  // brightness + tempo per state
  const baseBright =
    state === "connected"
      ? 0.7
      : state === "connecting"
        ? 0.6
        : asleep
          ? 0.18
          : 0.4;
  const bright = Math.min(1, baseBright + lvl * 0.45);
  const speedMul =
    state === "connecting"
      ? 0.42
      : state === "connected"
        ? 0.72
        : asleep
          ? 2.4
          : 1.6;

  const coreR = 1.9 + lvl * 1.9;
  const glowPx = 14 + bright * 26 + lvl * 50;

  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: size, height: size }}
    >
      {/* soft reactor glow */}
      <motion.div
        className="absolute rounded-full"
        style={{ width: size * 0.5, height: size * 0.5 }}
        animate={{
          boxShadow: `0 0 ${glowPx}px ${glowPx * 0.55}px rgba(${gr},${gg},${gb},${0.18 + bright * 0.3})`,
        }}
        transition={{ duration: 0.22 }}
      />

      {/* music shimmer — additive eased white pulse, no hard cuts */}
      {shimmer && (
        <motion.div
          className="absolute rounded-full"
          style={{
            width: size * 0.5,
            height: size * 0.5,
            background:
              "radial-gradient(circle, rgba(255,255,255,0.55) 0%, rgba(255,255,255,0) 70%)",
          }}
          animate={{ opacity: [0, 0.4, 0], scale: [0.9, 1.08, 0.9] }}
          transition={{ duration: 0.55, repeat: Infinity, ease: "easeInOut" }}
        />
      )}

      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${G} ${G}`}
        style={{ overflow: "visible" }}
      >
        {RINGS.map((r, i) => (
          <motion.g
            key={i}
            style={{ transformBox: "fill-box", transformOrigin: "center" }}
            initial={{ rotate: 0 }}
            animate={{ rotate: 360 * r.dir }}
            transition={{
              repeat: Infinity,
              ease: "linear",
              duration: r.period * 40 * speedMul,
            }}
          >
            {r.pts.map((p, j) => (
              <motion.rect
                key={j}
                x={p.x}
                y={p.y}
                width={1}
                height={1}
                animate={{ fill: ringFill }}
                transition={COLOR_TRANSITION}
                opacity={bright}
              />
            ))}
          </motion.g>
        ))}

        {/* core orb */}
        <defs>
          <radialGradient id="asterCore" cx="50%" cy="50%" r="50%">
            <motion.stop
              offset="0%"
              animate={{ stopColor: pal.coreInner }}
              transition={COLOR_TRANSITION}
            />
            <motion.stop
              offset="55%"
              animate={{ stopColor: pal.coreMid }}
              transition={COLOR_TRANSITION}
            />
            <motion.stop
              offset="100%"
              animate={{ stopColor: pal.coreOuter }}
              transition={COLOR_TRANSITION}
            />
          </radialGradient>
        </defs>
        {state === "idle" || asleep ? (
          <motion.circle
            cx={C}
            cy={C}
            fill="url(#asterCore)"
            animate={
              asleep
                ? { r: [1.5, 1.9, 1.5], opacity: [0.28, 0.42, 0.28] }
                : { r: [2.1, 2.7, 2.1], opacity: [0.65, 0.85, 0.65] }
            }
            transition={{
              repeat: Infinity,
              duration: asleep ? 4.8 : 3.6,
              ease: "easeInOut",
            }}
          />
        ) : (
          <motion.circle
            cx={C}
            cy={C}
            fill="url(#asterCore)"
            animate={
              shimmer
                ? { r: coreR, opacity: [0.8, 1, 0.8] }
                : { r: coreR, opacity: 0.75 + bright * 0.25 }
            }
            transition={
              shimmer
                ? { duration: 0.55, repeat: Infinity, ease: "easeInOut" }
                : { duration: 0.16, ease: "easeOut" }
            }
          />
        )}
      </svg>
    </div>
  );
}
