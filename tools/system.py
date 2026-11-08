import os
import subprocess
import time
import threading
import difflib
import json
import psutil
import pyautogui
from datetime import datetime
import config

try:
    from pycaw.pycaw import AudioUtilities
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False

try:
    import win32gui
    import win32con
    import win32process
    import win32api
    WIN32_AVAILABLE = True
except ImportError:
    win32gui = None
    win32con = None
    win32process = None
    win32api = None
    WIN32_AVAILABLE = False


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

_APP_WINDOW_ALIASES = {
    "instagram": {"keywords": ["instagram"], "processes": ["instagram.exe"]},
    "discord": {"keywords": ["discord"], "processes": ["discord.exe"]},
    "spotify": {"keywords": ["spotify"], "processes": ["spotify.exe"]},
    "telegram": {"keywords": ["telegram"], "processes": ["telegram.exe"]},
    "chrome": {"keywords": ["chrome", "google chrome"], "processes": ["chrome.exe"]},
    "google chrome": {"keywords": ["chrome", "google chrome"], "processes": ["chrome.exe"]},
    "stremio": {"keywords": ["stremio"], "processes": ["stremio.exe"]},
    "steam": {"keywords": ["steam"], "processes": ["steam.exe", "steamwebhelper.exe"]},
    "vscode": {"keywords": ["visual studio code", "vscode", "code"], "processes": ["code.exe"]},
    "code": {"keywords": ["visual studio code", "vscode", "code"], "processes": ["code.exe"]},
    "visual studio code": {"keywords": ["visual studio code", "vscode", "code"], "processes": ["code.exe"]},
    "edge": {"keywords": ["edge", "microsoft edge", "msedge"], "processes": ["msedge.exe"]},
    "msedge": {"keywords": ["edge", "microsoft edge", "msedge"], "processes": ["msedge.exe"]},
    "microsoft edge": {"keywords": ["edge", "microsoft edge", "msedge"], "processes": ["msedge.exe"]},
    "brave": {"keywords": ["brave"], "processes": ["brave.exe"]},
    "brave browser": {"keywords": ["brave"], "processes": ["brave.exe"]},
}

_KNOWN_HOST_PROCESSES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "code.exe", "devenv.exe", "windowsterminal.exe"
}


def _find_and_focus_existing_window(app_name: str) -> tuple[bool, str]:
    """Find an already-open window matching app_name and bring it to foreground.
    Returns (True, window_title) if found and focused, (False, "") otherwise."""
    if not WIN32_AVAILABLE or win32gui is None:
        return False, ""
    try:
        clean_name = (app_name or "").strip().lower()
        if not clean_name:
            return False, ""
        if clean_name.endswith(".exe"):
            clean_name = clean_name[:-4]

        alias_info = _APP_WINDOW_ALIASES.get(clean_name)
        if alias_info:
            target_keywords = alias_info["keywords"]
            target_processes = alias_info["processes"]
        else:
            target_keywords = [clean_name]
            target_processes = [f"{clean_name}.exe"]

        found_hwnd = None
        found_title = ""

        def _enum_cb(hwnd, _):
            nonlocal found_hwnd, found_title
            try:
                title = ""
                try:
                    title = win32gui.GetWindowText(hwnd) or ""
                except Exception:
                    pass

                title_lower = title.strip().lower()

                pname = ""
                if win32process is not None:
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if pid > 0:
                            pname = psutil.Process(pid).name().lower()
                    except Exception:
                        pass

                # If window belongs to a known host (editor/browser) but we're not targeting that host, skip it
                if pname in _KNOWN_HOST_PROCESSES and pname not in target_processes:
                    return True

                title_match = bool(title_lower) and any(kw in title_lower for kw in target_keywords)
                proc_match = bool(pname) and (pname in target_processes)

                if title_match or proc_match:
                    found_hwnd = hwnd
                    found_title = title.strip() or app_name
                    return False
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            pass

        if not found_hwnd:
            return False, ""

        if win32gui.GetForegroundWindow() == found_hwnd:
            return True, found_title

        if win32gui.IsIconic(found_hwnd):
            sw_restore = win32con.SW_RESTORE if win32con is not None else 9
            win32gui.ShowWindow(found_hwnd, sw_restore)

        # Alt tap workaround so Windows allows foreground activation
        try:
            if win32api is not None and win32con is not None:
                win32api.keybd_event(0x12, 0, 0, 0)
                win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)
            else:
                import ctypes
                ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        except Exception:
            pass

        try:
            win32gui.SetForegroundWindow(found_hwnd)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(found_hwnd)
            except Exception:
                pass

        return True, found_title
    except Exception:
        return False, ""


