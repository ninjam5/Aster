import { emit, listen, type UnlistenFn } from "@tauri-apps/api/event";
import {
  getCurrentWindow,
  PhysicalPosition,
  primaryMonitor,
  Window,
} from "@tauri-apps/api/window";
import type { Sentiment } from "../components/AsterFace";

export const inTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export type AsterState = {
  level: number;
  state: "idle" | "connecting" | "connected" | "asleep";
  sentiment: Sentiment;
  muted: boolean;
  callState: "idle" | "connecting" | "connected";
};

export type ControlAction = "startCall" | "endCall" | "toggleMute";

let _lastEmit = 0;

/** Main window → broadcast Aster's live state so the mini circle can mirror it. */
export function emitAsterState(s: AsterState, force = false): void {
  if (!inTauri) return;
  const now = performance.now();
  if (!force && now - _lastEmit < 80) return;
  _lastEmit = now;
  void emit("aster:state", s);
}

/** Mini window → receive the main window's broadcasts. */
export async function listenAsterState(
  cb: (s: AsterState) => void,
): Promise<UnlistenFn> {
  if (!inTauri) return () => {};
  return listen<AsterState>("aster:state", (e) => cb(e.payload));
}

/** Mini window → send a control action to the main window. */
export function sendControl(action: ControlAction): void {
  if (!inTauri) return;
  void emit("aster:control", action);
}

/** Main window → listen for control actions from the mini window. */
export async function listenControl(
  cb: (action: ControlAction) => void,
): Promise<UnlistenFn> {
  if (!inTauri) return () => {};
  return listen<ControlAction>("aster:control", (e) => cb(e.payload));
}

/** Smoothly glide a window from its current position to (toX, toY). */
async function animateWindowTo(
  win: Window,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  durationMs = 220,
): Promise<void> {
  if (fromX === toX && fromY === toY) return;
  return new Promise<void>((resolve) => {
    const start = performance.now();
    function step() {
      const progress = Math.min((performance.now() - start) / durationMs, 1);
      const t = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      const x = Math.round(fromX + (toX - fromX) * t);
      const y = Math.round(fromY + (toY - fromY) * t);
      void win.setPosition(new PhysicalPosition(x, y));
      if (progress < 1) requestAnimationFrame(step);
      else resolve();
    }
    requestAnimationFrame(step);
  });
}

/** Snap the mini window to the nearest left or right screen edge (animated). */
export async function snapToEdge(win: Window): Promise<"left" | "right"> {
  try {
    const monitor = await primaryMonitor();
    if (!monitor) return "right";

    const pos  = await win.outerPosition(); // physical px
    const size = await win.outerSize();     // physical px
    const sw   = monitor.size.width;
    const sh   = monitor.size.height;

    const distLeft  = pos.x;
    const distRight = sw - (pos.x + size.width);
    const snapX     = distLeft <= distRight ? 0 : sw - size.width;
    const clampedY  = Math.max(0, Math.min(pos.y, sh - size.height));

    await animateWindowTo(win, pos.x, pos.y, snapX, clampedY);
    return distLeft <= distRight ? "left" : "right";
  } catch {
    return "right";
  }
}

/** Label of the window this React tree is running in ("main" or "mini"). */
export function currentLabel(): string {
  if (!inTauri) return "main";
  try {
    return getCurrentWindow().label;
  } catch {
    return "main";
  }
}

// Tracks whether the mini window has ever been positioned so we can set a
// sensible default on the very first show (avoids a flash at 0,0).
let _miniEverPositioned = false;

/** Main window: show the mini circle while minimized + snap it to a screen edge. */
export async function watchMinimize(): Promise<() => void> {
  if (!inTauri) return () => {};
  const main = getCurrentWindow();
  let snapTimer: ReturnType<typeof setTimeout> | null = null;
  let moveUnlisten: UnlistenFn | null = null;

  const unlistenResize = await main.onResized(async () => {
    try {
      const mini = await Window.getByLabel("mini");
      if (!mini) return;

      if (await main.isMinimized()) {
        // Pre-position at right-edge centre on first ever show to avoid 0,0 flash
        if (!_miniEverPositioned) {
          _miniEverPositioned = true;
          const monitor = await primaryMonitor();
          if (monitor) {
            const { width: sw, height: sh } = monitor.size;
            const size = await mini.outerSize();
            await mini.setPosition(
              new PhysicalPosition(sw - size.width, Math.floor((sh - size.height) / 2)),
            );
          }
        }

        await mini.show();
        await snapToEdge(mini);

        // Re-snap to edge 400 ms after the user stops dragging
        moveUnlisten = await mini.onMoved(() => {
          if (snapTimer) clearTimeout(snapTimer);
          snapTimer = setTimeout(() => void snapToEdge(mini), 400);
        });
      } else {
        if (snapTimer) clearTimeout(snapTimer);
        moveUnlisten?.();
        moveUnlisten = null;
        await mini.hide();
      }
    } catch {
      /* window may be gone */
    }
  });

  return () => {
    unlistenResize();
    moveUnlisten?.();
    if (snapTimer) clearTimeout(snapTimer);
  };
}

/** Mini window: bring the main window back and hide the circle. */
export async function restoreMain(): Promise<void> {
  if (!inTauri) return;
  try {
    const main = await Window.getByLabel("main");
    await main?.unminimize();
    await main?.show();
    await main?.setFocus();
    await getCurrentWindow().hide();
  } catch {
    /* ignore */
  }
}
