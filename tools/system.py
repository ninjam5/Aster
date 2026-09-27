import os
import subprocess
import time
import threading
import difflib
import psutil
import pyautogui
from datetime import datetime
import config

try:
    from pycaw.pycaw import AudioUtilities
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False


# ============================================================================
# TOOL DEFINITION: get_current_time()
# ============================================================================
def get_current_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================================
# TOOL DEFINITION: read_local_file()
# ============================================================================
def read_local_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"[FILE NOT FOUND: '{file_path}' does not exist. Call list_directory_tree to find the correct path, then retry with the full relative path.]"
    except Exception as e:
        return str(e)


# ============================================================================
# TOOL DEFINITION: write_local_file()
# ============================================================================
def write_local_file(file_path, content):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return str(e)


# ============================================================================
# TOOL DEFINITION: set_system_state()
# ============================================================================
def _delayed_os_action(command):
    """Waits 3 seconds to allow Telegram to dispatch messages, then executes."""
    time.sleep(3)
    os.system(command)


def set_system_state(action):
    action = action.lower()
    commands = {
        "lock": "rundll32.exe user32.dll,LockWorkStation",
        "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "restart": "shutdown /r /t 0",
        "shutdown": "shutdown /s /t 0",
    }
    cmd = commands.get(action)
    if cmd:
        threading.Thread(target=_delayed_os_action, args=(cmd,), daemon=True).start()


# ============================================================================
# TOOL DEFINITION: set_volume()
# ============================================================================
def set_volume(level_percentage, mute=False):
    if not PYCAW_AVAILABLE:
        return "pycaw not installed"

    try:
        device = AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume

        if mute:
            if volume.GetMute():
                volume.SetMute(0, None)
            else:
                volume.SetMute(1, None)
        else:
            level = max(0, min(100, level_percentage))
            if volume.GetMute():
                volume.SetMute(0, None)
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
    except Exception as e:
        return str(e)


# ============================================================================
# TOOL DEFINITION: boss_key()
# ============================================================================
def boss_key():
    pyautogui.hotkey("win", "d")


# ============================================================================
# TOOL DEFINITION: open_application()
# ============================================================================
# Chromium-family aliases -> real executable names (registry App Paths keys).
_BROWSER_EXES = {
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "msedge": "msedge.exe", "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "brave": "brave.exe", "brave browser": "brave.exe",
    "chromium": "chrome.exe",
}

# NOTE (settled 2026-09-26): driving the owner's REAL profile with a debug port
# is impossible — Chrome >=136 ignores --remote-debugging-port on the default
# user-data-dir, and any copy/junction of that dir loses the app-bound
# encrypted logins (Chrome purges them). Aster's debug launches therefore use
# the DEDICATED profile only; use_real_profile falls back to a normal launch.


def _find_app_path(exe_name: str):
    """Resolve an executable via the Windows App Paths registry (what the
    `start` command uses), so it can be launched with arguments WITHOUT a shell
    and a genuine failure is detectable instead of a modal dialog."""
    try:
        import winreg
    except Exception:
        return None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\App Paths\\" + exe_name
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                val = winreg.QueryValue(key, None)
            if val and os.path.exists(val):
                return val
        except OSError:
            continue
    return None


def _launch_browser_with_debug(app_name: str) -> bool:
    """Start a Chromium-family browser with the CDP debugging port when the DOM
    motor is enabled. Returns True only when a process actually started; a
    False falls through to the legacy .lnk path.

    Uses the DEDICATED Aster profile: the owner's real profile cannot be
    debugged (Chrome >=136 blocks the port on the default user-data-dir), so
    with use_real_profile we just open their browser normally (no dead debug
    attempt). --disable-background-mode keeps closed windows from leaving
    background squatters on the port and the profile lock. An already-running
    dedicated instance wins (the flag is ignored, the window opens in it)."""
    if not getattr(config, "USE_DOM_MOTOR", False):
        return False
    if bool(getattr(config, "BROWSER_USE_REAL_PROFILE", False)):
        return False
    exe = _BROWSER_EXES.get((app_name or "").strip().lower())
    if not exe:
        return False
    path = _find_app_path(exe)
    if not path:
        import shutil
        path = shutil.which(exe)
    if not path:
        print(f"[DOM] {exe} not found via App Paths/PATH; using legacy launch")
        return False
    profile = str(getattr(config, "BROWSER_PROFILE_DIR", "") or "")
    if not profile:
        return False
    port = int(getattr(config, "BROWSER_CDP_PORT", 9222))
    args = [path, f"--remote-debugging-port={port}", "--remote-allow-origins=*",
            f"--user-data-dir={profile}", "--disable-background-mode",
            "--no-first-run", "--no-default-browser-check"]
    try:
        subprocess.Popen(args)
        print(f"[DOM] Launched {exe} with CDP on 127.0.0.1:{port} "
              f"(dedicated profile {profile})")
        return True
    except Exception as e:
        print(f"[DOM] debug browser launch failed: {e}")
        return False


