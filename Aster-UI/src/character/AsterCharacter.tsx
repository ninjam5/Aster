/**
 * AsterCharacter — choreographed puppet renderer.
 *
 * Structure (classes are the CSS animation hooks in character.css):
 *   .aster-stage[data-mood][data-phase]     phase machine root
 *     .aster-pose (aspect-locked, size container)
 *       .aster-flip (optional mirror — never animated, so transforms don't clash)
 *         .aster-figure (enter/exit choreography target)
 *           .aster-body / .aster-head (idle loops, per-part entrances)
 *         — or <SleepingScene/> for "tired": pillow + flopped head + covers
 *       .aster-fx particles, .aster-burst yellow star bursts
 *
 * Mood changes run exit → swap → enter via useMoodTransition; nothing simply
 * fades. Purely visual — mood comes in as a prop (see useAsterMood).
 */
import { useEffect } from "react";
import { ASTER_SPRITES, preloadAsterSprites, type AsterMood } from "./sprites";
import { useMoodTransition, type MoodPhase } from "./useMoodTransition";
import "./character.css";

const FX: Partial<Record<AsterMood, { glyphs: string[]; className: string }>> = {
  tired:    { glyphs: ["z", "Z", "z"], className: "aster-fx-zzz" },
  music:    { glyphs: ["♪", "♫", "♪"], className: "aster-fx-note" },
  confused: { glyphs: ["?"],           className: "aster-fx-question" },
  alert:    { glyphs: ["!"],           className: "aster-fx-alert" },
};

function MoodEffects({ mood }: { mood: AsterMood }) {
  const fx = FX[mood];
  if (!fx) return null;
  const cx = ASTER_SPRITES[mood].headCx * 100;
  return (
    <>
      {fx.glyphs.map((glyph, i) => (
        <span
          key={i}
          aria-hidden="true"
          className={`aster-fx ${fx.className}`}
          style={{
            left: `${cx + (i - (fx.glyphs.length - 1) / 2) * 16}%`,
            animationDelay: `${1.1 + i * 0.9}s`,
          }}
        >
          {glyph}
        </span>
      ))}
    </>
  );
}

// Cartoon four-point star, bursting outward on entrances/impacts.
function Star({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <path
        d="M10 0 L12.2 7.8 L20 10 L12.2 12.2 L10 20 L7.8 12.2 L0 10 L7.8 7.8 Z"
        fill="#ffd977"
        stroke="#b8860b"
        strokeWidth="0.6"
      />
    </svg>
  );
}

// Moods whose entrance lands with an impact star burst, and where it pops.
const BURST: Partial<Record<AsterMood, { top: string; left: string; delay: number }>> = {
  working:  { top: "38%", left: "40%", delay: 0.62 },
  thinking: { top: "40%", left: "36%", delay: 0.62 },
  success:  { top: "30%", left: "50%", delay: 0.3 },
  jumping:  { top: "35%", left: "50%", delay: 0.28 },
  calm:     { top: "88%", left: "50%", delay: 0.4 },
  tired:    { top: "55%", left: "38%", delay: 0.75 }, // flop impact
};

const BURST_VECTORS = [
  { dx: -34, dy: -22 }, { dx: 30, dy: -30 }, { dx: -26, dy: 16 },
  { dx: 36, dy: 10 }, { dx: 0, dy: -40 }, { dx: 12, dy: 30 },
];

function StarBurst({ mood, phase }: { mood: AsterMood; phase: MoodPhase }) {
  const spec = BURST[mood];
  if (!spec || phase !== "enter") return null;
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute"
      style={{ top: spec.top, left: spec.left }}
    >
      {BURST_VECTORS.map((v, i) => (
        <span
          key={i}
          className="aster-burst-star"
          style={{
            "--dx": `${v.dx}px`,
            "--dy": `${v.dy}px`,
            animationDelay: `${spec.delay + i * 0.03}s`,
          } as React.CSSProperties}
        >
          <Star size={i % 2 ? 10 : 14} />
        </span>
      ))}
    </div>
  );
}

