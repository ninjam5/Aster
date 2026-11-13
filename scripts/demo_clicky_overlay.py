"""Visual showcase for Aster's Jarvis Cyan / Electric Blue Overlay Engine.

Demonstrates:
1. Top Dynamic Island with state transitions (idle -> listening -> thinking -> locating -> assist -> acting -> idle)
2. Animated indicators (cyan equalizer bars, dual-ring scanner, pulsing orb, breathing idle dot)
3. Ghost Cursor with 4-dot motion trail & sonar ripple on arrival
4. Laser Target Highlight boxes with corner HUD brackets and floating label
5. Concentric Click Target Pulse lock ring
6. Idle mini-pill and smooth auto-hide after inactivity
"""

import os
import sys
import time

# Ensure repository root is on sys.path
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from tools.overlay import (
    clear_highlights,
    hide_ghost_cursor,
    is_overlay_running,
    move_ghost_cursor,
    pulse_at,
    set_assist_mode_badge,
    set_island_state,
    show_highlights,
    start_overlay_daemon,
    stop_overlay_daemon,
)


def run_demo() -> None:
    print("=" * 72, flush=True)
    print("       ASTER OVERLAY ENGINE & TOP DYNAMIC ISLAND SHOWCASE", flush=True)
    print("                (Jarvis Cyan HUD Edition)", flush=True)
    print("=" * 72, flush=True)

    # 1. Start Overlay Daemon (force capture visible so user/screen recorders can capture)
    print("\n[Demo 1/8] Starting Overlay daemon...", flush=True)
    ok = start_overlay_daemon(force_capture_visible=True)
    if not ok:
        print("[Demo ERROR] Failed to start overlay daemon. Exiting.", flush=True)
        return
    print(f"[Demo] Overlay daemon running: {is_overlay_running()}", flush=True)
    set_island_state("idle")
    time.sleep(1.5)

    # 2. Listening State: Dynamic Island expands with animated cyan equalizer bars
    print("\n[Demo 2/8] Voice trigger: 'Hey Aster, help me find settings'...", flush=True)
    set_island_state("listening", "Listening...", "Voice stream active")
    time.sleep(2.2)

    # 3. Thinking State: Rotating Jarvis dual-ring scanner
    print("\n[Demo 3/8] Aster text engine reasoning...", flush=True)
    set_island_state("thinking", "Analyzing intent...", "Qwen 3.6 MoE")
    time.sleep(2.0)

    # 4. Locating State: Grounding scanner
    print("\n[Demo 4/8] Locating UI target on screen...", flush=True)
    set_island_state("locating", "Locating 'Settings'...", "Foveated Grounding")
    time.sleep(1.8)

    # 5. Assist Mode Activated + Ghost Cursor Flight
    print("\n[Demo 5/8] Activating Assist Mode [ ASSIST ] and sweeping Ghost Cursor...", flush=True)
    set_assist_mode_badge(True)
    set_island_state("assist", "Assist Mode Active", "Target Identified")

    # Waypoint 1: Mid-screen traversal
    move_ghost_cursor(400, 320, "Scanning UI...", duration_ms=450, pulse=False)
    time.sleep(0.6)

    # Waypoint 2: Lock onto Settings location with sonar arrival ripple
    target_x, target_y = 960, 480
    move_ghost_cursor(target_x, target_y, "Windows Settings", duration_ms=480, pulse=True)
    time.sleep(1.2)

    # 6. Laser Target Highlight Box with Corner HUD Brackets + Target Lock Pulse
    print("\n[Demo 6/8] Projecting Laser Target Highlight & Lock-on Ring...", flush=True)
    show_highlights(
        [{"box": (target_x - 80, target_y - 30, 160, 60), "label": "Settings Icon"}],
        duration=4.5,
        move_ghost=False,
    )
    pulse_at(target_x, target_y, "TARGET LOCKED")
    time.sleep(2.0)

    # 7. Acting State: Cyan pulsing orb
    print("\n[Demo 7/8] Executing desktop action...", flush=True)
    set_island_state("acting", "Opening Settings...", "Executing click")
    time.sleep(2.2)

    # 8. Returning to Idle & Auto-hide
    print("\n[Demo 8/8] Action completed. Retracting highlights and returning to idle mini-pill...", flush=True)
    clear_highlights()
    hide_ghost_cursor()
    set_assist_mode_badge(False)
    set_island_state("idle")
    print("[Demo] Dynamic Island is now in mini-pill mode (✦ Aster).", flush=True)
    print("[Demo] Waiting 4.5 seconds to observe smooth auto-hide retracting off-screen...", flush=True)
    time.sleep(4.5)

    print("\n" + "=" * 72, flush=True)
    print("      SHOWCASE COMPLETED SUCCESSFULLY (Jarvis Cyan HUD Online)", flush=True)
    print("=" * 72, flush=True)


if __name__ == "__main__":
    try:
        run_demo()
    except KeyboardInterrupt:
        print("\n[Demo] Interrupted by user.")
    finally:
        # Give a moment for any final visual checks
        time.sleep(0.5)
        # In a standalone demo, we can cleanly stop the daemon
        stop_overlay_daemon()
        # Fast exit without CRT DLL unload conflict
        os._exit(0)
