/**
 * AsterHeroCard — the home-screen hero: the 3D-style Aster city scene in its
 * own card, with a slow Ken Burns drift, twinkling sky, gentle mouse parallax
 * (spring-smoothed) and the greeting + live mood chip overlaid. This is the
 * only Aster on the home page — the corner companion covers every other page.
 */
import { useRef, type MouseEvent } from "react";
import { motion, useMotionValue, useSpring } from "framer-motion";
import heroScene from "../assets/aster/hero-scene.jpg";
import { Chip } from "../components/ui/Chip";
import { MOOD_LABEL } from "./sprites";
import { useAsterMood } from "./AsterMoodProvider";
import "./character.css";

// Twinkle positions sit in the sky region of the scene (safe across crops).
const TWINKLES = [
  { left: "18%", top: "18%", delay: "0s" },
  { left: "34%", top: "30%", delay: "1.3s" },
  { left: "8%",  top: "38%", delay: "2.4s" },
];

interface AsterHeroCardProps {
  name: string;
  modelName?: string;
}

export function AsterHeroCard({ name, modelName }: AsterHeroCardProps) {
  const mood = useAsterMood();
  const ref = useRef<HTMLDivElement>(null);

  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const x = useSpring(mx, { stiffness: 60, damping: 18 });
  const y = useSpring(my, { stiffness: 60, damping: 18 });

  const onMouseMove = (e: MouseEvent) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    mx.set(((e.clientX - r.left) / r.width - 0.5) * -12);
    my.set(((e.clientY - r.top) / r.height - 0.5) * -8);
  };
  const onMouseLeave = () => {
    mx.set(0);
    my.set(0);
  };

  return (
    <div
      ref={ref}
      data-testid="aster-hero"
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      className="relative h-64 overflow-hidden rounded-card border border-outline-custom bg-surface-card xl:h-80 2xl:h-96"
    >
      {/* -inset-4 gives the Ken Burns drift + parallax room to move without
          exposing edges. object-position anchors near the character's face so
          he stays in frame at very wide aspect ratios (100% zoom, wide
          monitors) where the vertical crop is severe. */}
      <div className="aster-kenburns absolute -inset-4">
        <motion.img
          src={heroScene}
          alt="Aster in the city at dusk"
          draggable={false}
          className="h-full w-full scale-[1.05] object-cover object-[70%_16%] select-none"
          style={{ x, y }}
        />
      </div>

      {TWINKLES.map((t, i) => (
        <span key={i} aria-hidden="true" className="aster-twinkle" style={{ left: t.left, top: t.top, animationDelay: t.delay }} />
      ))}

      <div className="absolute inset-0 bg-gradient-to-t from-black/75 via-black/20 to-transparent" />

      <div className="absolute bottom-5 left-6">
        <h1 className="font-headline-lg text-headline-lg text-on-surface">Welcome back, {name}</h1>
        {modelName && (
          <p className="mt-1 text-body-md text-on-surface-variant/80">Running on {modelName}</p>
        )}
        <div className="mt-3">
          <Chip tone="accent">{MOOD_LABEL[mood]}</Chip>
        </div>
      </div>
    </div>
  );
}
