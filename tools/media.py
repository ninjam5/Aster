import re
import time
import threading
import difflib
from datetime import datetime
from winotify import Notification, audio
from config import sp, SPOTIFY_AVAILABLE
from core.tool_result import tool_ok, tool_fail


# ============================================================================
# SPOTIFY TOOLS: Phase 2 Media DJ
# ============================================================================

# Failure result fed back to the LLM. Must be blunt and instructive: the model
# has a history of reporting success after a failed call, so the string itself
# forbids that. The leading "FAILED" stays for the LLM's benefit (tool schemas
# document it); the machine-readable failure signal is now the ToolResult
# envelope (core/tool_result.py).
_NO_DEVICE_MSG = (
    "FAILED — nothing is playing. Spotify could not be started or never "
    "registered a playback device. Do NOT tell the user this succeeded; "
    "report the failure honestly."
)
_NO_DEVICE = tool_fail(_NO_DEVICE_MSG)
_NOT_INSTALLED = tool_fail("spotipy not installed")


def _launch_spotify_and_wait(timeout_s=25):
    """Start the Spotify desktop app and poll until it registers as a
    Spotify Connect device. Returns the new device_id, or None on timeout."""
    try:
        from tools.system import open_application  # local import: system.py is heavyweight
        open_application("spotify")
    except Exception:
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            devices = sp.devices()
            devs = (devices or {}).get("devices") or []
            if devs:
                return devs[0]["id"]
        except Exception:
            pass
        time.sleep(2)
    return None


def ensure_active_spotify_device():
    """Checks for an active Spotify device, waking — or launching — one if necessary.
    Returns the active device_id string on success, or None on failure."""
    if not SPOTIFY_AVAILABLE: return None
    try:
        devices = sp.devices()
        devs = (devices or {}).get('devices') or []
        active = next((d for d in devs if d['is_active']), None)
        if active:
            return active['id']
        if not devs:
            # Spotify isn't running anywhere — launch the desktop app and wait
            # for it to appear as a Connect device (takes several seconds).
            launched = _launch_spotify_and_wait()
            if not launched:
                return None
            devs = [{'id': launched}]
        # No active device — transfer playback to the first available one
        device_id = devs[0]['id']
        sp.transfer_playback(device_id=device_id, force_play=False)
        time.sleep(2)  # Give Spotify time to register
        # Re-check that the device is now active
        devices = sp.devices()
        if devices and devices.get('devices'):
            active = next((d for d in devices['devices'] if d['is_active']), None)
            if active:
                return active['id']
        return device_id  # Best-effort: return it and let the caller try
    except Exception as e:
        return None


def get_current_track():
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    try:
        current = sp.current_playback()
        if current and current.get("item"):
            name = current["item"]["name"]
            artist = current["item"]["artists"][0]["name"]
            return tool_ok(f"{name} by {artist}")
        return tool_ok("No active Spotify playback found")
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def pause_spotify():
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        sp.pause_playback(device_id=device_id)
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def resume_spotify():
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        sp.start_playback(device_id=device_id)
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def play_spotify_track(query):
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        results = sp.search(q=query, type="track", limit=1)
        tracks = results.get("tracks", {}).get("items", [])
        if tracks:
            uri = tracks[0]["uri"]
            name = tracks[0]["name"]
            artist = tracks[0]["artists"][0]["name"]
            sp.start_playback(uris=[uri], device_id=device_id)
            return tool_ok(f"Playing {name} by {artist}")
        return tool_fail("FAILED — no Spotify track matched that query; nothing is playing.")
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def play_spotify_playlist(playlist_name):
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        playlists = sp.current_user_playlists(limit=50)
        best_match = None
        for pl in playlists.get("items", []):
            if pl["name"].lower() == playlist_name.lower():
                best_match = pl
                break
        if not best_match:
            matches = difflib.get_close_matches(
                playlist_name.lower(),
                [p["name"].lower() for p in playlists.get("items", [])],
                n=1, cutoff=0.4
            )
            if matches:
                for pl in playlists.get("items", []):
                    if pl["name"].lower() == matches[0]:
                        best_match = pl
                        break
        if best_match:
            sp.start_playback(context_uri=best_match["uri"], device_id=device_id)
            return tool_ok(f"Playing playlist: {best_match['name']}")
        return tool_fail(f"FAILED — no playlist found matching '{playlist_name}'; nothing is playing.")
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def play_liked_songs():
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        results = sp.current_user_saved_tracks(limit=50)
        uris = [item["track"]["uri"] for item in results.get("items", []) if item.get("track")]
        if uris:
            sp.start_playback(uris=uris, device_id=device_id)
            return tool_ok(f"Playing your liked songs ({len(uris)} tracks loaded)")
        return tool_fail("FAILED — no liked songs found; nothing is playing.")
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def skip_spotify_track():
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        sp.next_track(device_id=device_id)
        return tool_ok("Skipped to next track.")
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def previous_spotify_track():
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        sp.previous_track(device_id=device_id)
        return tool_ok("Went back to previous track.")
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


