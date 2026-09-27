// Aster's 10 mood sprites, cut from the 2d-aster.png concept sheet and split
// into head/body puppet layers at the neck (scratchpad split_layers.py) so the
// head animates independently of the body. Celebration poses (success,
// jumping) have raised arms crossing the neck line, so they stay unsplit and
// animate as whole-sprite bounces. Pivot/anchor values are fractions of the
// sprite box, measured from the source art.

import calmHead from "../assets/aster/calm_head.png";
import calmBody from "../assets/aster/calm_body.png";
import workingHead from "../assets/aster/working_head.png";
import workingBody from "../assets/aster/working_body.png";
import alertHead from "../assets/aster/alert_head.png";
import alertBody from "../assets/aster/alert_body.png";
import musicHead from "../assets/aster/music_head.png";
import musicBody from "../assets/aster/music_body.png";
import successBody from "../assets/aster/success_body.png";
import poutyHead from "../assets/aster/pouty_head.png";
import poutyBody from "../assets/aster/pouty_body.png";
import jumpingBody from "../assets/aster/jumping_body.png";
import thinkingHead from "../assets/aster/thinking_head.png";
import thinkingBody from "../assets/aster/thinking_body.png";
import confusedHead from "../assets/aster/confused_head.png";
import confusedBody from "../assets/aster/confused_body.png";
import tiredHead from "../assets/aster/tired_head.png";
import tiredBody from "../assets/aster/tired_body.png";

export type AsterMood =
  | "calm"
  | "working"
  | "alert"
  | "music"
  | "success"
  | "pouty"
  | "jumping"
  | "thinking"
  | "confused"
  | "tired";

export interface SpriteMeta {
  body: string;
  /** absent on unsplit (whole-sprite) poses */
  head?: string;
  /** natural sprite dimensions — the pose wrapper enforces this aspect ratio
   *  so the pivot percentages below stay accurate */
  w: number;
  h: number;
  /** head rotation pivot (the neck), as fractions of the sprite box */
  pivotX: number;
  pivotY: number;
  /** head centre x — anchor for mood particles */
  headCx: number;
}

export const ASTER_SPRITES: Record<AsterMood, SpriteMeta> = {
  calm:     { body: calmBody,     head: calmHead,     w: 291, h: 593, pivotX: 0.4725, pivotY: 0.3491, headCx: 0.4799 },
  working:  { body: workingBody,  head: workingHead,  w: 563, h: 581, pivotX: 0.3908, pivotY: 0.42,   headCx: 0.3684 },
  alert:    { body: alertBody,    head: alertHead,    w: 297, h: 597, pivotX: 0.4697, pivotY: 0.3484, headCx: 0.4813 },
  music:    { body: musicBody,    head: musicHead,    w: 476, h: 583, pivotX: 0.5723, pivotY: 0.5935, headCx: 0.5437 },
  success:  { body: successBody,                      w: 363, h: 515, pivotX: 0.5,    pivotY: 1.0,    headCx: 0.5077 },
  pouty:    { body: poutyBody,    head: poutyHead,    w: 297, h: 509, pivotX: 0.4747, pivotY: 0.3497, headCx: 0.4806 },
  jumping:  { body: jumpingBody,                      w: 454, h: 555, pivotX: 0.5,    pivotY: 1.0,    headCx: 0.4971 },
  thinking: { body: thinkingBody, head: thinkingHead, w: 441, h: 562, pivotX: 0.3529, pivotY: 0.427,  headCx: 0.3319 },
  confused: { body: confusedBody, head: confusedHead, w: 303, h: 581, pivotX: 0.4125, pivotY: 0.3993, headCx: 0.5003 },
  tired:    { body: tiredBody,    head: tiredHead,    w: 282, h: 570, pivotX: 0.3741, pivotY: 0.4702, headCx: 0.4799 },
};

export const MOOD_LABEL: Record<AsterMood, string> = {
  calm: "Feeling calm",
  working: "Hard at work",
  alert: "On alert",
  music: "Vibing to music",
  success: "Nailed it!",
  pouty: "Offline…",
  jumping: "Celebrating!",
  thinking: "Thinking…",
  confused: "A bit confused",
  tired: "Sleepy hours",
};

let preloaded = false;

/** Warm the browser cache so mood crossfades never flash an unloaded layer. */
export function preloadAsterSprites(): void {
  if (preloaded || typeof Image === "undefined") return;
  preloaded = true;
  for (const meta of Object.values(ASTER_SPRITES)) {
    new Image().src = meta.body;
    if (meta.head) new Image().src = meta.head;
  }
}