def open_application(app_name, action=None):
    if _launch_browser_with_debug(app_name):
        # Skips the .lnk path: shortcuts cannot carry the CDP arguments.
        time.sleep(2.5)
    else:
        user_start_menu = os.path.join(
            os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"
        )
        system_start_menu = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs"

        shortcuts = {}
        for start_menu in [user_start_menu, system_start_menu]:
            if os.path.exists(start_menu):
                for root, dirs, files in os.walk(start_menu):
                    for file in files:
                        if file.lower().endswith(".lnk"):
                            name = file[:-4].lower()
                            shortcuts[name] = os.path.join(root, file)

        matches = difflib.get_close_matches(
            app_name.lower(), list(shortcuts.keys()), n=1, cutoff=0.5
        )

        if matches:
            os.startfile(shortcuts[matches[0]])
        else:
            subprocess.Popen(f"start {app_name}", shell=True)

        time.sleep(2.5)  # Let the window come to foreground before next tool fires

    if action:
        time.sleep(2)
        if action == "play" or action == "pause":
            pyautogui.press("space")
        elif action == "next":
            pyautogui.hotkey("ctrl", "right")
        elif action == "previous":
            pyautogui.hotkey("ctrl", "left")
        elif action == "volume_up":
            pyautogui.press("volumeup")
        elif action == "volume_down":
            pyautogui.press("volumedown")
        elif action == "mute":
            pyautogui.press("volumemute")


# ============================================================================
# TOOL DEFINITION: aster_shutdown_protocol()
# ============================================================================
def aster_shutdown_protocol(shutdown_os=False, delay_minutes=0):
    """Flags the system to terminate the script and optionally shut down Windows."""
    config.SHUTDOWN_REQUESTED = True
    if shutdown_os:
        config.OS_SHUTDOWN_DELAY = delay_minutes
        return f"System flagged for script termination AND Windows OS shutdown in {delay_minutes} minutes."
    return "System flagged for script termination only."


# ============================================================================
# TOOL DEFINITION: list_directory_tree()
# ============================================================================
TREE_IGNORE = {".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".env"}

def list_directory_tree(root_path="."):
    """Returns a directory tree string for the workspace, skipping junk folders."""
    lines = []
    root_path = os.path.abspath(root_path)

    def _walk(directory, prefix=""):
        try:
            entries = sorted(os.listdir(directory))
        except PermissionError:
            return
        dirs = [e for e in entries if os.path.isdir(os.path.join(directory, e)) and e not in TREE_IGNORE]
        files = [e for e in entries if os.path.isfile(os.path.join(directory, e))]

        for f in files:
            lines.append(f"{prefix}{f}")
        for i, d in enumerate(dirs):
            lines.append(f"{prefix}{d}/")
            _walk(os.path.join(directory, d), prefix + "  ")

    lines.append(os.path.basename(root_path) + "/")
    _walk(root_path, "  ")
    return "\n".join(lines)


# ============================================================================
# TOOL DEFINITION: list_running_processes()
# ============================================================================
def list_running_processes(sort_by="memory"):
    """Returns a formatted summary of running processes sorted by CPU or memory usage."""
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status', 'username']):
        try:
            info = p.info
            mem_mb = round(info['memory_info'].rss / (1024 * 1024), 1) if info['memory_info'] else 0.0
            procs.append({
                'pid': info['pid'],
                'name': info['name'],
                'cpu': info['cpu_percent'] or 0.0,
                'mem_mb': mem_mb,
                'status': info['status'],
                'user': info['username'] or 'N/A',
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = 'mem_mb' if sort_by == "memory" else 'cpu'
    procs.sort(key=lambda x: x[key], reverse=True)
    top = procs[:25]

    lines = [f"{'PID':<8} {'Name':<30} {'CPU%':<8} {'Mem(MB)':<10} {'Status':<12} {'User'}"]
    lines.append("-" * 90)
    for p in top:
        lines.append(f"{p['pid']:<8} {p['name']:<30} {p['cpu']:<8.1f} {p['mem_mb']:<10.1f} {p['status']:<12} {p['user']}")
    lines.append(f"\nTotal processes: {len(procs)}")
    return "\n".join(lines)