def shuffle_spotify(state):
    if not SPOTIFY_AVAILABLE:
        return _NOT_INSTALLED
    device_id = ensure_active_spotify_device()
    if not device_id: return _NO_DEVICE
    try:
        sp.shuffle(state, device_id=device_id)
        return tool_ok(f"Shuffle {'enabled' if state else 'disabled'}.")
    except Exception as e:
        return tool_fail(f"FAILED — Spotify error, the action did NOT happen: {e}")


# ============================================================================
# TIMER & ALARM TOOLS (winotify)
# ============================================================================
def _alarm_thread(seconds, reason):
    """Background thread that triggers a native Windows 11 toast notification."""
    time.sleep(seconds)
    toast = Notification(app_id="Aster OS",
                         title="Aster Alarm",
                         msg=f"Time is up: {reason}",
                         duration="long")
    toast.set_audio(audio.LoopingAlarm, loop=True)
    toast.show()


def _parse_timer_duration(minutes=None, seconds=None, hours=None, duration=None) -> int:
    """
    Resolve any combination of time unit args to a total integer seconds value.
    Accepts:
      - Explicit fields: hours, minutes, seconds (all optional, additive)
      - Natural-language duration string: "5 minutes 30 seconds", "1h 30m", "90s", etc.
    Returns total seconds as int, or raises ValueError if nothing parseable was given.
    """
    if duration is not None:
        s = str(duration).lower()
        h_match = re.search(r'(\d+(?:\.\d+)?)\s*h(?:our|r)?s?', s)
        m_match = re.search(r'(\d+(?:\.\d+)?)\s*m(?:in(?:ute)?)?s?(?!\s*[:/])', s)
        s_match = re.search(r'(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?)?s?', s)
        total = (
            float(h_match.group(1)) * 3600 if h_match else 0.0
        ) + (
            float(m_match.group(1)) * 60  if m_match else 0.0
        ) + (
            float(s_match.group(1))        if s_match else 0.0
        )
        if total <= 0:
            raise ValueError(f"Could not parse duration from string: '{duration}'")
        return int(round(total))

    total = (
        float(hours   or 0) * 3600
        + float(minutes or 0) * 60
        + float(seconds or 0)
    )
    if total <= 0:
        raise ValueError("No valid time unit provided (hours/minutes/seconds all zero or missing).")
    return int(round(total))


def set_timer(minutes=None, seconds=None, hours=None, duration=None, reason="Timer"):
    """
    Starts a background timer using winotify. Accepts any combination of time units:
      minutes=5.5, seconds=330, hours=1, or a natural-language duration string.
    All unit fields are additive (minutes=5, seconds=30 → 330 s total).
    """
    try:
        total_s = _parse_timer_duration(minutes=minutes, seconds=seconds,
                                        hours=hours, duration=duration)
        h, rem = divmod(total_s, 3600)
        m, s   = divmod(rem, 60)
        parts  = ([f"{h}h"] if h else []) + ([f"{m}m"] if m else []) + ([f"{s}s"] if s else [])
        label  = " ".join(parts) or f"{total_s}s"
        threading.Thread(target=_alarm_thread, args=(total_s, reason), daemon=True).start()
        return tool_ok(f"Timer set for {total_s} seconds ({label}). Reason: '{reason}'.")
    except ValueError as e:
        return tool_fail(f"Failed to set timer: {e}")
    except Exception as e:
        return tool_fail(f"Failed to set timer: {e}")


def set_alarm(time_str, reason="Alarm"):
    """Sets an alarm for a specific wall-clock time using winotify."""
    try:
        now = datetime.now()
        # Parse flexible time formats: HH:MM, H:MM, HH:MM AM/PM, H:MM AM/PM
        for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
            try:
                target = datetime.strptime(time_str.strip().upper(), fmt)
                break
            except ValueError:
                continue
        else:
            return tool_fail(f"Could not parse time '{time_str}'. Use formats like '7:30 AM' or '14:00'.")

        target = target.replace(year=now.year, month=now.month, day=now.day)
        if target <= now:
            # Roll over to next day if the time has already passed today
            target = target.replace(day=now.day + 1)

        seconds = int((target - now).total_seconds())
        threading.Thread(target=_alarm_thread, args=(seconds, reason), daemon=True).start()
        return tool_ok(f"Alarm set for {target.strftime('%I:%M %p')}. Reason: '{reason}'.")
    except Exception as e:
        return tool_fail(f"Failed to set alarm: {e}")