// "tired" is a full scene: pillow slides in, head flops onto it, the covers
// get pulled up, then the blanket lump breathes with Zzz drifting off.
function SleepingScene() {
  const head = ASTER_SPRITES.tired.head!;
  return (
    <div
      className="aster-figure aster-sleep absolute inset-0"
      role="img"
      aria-label="Aster (tired)"
    >
      {/* pillow */}
      <svg className="aster-sleep-pillow absolute" viewBox="0 0 100 44" aria-hidden="true">
        <rect x="2" y="4" width="96" height="38" rx="16" fill="#e7e9f2" />
        <rect x="2" y="4" width="96" height="38" rx="16" fill="none" stroke="#b9bdd1" strokeWidth="2.5" />
      </svg>
      {/* his head, flopped onto the pillow. The head layer is a full-frame PNG
          (head pixels in the top ~47%), so a cropping box shows just the head
          and the whole box rotates into the lying pose. */}
      <div className="aster-sleep-headbox absolute overflow-hidden">
        <img src={head} alt="" draggable={false} className="absolute left-0 top-0 w-full select-none" />
      </div>
      {/* covers pulled up to the chin, with a body lump that breathes */}
      <svg className="aster-sleep-blanket absolute" viewBox="0 0 160 60" aria-hidden="true">
        <path
          d="M4 56 L4 34 Q4 22 18 22 L52 22 Q74 2 106 8 Q140 14 152 30 Q158 38 158 56 Z"
          fill="#5B7FA6"
        />
        <path
          d="M4 34 Q4 22 18 22 L52 22 Q74 2 106 8"
          fill="none" stroke="#48688c" strokeWidth="3" strokeLinecap="round"
        />
        <path d="M14 34 Q26 30 38 34" fill="none" stroke="#48688c" strokeWidth="2" strokeLinecap="round" opacity="0.7" />
      </svg>
    </div>
  );
}

interface AsterCharacterProps {
  mood: AsterMood;
  className?: string;
  /** mirror the sprite so he faces the page content */
  flip?: boolean;
  /** play the entrance choreography on first mount (companion page changes) */
  animateFirstMount?: boolean;
}

export function AsterCharacter({
  mood: targetMood,
  className = "",
  flip = false,
  animateFirstMount = false,
}: AsterCharacterProps) {
  useEffect(() => {
    preloadAsterSprites();
  }, []);

  const { mood, phase } = useMoodTransition(targetMood, animateFirstMount);
  const meta = ASTER_SPRITES[mood];
  const sleeping = mood === "tired";

  return (
    <div className={`aster-stage relative ${className}`} data-mood={mood} data-phase={phase}>
      <div className="absolute inset-0 flex items-end justify-center">
        <div
          className={`aster-pose relative ${sleeping ? "w-full" : "h-full"}`}
          data-mood={mood}
          style={{ aspectRatio: sleeping ? "5 / 3" : `${meta.w} / ${meta.h}` }}
        >
          <div className={`absolute inset-0 ${flip ? "aster-flip" : ""}`}>
            {sleeping ? (
              <SleepingScene />
            ) : (
              <div className="aster-figure absolute inset-0">
                <img
                  src={meta.body}
                  alt={`Aster (${mood})`}
                  data-mood={mood}
                  draggable={false}
                  className="aster-body absolute inset-0 h-full w-full select-none"
                />
                {meta.head && (
                  <img
                    src={meta.head}
                    alt=""
                    draggable={false}
                    className="aster-head absolute inset-0 h-full w-full select-none"
                    style={{ transformOrigin: `${meta.pivotX * 100}% ${meta.pivotY * 100}%` }}
                  />
                )}
              </div>
            )}
          </div>
          <MoodEffects mood={mood} />
          <StarBurst mood={mood} phase={phase} />
        </div>
      </div>
    </div>
  );
}