def _perform_action(action):
    """Executes media / playback keyboard shortcuts for opened apps."""
    if not action:
        return
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


_STORE_APPS_CACHE: dict[str, str] | None = None


def _get_store_apps() -> dict[str, str]:
    global _STORE_APPS_CACHE
    if _STORE_APPS_CACHE is not None:
        return _STORE_APPS_CACHE
    apps: dict[str, str] = {}
    try:
        cmd = ["powershell", "-NoProfile", "-Command", "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"]
        out = subprocess.check_output(cmd, text=True, timeout=8)
        parsed = json.loads(out)
        if isinstance(parsed, dict):
            parsed = [parsed]
        for item in parsed:
            name = (item.get("Name") or "").strip().lower()
            appid = (item.get("AppID") or "").strip()
            if name and appid and "!" in appid:
                apps[name] = appid
    except Exception as e:
        print(f"[System] Failed to enumerate Store apps: {e}")
    _STORE_APPS_CACHE = apps
    return _STORE_APPS_CACHE


def open_application(app_name, action=None):
    already_open, win_title = _find_and_focus_existing_window(app_name)
    if already_open:
        if action:
            _perform_action(action)
        return f"App {app_name!r} is already open ({win_title!r}). Brought to foreground."

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

        app_lower = (app_name or "").strip().lower()
        # 1. Check Windows App Paths registry first (resolves chrome.exe, msedge.exe, etc. directly)
        exe_name = _BROWSER_EXES.get(app_lower, f"{app_lower}.exe" if not app_lower.endswith(".exe") else app_lower)
        app_path = _find_app_path(exe_name)
        if app_path and os.path.exists(app_path):
            os.startfile(app_path)
        else:
            # 2. Match Start Menu shortcuts: exact -> word-boundary -> substring -> fuzzy
            target_file = None
            if app_lower in shortcuts:
                target_file = shortcuts[app_lower]
            elif any(app_lower in k.split() for k in shortcuts):
                word_matches = [k for k in shortcuts if app_lower in k.split()]
                word_matches.sort(key=len)
                target_file = shortcuts[word_matches[0]]
            elif any(app_lower in k for k in shortcuts):
                sub_matches = [k for k in shortcuts if app_lower in k]
                sub_matches.sort(key=len)
                target_file = shortcuts[sub_matches[0]]
            else:
                fuzzy = difflib.get_close_matches(app_lower, list(shortcuts.keys()), n=1, cutoff=0.6)
                if fuzzy:
                    target_file = shortcuts[fuzzy[0]]

            if target_file and os.path.exists(target_file):
                os.startfile(target_file)
            else:
                store_apps = _get_store_apps()
                store_app_id = None
                if app_lower in store_apps:
                    store_app_id = store_apps[app_lower]
                elif any(app_lower in k.split() for k in store_apps):
                    word_matches = [k for k in store_apps if app_lower in k.split()]
                    word_matches.sort(key=len)
                    store_app_id = store_apps[word_matches[0]]
                elif any(app_lower in k for k in store_apps):
                    sub_matches = [k for k in store_apps if app_lower in k]
                    sub_matches.sort(key=len)
                    store_app_id = store_apps[sub_matches[0]]

                if store_app_id:
                    try:
                        os.startfile(f"shell:AppsFolder\\{store_app_id}")
                    except Exception:
                        subprocess.Popen(f"explorer.exe shell:AppsFolder\\{store_app_id}", shell=True)
                else:
                    subprocess.Popen(f"start {app_name}", shell=True)

        time.sleep(2.5)  # Let the window come to foreground before next tool fires

    if action:
        _perform_action(action)

    return f"Launched {app_name!r}. Window brought to foreground."


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
