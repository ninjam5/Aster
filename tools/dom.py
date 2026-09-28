"""Stage 1 — DOM-first perception motor (the "motor loop" perception layer).

The accessibility tree IS the shortlist source: every interactive element
already exposes role + accessible name + geometry, so no model is needed to
narrow a page down. This module does exactly three deterministic things:

  1. snapshot   — Playwright aria snapshot (web) or UIA walk (Windows) -> nodes
  2. shortlist  — structural filter + lexical rank against the plan's target
  3. execute    — resolve the chosen node by role+accessible-name and act

A System-1 model (Laya, see core/system1.py) may later pick among ambiguous
shortlists and the LLM remains the planner; neither is imported here. Public
functions never raise: every failure returns None/[] so callers fall through
to the existing OCR/YOLO tracks.

Safety contract:
- Model output is never turned into selectors, JavaScript, or coordinates.
  Web nodes are re-resolved at execution time by role + accessible name only.
- Send-like actions (send/submit/delete/buy/...) are refused unless the caller
  explicitly passes confirm_send=True (DOM_MOTOR_SEND_POLICY).
- Web actions only run on a page whose window actually has OS focus; if the
  attached browser is in the background the motor returns None so the
  foreground-based UIA/OCR tracks handle the click instead.
- All Playwright work runs on one dedicated worker thread: the sync API is
  thread-affine and execute_tool runs on whichever surface thread owns the turn.
- The owner's real browser profile is NEVER driven or copied: Chrome >=136
  blocks debugging on it and its cookie encryption wipes any copy (see
  RealProfileUnavailable). Logged-in browsing happens via one-time logins in
  the dedicated profile (login_once.py).
"""
import os
import queue
import re
import threading
import time
import urllib.parse

from tools.locator_common import parse_goal, score_text, spatial_weight

# ---------------------------------------------------------------------------
# Node model
# ---------------------------------------------------------------------------
# node = {
#   "role": str,          # aria role (web) or mapped UIA type (win), lowercase
#   "name": str,          # accessible name
#   "text": str,          # text content when the snapshot carries it
#   "ref": str | None,    # aria snapshot ref ("e5") when present (never executed)
#   "bbox": tuple | None, # (left, top, right, bottom), web = viewport CSS px
#   "type": str,          # raw UIA ControlTypeName for Windows nodes
#   "source": "web" | "win",
#   "score": float,       # filled by rank_nodes() — comparative, may exceed 1.0
#   "ambiguous": bool,    # filled at resolution time (multiple matches)
# }

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "listbox",
    "menuitem", "menuitemcheckbox", "menuitemradio", "checkbox", "radio",
    "switch", "slider", "spinbutton", "tab", "treeitem", "option",
    "gridcell", "menubar",
}

# UIA ControlTypeName -> web-ish role, so both backends rank identically.
_UIA_WEB_ROLE = {
    "ButtonControl": "button",
    "SplitButtonControl": "button",
    "MenuItemControl": "menuitem",
    "MenuControl": "menubar",
    "MenuBarControl": "menubar",
    "EditControl": "textbox",
    "ComboBoxControl": "combobox",
    "CheckBoxControl": "checkbox",
    "RadioButtonControl": "radio",
    "HyperlinkControl": "link",
    "TabItemControl": "tab",
    "ListItemControl": "option",
    "TreeItemControl": "treeitem",
    "SliderControl": "slider",
    "SpinnerControl": "spinbutton",
    "TextControl": "text",
}
_UIA_INTERACTIVE_TYPES = set(_UIA_WEB_ROLE) - {"TextControl"}

# Role affinity per planned operation (bounded set, no model).
_OP_ROLES = {
    "click": {"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio",
              "tab", "treeitem", "option", "checkbox", "radio", "switch"},
    "type": {"textbox", "searchbox", "combobox", "spinbutton"},
    "select": {"combobox", "listbox", "option", "radio", "checkbox"},
}

# Clicking anything in this list transmits, destroys, or forwards -> gated.
SEND_LIKE_WORDS = {
    "send", "submit", "post", "publish", "tweet", "share", "forward",
    "resend", "reply", "delete", "remove", "buy", "pay", "purchase",
    "confirm", "accept", "invite", "upload", "unsubscribe", "archive", "block",
}

DEFAULT_SHORTLIST_K = 18
DEFAULT_MIN_SCORE = 0.55


# ---------------------------------------------------------------------------
# ARIA snapshot parsing
# ---------------------------------------------------------------------------
_ITEM_RE = re.compile(r"^\s*-\s+(?P<body>.+?)\s*$")
_ROLE_RE = re.compile(r"^(?P<role>[A-Za-z][A-Za-z0-9_-]*)")
_QUOTED_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_REF_RE = re.compile(r"\[ref=([^\]]+)\]")
_ATTR_RE = re.compile(r"\[([a-zA-Z-]+)(?:=([^\]]*))?\]")
_FLAG_ATTRS = {"disabled", "checked", "pressed", "expanded", "selected"}


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def parse_aria_snapshot(text: str) -> list:
    """Parse Playwright's aria snapshot YAML into flat nodes.

    Handles both the default and `mode="ai"` forms, e.g.:
        - button "Submit" [ref=e5]
        - textbox "Search" [ref=e3]: hello
        - text: "Buy groceries"
        - checkbox "Toggle Todo" [ref=e10] [checked]
    Tolerates arbitrary indentation/nesting and CRLF; [] on garbage input.
    """
    nodes: list = []
    for raw in (text or "").replace("\r\n", "\n").splitlines():
        m = _ITEM_RE.match(raw)
        if not m:
            continue
        body = m.group("body").strip()
        rm = _ROLE_RE.match(body)
        if not rm:
            continue
        role = rm.group("role").lower()
        rest = body[rm.end():]

        node = {"role": role, "name": "", "text": "", "ref": None,
                "bbox": None, "type": None, "source": "web", "score": 0.0}
        flags = set()
        for am in _ATTR_RE.finditer(rest):
            key = am.group(1).lower()
            if am.group(2) is None and key in _FLAG_ATTRS:
                flags.add(key)

        quoted = _QUOTED_RE.search(rest)
        if quoted:
            val = _unescape(quoted.group(1))
            if role == "text":
                node["text"] = val
            else:
                node["name"] = val
            # Refs are only trusted outside a quoted accessible name.
            after_name = rest[quoted.end():]
        else:
            after_name = rest
        refm = _REF_RE.search(after_name)
        if refm:
            node["ref"] = refm.group(1)

        # Remaining colon content -> text (e.g. `textbox "Search": hello`)
        tail = _ATTR_RE.sub("", rest)
        tail = _QUOTED_RE.sub("", tail).strip()
        if tail.startswith(":"):
            tail = tail[1:].strip()
            if tail:
                node["text"] = _unescape(tail.strip('"'))

        node["disabled"] = "disabled" in flags
        nodes.append(node)
    return nodes


# ---------------------------------------------------------------------------
# Node conversion / filtering / ranking
# ---------------------------------------------------------------------------
def uia_candidates_to_nodes(candidates) -> list:
    """Convert tools.uia raw candidates ({name, type, rect}) to nodes."""
    out = []
    for cand in candidates or []:
        try:
            x1, y1, x2, y2 = cand["rect"]
            out.append({
                "role": _UIA_WEB_ROLE.get(cand.get("type", ""), "other"),
                "name": cand.get("name", ""),
                "text": "",
                "ref": None,
                "bbox": (x1, y1, x2, y2),
                "type": cand.get("type", ""),
                "source": "win",
                "score": 0.0,
            })
        except Exception:
            continue
    return out


def is_interactive(node: dict) -> bool:
    if node.get("source") == "win":
        return node.get("type") in _UIA_INTERACTIVE_TYPES
    return node.get("role") in INTERACTIVE_ROLES


def filter_nodes(nodes, interactive_only: bool = True) -> list:
    """Structural filter only: enabled, interactive, labelled. No scoring."""
    out = []
    for n in nodes or []:
        if n.get("disabled"):
            continue
        if interactive_only and not is_interactive(n):
            continue
        if not (n.get("name") or n.get("text")):
            continue
        out.append(n)
    return out


def _hint_roles(parsed: dict) -> set:
    """Map locator_common UIA type hints onto role names for both backends."""
    roles = set()
    for hint in parsed.get("type_hints", ()):  # e.g. "ButtonControl"
        mapped = _UIA_WEB_ROLE.get(hint)
        if mapped:
            roles.add(mapped)
    return roles


def rank_nodes(nodes, goal: str, op: str = None, screen=None) -> list:
    """Score every node against the parsed goal; stable-sorted descending.

    Scores are comparative and may exceed 1.0 (role bonuses separate labels
    that text matching scores identically). screen = (w, h) enables the shared
    spatial prior when bboxes exist.
    """
    parsed = parse_goal(goal)
    hint_roles = _hint_roles(parsed)
    op_roles = _OP_ROLES.get((op or "").lower(), set())

    ranked = []
    for idx, node in enumerate(nodes or []):
        label = node.get("name") or node.get("text") or ""
        s = score_text(label, parsed)
        if s <= 0.0:
            node["score"] = 0.0
            ranked.append((0.0, idx, node))
            continue
        if node.get("role") in hint_roles:
            s += 0.05
        if op_roles and node.get("role") in op_roles:
            s += 0.10
        bbox = node.get("bbox")
        if screen and bbox and len(bbox) == 4:
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2
            s *= spatial_weight(cx, cy, screen[0], screen[1], parsed["spatial"])
        node["score"] = round(s, 4)
        ranked.append((node["score"], idx, node))

    ranked.sort(key=lambda t: (-t[0], t[1]))
    return [n for _, _, n in ranked]


def build_shortlist(nodes, goal: str, op: str = None, k: int = None,
                    min_score: float = None, screen=None) -> list:
    """Bounded candidate list for the decision step (Laya sweet spot <= 18)."""
    k = DEFAULT_SHORTLIST_K if k is None else k
    min_score = DEFAULT_MIN_SCORE if min_score is None else min_score
    ranked = rank_nodes(nodes, goal, op=op, screen=screen)
    picked = [n for n in ranked if n.get("score", 0.0) >= min_score]
    return picked[:max(1, int(k))]


def best_match(nodes, goal: str, op: str = None, min_score: float = None,
               screen=None, k: int = None):
    """Top shortlist entry if it clears the auto-act threshold, else None."""
    min_score = DEFAULT_MIN_SCORE if min_score is None else min_score
    sl = build_shortlist(nodes, goal, op=op, k=k, min_score=min_score,
                         screen=screen)
    if sl and sl[0].get("score", 0.0) >= min_score:
        return sl[0]
    return None


def is_send_like(node: dict) -> bool:
    """True when acting on this element transmits, destroys, or forwards something.

    Two independent nets (Phase 1 / ID 17 of laya-integration.md):
      1. the word set — fast, and catches anything explicitly named
      2. a Laya yes/no gate over role + accessible name, which catches the
         paraphrases the word set misses ("Place order", "Complete purchase",
         "Confirm booking") and does not false-fire on "share location"

    FAILS CLOSED on kernel doubt: a low-margin verdict or a kernel failure gates the
    element behind `confirm_send` rather than clicking it. With the kernel OFF this
    returns the word-set answer only (exactly today's behavior).
    """
    label = (node.get("name") or node.get("text") or "").lower()
    words = set(re.findall(r"[a-z]+", label))
    if words & SEND_LIKE_WORDS:
        return True
    return _laya_send_like(node)


def _laya_send_like(node: dict) -> bool:
    """Laya paraphrase check for the send/destructive gate. Fails closed."""
    label = (node.get("name") or node.get("text") or "").lower()
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return False  # kernel off -> the word set is the only net
        verdict = system1.check_state(
            "Is acting on this element an outward-facing or destructive action?",
            yes_description=("outward/destructive — it sends, submits, publishes, forwards, "
                             "deletes, buys, pays, or confirms a booking"),
            no_description=("benign — navigation, opening, viewing, toggling, filtering, "
                            "or a harmless control"),
            state_text=f"element role={node.get('role')!r} label={label!r}",
            min_margin=0.5,   # QA 2026-09-28: this is the safety gate — it was inheriting
                              # the loosest global threshold (0.25) in the codebase
        )
    except Exception:
        return True  # kernel failure -> fail closed
    if verdict.get("escalate") or verdict.get("answer") is None:
        return True  # doubtful -> gate it
    return bool(verdict.get("answer"))


_SEARCH_LIKE_RE = re.compile(r"\b(?:search|find|query|look ?up|filter)\b", re.IGNORECASE)


def submit_needs_confirm(goal: str, label: str = "") -> bool:
    """True when a submit/Enter action must be confirmed (ID 17, QA round 4).

    Pressing Enter submits whatever form has focus — the most common way to SEND
    something — and the submit paths had no send/destructive check at all, so a
    compose or checkout form could be submitted silently.

    Search-like targets are exempt on purpose: the human-search flow deliberately uses
    `submit=true` for a site's own search bar.
    """
    if _SEARCH_LIKE_RE.search(f"{goal or ''} {label or ''}"):
        return False
    return _send_policy() == "confirm"


def _send_policy() -> str:
    """Validated policy; anything unexpected fails closed to 'confirm'."""
    try:
        import config
        policy = str(getattr(config, "DOM_MOTOR_SEND_POLICY", "confirm")).lower()
    except Exception:
        policy = "confirm"
    return policy if policy in ("confirm", "allow") else "confirm"


def _motor_limits():
    try:
        import config
        return (int(getattr(config, "DOM_MOTOR_SHORTLIST_K", DEFAULT_SHORTLIST_K)),
                float(getattr(config, "DOM_MOTOR_MIN_SCORE", DEFAULT_MIN_SCORE)))
    except Exception:
        return DEFAULT_SHORTLIST_K, DEFAULT_MIN_SCORE


# ---------------------------------------------------------------------------
# Web backend — all Playwright work runs on one dedicated worker thread
# ---------------------------------------------------------------------------
_WEB_LOCK = threading.Lock()
_WEB = None                 # {"pw":..., "browser":...} created on the worker
_WEB_FAILED_AT = 0.0
_WEB_RETRY_S = 30.0
_WEB_INFO = {"attached": False, "last_page_count": 0, "mode": None, "warning": ""}
_WORKER_LOCK = threading.Lock()
_WORKER = None


class _WebWorker:
    """Single thread that owns every Playwright object (sync API is thread-
    affine). Callers submit callables; results/errors come back synchronously."""

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="dom-web", daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            fn, box, done = self._jobs.get()
            if fn is None:
                done.set()
                return
            try:
                box["result"] = fn()
            except Exception as e:  # re-raised in the caller's thread
                box["error"] = e
            finally:
                done.set()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def call(self, fn, timeout: float = 12.0):
        done = threading.Event()
        box: dict = {}
        self._jobs.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("dom-web worker timed out")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def stop(self):
        try:
            self._jobs.put((None, {}, threading.Event()))
        except Exception:
            pass


def _get_worker() -> _WebWorker:
    global _WORKER
    if _WORKER is not None and _WORKER.alive():
        return _WORKER
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.alive():
            _WORKER = _WebWorker()
    return _WORKER


def _web_call(fn, timeout: float = 12.0):
    """Run fn() on the Playwright worker; None on any failure (never raises) —
    except RealProfileUnavailable, which is re-raised so callers can tell the
    owner the truth instead of silently degrading to a logged-out guest."""
    try:
        return _get_worker().call(fn, timeout=timeout)
    except RealProfileUnavailable as e:
        _WEB_INFO["warning"] = str(e)
        raise
    except Exception as e:
        print(f"[DOM] web worker call failed: {e.__class__.__name__}: {e}")
        return None


def _cdp_url() -> str:
    try:
        import config
        port = int(getattr(config, "BROWSER_CDP_PORT", 9222))
    except Exception:
        port = 9222
    return f"http://127.0.0.1:{port}"


def _wait_cdp(port: int, timeout: float = 25.0) -> bool:
    """Poll the CDP HTTP endpoint until the launched browser is ready."""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


class RealProfileUnavailable(RuntimeError):
    """Raised when the owner asked to browse with their real logged-in browser
    profile (automation.use_real_profile). Chrome >=136 forbids remote
    debugging on the default user-data-dir, and its app-bound cookie encryption
    refuses to decrypt through any copy or junction of that dir — attempts do
    not just fail, they PURGE the copied cookies (verified experimentally
    2026-09-26; see aster-failure-archaeology). Never attempt a workaround;
    the only supported CDP way to browse logged-in is a one-time login in the
    dedicated Aster profile (login_once.py)."""


class InsufficientMemoryForBrowser(RuntimeError):
    """Raised when free system RAM is too low to safely launch Aster's browser.

    Deterministic refusal (like RealProfileUnavailable): no retry, no launch.
    Launching Chrome beside llama-server's mmap'd MoE experts OOM'd this machine
    (2026-09-28). The message is surfaced to the model verbatim.
    """

def _real_profile_message() -> str:
    """Single source of truth for the honest, actionable refusal."""
    return (
        "Cannot drive your real Chrome/Edge profile: Chrome 136+ blocks remote "
        "debugging on your main browser folder, and its cookie encryption refuses "
        "any copy or link of it (a security feature — attempts also wipe the copied "
        "cookies, so it must never be tried). The supported way to browse logged-in "
        "is a ONE-TIME login in Aster's own browser profile: run "
        "'python login_once.py <site>' (e.g. linkedin), sign in once in the window "
        "that opens, and every later browse_web uses that saved session. Until then "
        "Aster browses logged-out; set automation.use_real_profile: false to stop "
        "seeing this error.")


def _free_port(start: int, tries: int = 20) -> int:
    """A free loopback TCP port at or after `start` (for Aster's own browser)."""
    import socket
    for port in range(start, start + tries):
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    return start


def _memory_headroom_ok() -> tuple[bool, str]:
    """True when there is enough free RAM to open a browser without OOMing the box.

    Returns (ok, reason_when_not_ok). Never raises - on any probe failure it
    returns True so a missing psutil cannot block browsing.
    """
    import config as _cfgmod
    need = float(getattr(_cfgmod, "BROWSER_MIN_FREE_RAM_GB", 1.5) or 0)
    if need <= 0:
        return True, ""
    # Under pytest, never let the dev machine's live RAM decide a test outcome
    # (the guard's own logic is covered by TestBrowserMemoryGuard with mocked psutil).
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True, ""
    try:
        import psutil
        avail_gb = psutil.virtual_memory().available / (1024 ** 3)
    except Exception:
        return True, ""
    if avail_gb < need:
        return False, (f"only {avail_gb:.1f} GB RAM free and opening a browser needs "
                       f">= {need:.1f} GB - close some applications and try again")
    return True, ""


def _spawn_cdp_browser(pw, exe, profile, port, headless):
    """Spawn the system browser on a FREE debug port with the dedicated profile
    and attach. Returns (browser, context, mode, popen) or None.

    --disable-background-mode: without it a closed window can leave background
    processes holding the debug port and the profile lock (the stale-guest
    squatters on 9222, 2026-09-25). Never reuses an unknown browser already on
    the port (that is how a stale guest hijacked the session)."""
    if not exe or headless or not profile:
        return None
    ok, why = _memory_headroom_ok()
    if not ok:
        raise InsufficientMemoryForBrowser(why)
    import subprocess
    os.makedirs(profile, exist_ok=True)
    args = [exe, f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--disable-background-mode", "--remote-allow-origins=*",
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1400,950", "--lang=en-US"]
    try:
        proc = subprocess.Popen(args)
    except Exception as e:
        print(f"[DOM] system-browser launch failed: {e}")
        return None
    if _wait_cdp(port):
        try:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}",
                                                   timeout=3000)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            print(f"[DOM] Launched {os.path.basename(exe)} with CDP on :{port} "
                  f"(profile={profile})")
            return browser, context, "own-launch", proc
        except Exception as e:
            print(f"[DOM] attach to own launch failed: {e.__class__.__name__}")
    return None


def _kill_browser_tree(profile_dir: str) -> None:
    """Kill Chrome/Edge processes using OUR dedicated profile dir — never the
    owner's browser (different user-data-dir). Matching by --user-data-dir is
    the robust way: the Popen launcher pid can hand the browser off to a child
    process (observed 2026-09-26), and killing by pid leaves the real browser
    alive holding the port and the profile lock. Best effort; never raises."""
    if not profile_dir:
        return
    norm = os.path.normcase(os.path.abspath(profile_dir))
    pids = []
    try:
        import psutil
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                if (proc.info.get("name") or "").lower() not in ("chrome.exe", "msedge.exe"):
                    continue
                cmd = proc.info.get("cmdline") or []
                for tok in cmd:
                    if tok.startswith("--user-data-dir="):
                        udd = tok.split("=", 1)[1].strip('"')
                        if os.path.normcase(os.path.abspath(udd)) == norm:
                            pids.append(proc.pid)
                        break
            except Exception:
                continue
    except Exception:
        return
    if not pids:
        return
    try:
        import subprocess
        for pid in pids:  # graceful first (lets Chrome flush session data)
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
        time.sleep(1.5)
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    except Exception:
        pass


def _launch_own_browser(pw, port: int):
    """Worker-thread only. Launch Aster's own browser and return
    (browser, context, mode, proc) — or raise RealProfileUnavailable.

    Uses the dedicated Aster profile on a real system Chrome/Edge (bundled
    Chromium is bot-blocked), launched on a port other than the CDP attach
    port so a lingering browser can never be mistaken for the attach target.
    With automation.use_real_profile the request is impossible (see
    RealProfileUnavailable) and fails loudly — never a silent logged-out guest.
    """
    import config as _cfgmod
    if bool(getattr(_cfgmod, "BROWSER_USE_REAL_PROFILE", False)):
        raise RealProfileUnavailable(_real_profile_message())
    headless = bool(getattr(_cfgmod, "BROWSER_HEADLESS", False))
    ok, why = _memory_headroom_ok()
    if not ok:
        raise InsufficientMemoryForBrowser(why)

    exe = None
    try:
        from tools.system import _find_app_path
        for name in ("chrome.exe", "msedge.exe"):
            exe = _find_app_path(name)
            if exe:
                break
    except Exception:
        exe = None

    _WEB_INFO["warning"] = ""
    own_port = _free_port(int(getattr(_cfgmod, "BROWSER_CDP_PORT", 9222)) + 1)
    profile = str(getattr(_cfgmod, "BROWSER_PROFILE_DIR", "") or "")

    if profile:
        result = _spawn_cdp_browser(pw, exe, profile, own_port, headless)
        if result:
            return result
        print("[DOM] could not open the dedicated profile's debug port")

    context = pw.chromium.launch_persistent_context(
        profile or None,
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-first-run",
              "--no-default-browser-check", "--window-size=1400,950", "--lang=en-US"],
    )
    print(f"[DOM] Launched bundled Chromium (headless={headless}, profile={profile})")
    return None, context, "own", None


def _is_web_alive(ctx) -> bool:
    """True while the cached browser/context is still usable.

    The owner can close the browser window at any time; a dead cached context
    otherwise makes every later browse/click fail (the 2026-09-25 'Amazon.eg
    appears to be down' report).
    """
    if not ctx:
        return False
    try:
        if ctx.get("mode") == "own":
            _ = ctx["context"].pages  # raises once the context is closed
            return True
        browser = ctx.get("browser")
        return bool(browser is not None and browser.is_connected())
    except Exception:
        return False


def _attach_impl():
    """Worker-thread only: get a browser context.

    1. Prefer attaching to a browser over CDP (keeps saved sessions).
    2. If that fails and DOM_MOTOR_OWN_BROWSER is on, launch our own browser on
       the dedicated profile.
    Raises RealProfileUnavailable when the owner configured the (impossible)
    real-profile mode. Stops a leaked Playwright driver on failure and
    rate-limits retries.
    """
    global _WEB, _WEB_FAILED_AT
    if _WEB is not None and (_WEB.get("browser") is not None or _WEB.get("context") is not None):
        if _is_web_alive(_WEB):
            return _WEB
        print("[DOM] cached browser is gone (window/tab closed); reconnecting")
        _detach_impl()
    if _WEB_FAILED_AT and (time.time() - _WEB_FAILED_AT) < _WEB_RETRY_S:
        return None
    pw = None
    try:
        import config as _cfgmod
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = None
        context = None
        mode = None
        proc = None
        try:
            browser = pw.chromium.connect_over_cdp(_cdp_url(), timeout=1500)
            context = browser.contexts[0]
            mode = "attach"
            _WEB_INFO["attached"] = True
        except Exception as attach_err:
            if not bool(getattr(_cfgmod, "DOM_MOTOR_OWN_BROWSER", True)):
                raise
            try:
                browser, context, mode, proc = _launch_own_browser(
                    pw, int(getattr(_cfgmod, "BROWSER_CDP_PORT", 9222)))
            except RealProfileUnavailable:
                try:
                    pw.stop()
                except Exception:
                    pass
                _WEB = None
                # No failure cooldown: this is a deterministic config refusal,
                # checked before anything launches — every call must hear it.
                _WEB_INFO["attached"] = False
                _WEB_INFO["warning"] = _real_profile_message()
                raise
            _WEB_INFO["attached"] = (mode == "own-launch")
            print(f"[DOM] CDP attach unavailable ({attach_err.__class__.__name__}); "
                  f"using Aster's own browser ({mode})")
        _WEB = {"pw": pw, "browser": browser, "context": context, "mode": mode,
                "proc": proc}
        _WEB_FAILED_AT = 0.0
        _WEB_INFO["mode"] = mode
        if mode == "attach":
            print(f"[DOM] Attached to browser via CDP at {_cdp_url()}")
        return _WEB
    except RealProfileUnavailable:
        raise
    except InsufficientMemoryForBrowser as e:
        # Deterministic refusal like RealProfileUnavailable: no failure cooldown
        # (a low-RAM refusal must not poison the retry window) and re-raised so
        # the caller hears the real reason instead of "is Chrome installed?".
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        _WEB = None
        _WEB_FAILED_AT = 0.0
        _WEB_INFO["attached"] = False
        _WEB_INFO["warning"] = str(e)
        print(f"[DOM] browser launch refused ({e})")
        raise
    except Exception as e:
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        _WEB = None
        _WEB_FAILED_AT = time.time()
        _WEB_INFO["attached"] = False
        print(f"[DOM] browser unavailable ({e.__class__.__name__}: {e}); "
              f"retry in {_WEB_RETRY_S:.0f}s")
        return None


def _detach_impl():
    """Worker-thread only: close our own browser (killing the process tree we
    spawned — a window close can otherwise leave background squatters), never
    a browser we only attached to. Also clears the failure cooldown so
    recovery can reconnect immediately."""
    global _WEB, _WEB_FAILED_AT
    if _WEB is not None:
        mode = _WEB.get("mode")
        if mode == "own" and _WEB.get("context") is not None:
            try:
                _WEB["context"].close()
            except Exception:
                pass
        if mode == "own-launch" and _WEB.get("browser") is not None:
            try:
                _WEB["browser"].close()  # CDP connect: disconnects only
            except Exception:
                pass
            try:
                import config as _cfgmod
                _kill_browser_tree(str(getattr(_cfgmod, "BROWSER_PROFILE_DIR", "") or ""))
            except Exception:
                pass
        try:
            _WEB["pw"].stop()
        except Exception:
            pass
        _WEB = None
    _WEB_FAILED_AT = 0.0
    _WEB_INFO["attached"] = False
    _WEB_INFO["mode"] = None


def _pages_impl(ctx) -> list:
    """All pages for either backend (attached browser or our own context)."""
    try:
        if ctx.get("mode") == "own":
            return list(ctx["context"].pages)
        return list(ctx["browser"].contexts[0].pages)
    except Exception as e:
        print(f"[DOM] page enumeration failed: {e}")
        return []


def _active_page_impl():
    """Worker-thread only. The page to act on, or None.

    Attached mode: only a page whose window actually has OS focus (avoids
    clicking a background tab while the owner works elsewhere). Own-browser
    mode: no OS-focus requirement — the browser belongs to Aster.
    """
    ctx = _attach_impl()
    if ctx is None:
        return None
    pages = [p for p in _pages_impl(ctx) if p.url != "about:blank"]
    _WEB_INFO["last_page_count"] = len(pages)
    if not pages:
        return None
    if ctx.get("mode") in ("own", "own-launch"):
        return pages[-1]
    focused = []
    for p in pages:
        try:
            if p.evaluate("document.hasFocus()"):
                focused.append(p)
        except Exception:
            continue
    return focused[-1] if focused else None


def active_page():
    """Focused page, or None (runs on the worker thread)."""
    return _web_call(_active_page_impl)


def close_web_context():
    """Detach Playwright WITHOUT closing the owner's browser."""
    global _WEB, _WEB_FAILED_AT, _WORKER
    worker = _WORKER
    if worker is not None and worker.alive():
        try:
            # 12 s: the detach kills our spawned browser tree (graceful taskkill
            # + 1.5 s wait + force) — 5 s aborted it mid-way and left squatters.
            worker.call(_detach_impl, timeout=12.0)
        except Exception:
            pass
        worker.stop()
    _WORKER = None
    _WEB = None
    _WEB_FAILED_AT = 0.0
    _WEB_INFO["attached"] = False


def _snapshot_impl(page) -> list:
    if page is None:
        return []
    try:
        try:
            text = page.aria_snapshot(mode="ai")
        except TypeError:  # older Playwright without the mode kwarg
            text = page.aria_snapshot()
        return parse_aria_snapshot(text)
    except Exception as e:
        print(f"[DOM] aria snapshot failed: {e}")
        return []


def snapshot_web(page=None, interactive_only: bool = False) -> list:
    """aria snapshot of the page -> nodes. [] on failure."""
    if page is not None:
        nodes = _snapshot_impl(page)
    else:
        nodes = _web_call(lambda: _snapshot_impl(_active_page_impl())) or []
    return filter_nodes(nodes) if interactive_only else nodes


def _role_for_playwright(role: str) -> str:
    return {"menubar": "menubar", "option": "option", "searchbox": "searchbox"}.get(role, role)


def resolve_locator(page, node: dict):
    """Re-resolve a node by role + accessible name at execution time.

    Returns (locator, ambiguous) or (None, False). Never uses refs — refs are
    snapshot-scoped and would let a stale snapshot scatter clicks.
    """
    role = node.get("role") or ""
    name = node.get("name") or ""
    try:
        if role and name and role not in ("other", "text"):
            try:
                loc = page.get_by_role(_role_for_playwright(role), name=name, exact=True)
                count = loc.count()
            except Exception:
                loc, count = None, 0
            if count == 0:
                loc = page.get_by_role(_role_for_playwright(role), name=name)
                count = loc.count()
            if count > 0:
                return (loc.first if count > 1 else loc), count > 1
        if name:
            loc = page.get_by_text(name, exact=True)
            count = loc.count()
            if count > 0:
                return (loc.first if count > 1 else loc), count > 1
            loc = page.get_by_text(name)
            count = loc.count()
            if count > 0:
                return (loc.first if count > 1 else loc), count > 1
    except Exception as e:
        print(f"[DOM] resolve_locator failed for {name!r}: {e}")
    return None, False


def _kernel_pick(shortlist, goal, op):
    """Stage-3: let the System-1 kernel (Laya) choose among the shortlist.

    Returns the chosen node, or None when the kernel is off / unavailable / low
    margin — the caller then keeps the top lexical match (pre-Stage-3 behavior).
    """
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return None
        verdict = system1.pick_element(goal, shortlist, op=op)
        if verdict.get("escalate"):
            print(f"[DOM] System-1 escalated ({verdict.get('reason')}); "
                  f"using top lexical match")
            return None
        index = verdict.get("index")
        if index is None or not (0 <= index < len(shortlist)):
            return None
        chosen = shortlist[index]
        margin = verdict.get("margin")
        margin_txt = "n/a" if margin is None else f"{margin:.2f}"
        print(f"[DOM] System-1 picked #{index} {chosen.get('name')!r} "
              f"(margin {margin_txt})")
        return chosen
    except Exception as e:
        print(f"[DOM] System-1 kernel unavailable: {e}")
        return None


def _click_locator(page, loc) -> None:
    """Click with a short timeout; fall back to force-click for overlay panes."""
    try:
        loc.click(timeout=3000)
    except Exception as e:
        print(f"[DOM] normal click failed ({e.__class__.__name__}), force-clicking")
        loc.click(timeout=3000, force=True)


def _is_visible(loc) -> bool:
    """Actionability pre-check: never attempt an invisible/zero-size candidate.

    Live amazon.eg failure (2026-09-25): the hidden keyboard-shortcut link
    'Search, ALT, forward slash' outranked the real submit button and the click
    burned a 3 s timeout + force-click. Unknown visibility returns True so we
    never make things worse than the pre-check behavior.
    """
    try:
        return bool(loc.is_visible())
    except Exception:
        return True


def _ordered_candidates(shortlist, preferred, limit=6):
    """Preferred (kernel) candidate first, then the rest, capped and deduped."""
    order = []
    if preferred is not None:
        order.append(preferred)
    for node in shortlist:
        if node is not preferred:
            order.append(node)
    return order[:max(1, limit)]


def _web_click_on_page(page, goal: str, confirm_send: bool = False):
    """Pure logic against a page object (test seam). Returns text or None.

    Tries candidates in order (kernel choice first, then lexical rank), skipping
    ones that cannot be resolved or are not visible, so a hidden look-alike does
    not consume the click.
    """
    if page is None:
        return None
    nodes = filter_nodes(_snapshot_impl(page))
    if not nodes:
        return None
    k, min_score = _motor_limits()
    shortlist = build_shortlist(nodes, goal, op="click", k=k, min_score=min_score)
    if not shortlist:
        return None
    preferred = _kernel_pick(shortlist, goal, "click")
    skipped = []
    for node in _ordered_candidates(shortlist, preferred):
        if _send_policy() == "confirm" and is_send_like(node) and not confirm_send:
            return (f"FAILED — BLOCKED: '{node.get('name')}' looks like a send/"
                    f"submit/destructive action and DOM_MOTOR_SEND_POLICY is "
                    f"'confirm'. Ask the owner to confirm, then call smart_click "
                    f"again with confirm_send=true.")
        loc, ambiguous = resolve_locator(page, node)
        if loc is None:
            skipped.append((node.get("name"), "unresolved"))
            continue
        if not _is_visible(loc):
            skipped.append((node.get("name"), "not visible"))
            continue
        try:
            pre_url = page.url
        except Exception:
            pre_url = None
        try:
            _click_locator(page, loc)
        except Exception as e:
            # Actionability failure (off-screen/covered/stale): try the next
            # ranked candidate instead of aborting the whole locate.
            skipped.append((node.get("name"), f"click failed: {e.__class__.__name__}"))
            print(f"[DOM] click failed for {node.get('name')!r}: {e.__class__.__name__}")
            continue
        try:
            page.wait_for_timeout(250)
            post_url = page.url
        except Exception:
            post_url = pre_url
        src = f"role={node.get('role')}, name={node.get('name')!r}"
        note = f"matched via dom-web: {src}, score {node.get('score', 0):.2f}"
        if ambiguous:
            note += " — AMBIGUOUS: multiple elements match this role+name; the first was clicked"
        if node.get("score", 0) < 0.65:
            note += " — LOW CONFIDENCE, the click may have hit the wrong element"
        if skipped:
            note += f" — skipped {len(skipped)} non-actionable candidate(s)"
        change = "" if post_url != pre_url else (
            " NOTE: the page URL did not change (normal for in-page actions).")
        return (f"Clicked '{goal}' in the browser ({note}).{change} "
                f"[Verify silently — continue with next action.]")
    if skipped:
        print(f"[DOM] no clickable candidate for {goal!r}; skipped {skipped}")
    return None


def _web_type_on_page(page, goal: str, text: str, submit: bool = False):
    """Pure logic against a page object (test seam). Returns text or None.

    submit=True presses Enter after filling (search boxes / form submits)."""
    if page is None:
        return None
    nodes = filter_nodes(_snapshot_impl(page))
    if not nodes:
        return None
    k, min_score = _motor_limits()
    shortlist = build_shortlist(nodes, goal, op="type", k=k, min_score=min_score)
    if not shortlist:
        return None
    preferred = _kernel_pick(shortlist, goal, "type")
    for node in _ordered_candidates(shortlist, preferred, limit=4):
        loc, ambiguous = resolve_locator(page, node)
        if loc is None or not _is_visible(loc):
            continue
        try:
            loc.fill(text, timeout=3000)
        except Exception:
            try:
                loc.click(timeout=3000)
                page.keyboard.type(text)
            except Exception as e:
                print(f"[DOM] type failed for {node.get('name')!r}: {e.__class__.__name__}")
                continue
        note = (f"matched via dom-web: role={node.get('role')}, "
                f"name={node.get('name')!r}, score {node.get('score', 0):.2f}")
        if ambiguous:
            note += " — AMBIGUOUS: multiple fields match; the first was used"
        if submit:
            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
                note += " — submitted with Enter"
            except Exception:
                pass
        return (f"Typed {text!r} into '{goal}' in the browser ({note}). "
                f"[Verify silently — continue with next action.]")
    return None


def _web_press_on_page(page, key: str):
    if page is None:
        return None
    try:
        page.keyboard.press(key)
        page.wait_for_timeout(150)
        return f"Pressed {key!r} in the browser."
    except Exception as e:
        print(f"[DOM] web_press failed: {e}")
        return None


def web_click(goal: str, confirm_send: bool = False):
    """Locate + click in the attached, focused browser. Returns result text or
    None ('motor unavailable / no confident match / browser not focused').

    60 s worker budget: a big page's aria snapshot + an optional first Laya load
    can exceed the default 12 s (observed on YouTube search).
    """
    try:
        return _web_call(lambda: _web_click_on_page(_active_page_impl(), goal, confirm_send),
                         timeout=60.0)
    except RealProfileUnavailable as e:
        return f"FAILED — {e}"


def web_type(goal: str, text: str, submit: bool = False):
    """Locate a field in the current browser page and fill it (optionally submit
    with Enter — for search boxes)."""
    try:
        return _web_call(lambda: _web_type_on_page(_active_page_impl(), goal, text, submit),
                         timeout=60.0)
    except RealProfileUnavailable as e:
        return f"FAILED — {e}"


def web_press(key: str):
    """Press a keyboard key in the attached, focused browser."""
    try:
        return _web_call(lambda: _web_press_on_page(_active_page_impl(), key), timeout=30.0)
    except RealProfileUnavailable as e:
        return f"FAILED — {e}"


# ---------------------------------------------------------------------------
# browse_web — navigate a URL (or site+query) and return the visible page text
# ---------------------------------------------------------------------------
_SITE_SEARCH = {
    "amazon": "https://www.{domain}/s?k={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "linkedin": "https://www.linkedin.com/search/results/people/?keywords={q}",
}
_SITE_HOME = {
    "amazon": "https://www.{domain}/",
    "wikipedia": "https://en.wikipedia.org/",
    "google": "https://www.google.com/",
    "youtube": "https://www.youtube.com/",
    "bing": "https://www.bing.com/",
    "linkedin": "https://www.linkedin.com/",
    "duckduckgo": "https://duckduckgo.com/",
}


def _human_search() -> bool:
    """automation.human_search — when on, site+query opens the site's home page
    and the model drives its real search bar (open site -> find the search box
    -> type -> submit -> read), like a human, instead of a pre-mapped search
    URL. The map becomes the rollback path (flag off)."""
    try:
        import config
        return bool(getattr(config, "BROWSER_HUMAN_SEARCH", False))
    except Exception:
        return False


def _resolve_target(url: str, query: str, site: str) -> str:
    """Turn (url | site+query | site-home | query) into a navigable http(s) URL.

    With no query, a known site resolves to its HOME page (e.g. site='youtube'
    → youtube.com) so 'check youtube recommendations' works, not an empty search.

    automation.human_search (BROWSER_HUMAN_SEARCH): site+query also resolves to
    the home page instead of the mapped search URL — the model then uses the
    site's own search bar interactively (the human flow). Browsing without a
    query never needed the map, so the flag only changes has_query=True.
    """
    url = (url or "").strip()
    if url:
        return url if "://" in url else "https://" + url
    s = (site or "").strip().lower()
    has_query = bool((query or "").strip())
    q = urllib.parse.quote_plus(query or "")
    if s and has_query and _human_search():
        if "." in s:  # a real domain -> its home page; the model uses the
            return s if "://" in s else "https://" + s  # site's own search bar
        for key, home in _SITE_HOME.items():  # bare known name -> home page
            if s.startswith(key):
                return (home.format(domain=s) if "{domain}" in home else home)
        # Unknown bare name falls through to the search below — the model
        # must LEARN the domain first (a guessed https://name is a dead host).
    for key in _SITE_SEARCH:
        if key in s:
            if key == "amazon":
                domain = s if "." in s else "amazon.com"
                return (_SITE_SEARCH[key].format(domain=domain, q=q) if has_query
                        else _SITE_HOME[key].format(domain=domain))
            return _SITE_SEARCH[key].format(q=q) if has_query else _SITE_HOME[key]
    if s:
        if "." in s:  # a real domain
            if has_query:
                return "https://www.bing.com/search?q=" + urllib.parse.quote_plus(f"site:{s} {query}")
            return s if "://" in s else "https://" + s
        # Bare name with no dot (e.g. "opencode"): search for it so the model
        # can read the results and follow the real URL, instead of a dead host.
        return "https://www.bing.com/search?q=" + urllib.parse.quote_plus(
            (s + " " + (query or "")).strip())
    return f"https://www.bing.com/search?q={q}"


def _pick_page(ctx):
    """Worker-thread only. The page to use for browse/read (raises if none)."""
    pages = [p for p in _pages_impl(ctx) if p.url != "about:blank"]
    if ctx.get("mode") in ("own", "own-launch"):
        return pages[-1] if pages else ctx["context"].new_page()
    focused = []
    for p in pages:
        try:
            if p.evaluate("document.hasFocus()"):
                focused.append(p)
        except Exception:
            continue
    page = focused[-1] if focused else (pages[-1] if pages else None)
    if page is None:
        page = ctx["context"].new_page()
    return page


def _page_text(page, max_chars: int, status=None) -> dict:
    """Read title + visible text of a page (no navigation)."""
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        page.wait_for_timeout(1000)
    title = page.title()
    text = page.inner_text("body")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"ok": True, "url": page.url, "title": title, "status": status,
            "warning": _WEB_INFO.get("warning") or "",
            "text": text[:max(500, int(max_chars))], "chars": len(text)}


def _browse_once(ctx, target: str, max_chars: int) -> dict:
    """Worker-thread only. Navigate + read. Raises on a stale/dead browser so the
    caller can reconnect and retry."""
    page = _pick_page(ctx)
    try:
        resp = page.goto(target, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        # Some sites challenge/download the first hit; retry on a fresh page.
        fresh = ctx["context"].new_page()
        resp = fresh.goto(target, wait_until="domcontentloaded", timeout=45000)
        page = fresh
    return _page_text(page, max_chars, status=getattr(resp, "status", None))


def _read_current(ctx, max_chars: int) -> dict:
    """Read the CURRENT page — used after smart_click/smart_type to review results."""
    return _page_text(_pick_page(ctx), max_chars)


def _browse_on_worker(url: str, query: str, site: str, max_chars: int):
    """Worker-thread only. One reconnect retry. With NO url/query/site this reads
    the CURRENT page (after clicks/typing) instead of navigating."""
    reading_current = not (url or query or site)
    target = None if reading_current else _resolve_target(url, query, site)
    last = "browser unavailable — is Chrome/Edge installed?"
    for attempt in (1, 2):
        try:
            ctx = _attach_impl()
        except (RealProfileUnavailable, InsufficientMemoryForBrowser) as e:
            # Deterministic refusals - no retry, no guest fallback.
            return {"ok": False, "error": str(e), "warning": str(e)}
        if ctx is None:
            return {"ok": False, "error": last}
        try:
            result = (_read_current(ctx, max_chars) if reading_current
                      else _browse_once(ctx, target, max_chars))
            if (isinstance(result, dict) and result.get("ok")
                    and _human_search() and (site or "").strip()
                    and not (url or "").strip()):
                # Tell the model this was the HUMAN-FLOW first step (land on the
                # site); the next action is its search bar, not giving up.
                result["human_flow"] = True
            return result
        except Exception as e:
            last = f"navigation failed: {e.__class__.__name__}: {e}"
            print(f"[DOM] browse attempt {attempt} failed ({e.__class__.__name__}); "
                  f"reconnecting")
            _detach_impl()
    return {"ok": False, "error": last}


def web_cookies(url: str) -> dict:
    """Cookie names Aster's browser holds for a URL (login_once / diagnostics).

    Returns {"ok": True, "names": [...]} (empty list when no browser/no
    cookies) or {"ok": False, "error": ...}. Never raises.
    """
    def _do():
        ctx = _attach_impl()
        if ctx is None:
            return None
        c = ctx["context"] or (ctx["browser"].contexts[0] if ctx.get("browser") else None)
        if c is None:
            return []
        return sorted({ck.get("name") for ck in c.cookies([url])})
    try:
        return {"ok": True, "names": _web_call(_do, timeout=15.0) or []}
    except RealProfileUnavailable as e:
        return {"ok": False, "error": str(e)}


def screenshot(path: str) -> bool:
    """Save a PNG of the current browser page (diagnostics / E2E). Never raises."""
    def _do():
        ctx = _attach_impl()
        if ctx is None:
            return False
        _pick_page(ctx).screenshot(path=path, full_page=False)
        return True
    try:
        # 60 s: heavy commerce pages (amazon.eg results) can exceed 30 s to render
        return bool(_web_call(_do, timeout=60.0))
    except RealProfileUnavailable:
        return False


def browse(url: str = "", query: str = "", site: str = "", max_chars: int = 6000):
    """Open a page in the motor's browser and return its visible text.

    Returns {"ok", "url", "title", "text", "chars"} or {"ok": False, "error"}.
    Runs on the Playwright worker thread; read-only (navigates a GET).
    """
    return _web_call(lambda: _browse_on_worker(url, query, site, max_chars),
                     timeout=90.0)


# ---------------------------------------------------------------------------
# Windows backend — shortlist over the UIA tree, with walk reuse
# ---------------------------------------------------------------------------
def windows_locate_cached(goal: str, op: str = None):
    """(result_or_None, raw_candidates, desktop_size) from one UIA walk.

    The caller can hand raw_candidates to tools.uia.uia_locate_candidates when
    the motor does not win, so Track 0 does not walk the tree a second time.
    Never raises.
    """
    try:
        from tools.uia import uia_nodes
        raw, desk = uia_nodes()
    except Exception as e:
        print(f"[DOM] UIA nodes unavailable: {e}")
        return None, [], None
    if not raw or not desk:
        return None, raw or [], desk
    k, min_score = _motor_limits()
    nodes = filter_nodes(uia_candidates_to_nodes(raw))
    sl = build_shortlist(nodes, goal, op=op, k=k, min_score=min_score,
                         screen=desk)
    if not sl or sl[0].get("score", 0.0) < min_score:
        return None, raw, desk
    best = _kernel_pick(sl, goal, op or "click") or sl[0]
    try:
        import pyautogui
        logical_w, logical_h = pyautogui.size()
        x1, y1, x2, y2 = best["bbox"]
        scale_x = logical_w / desk[0] if desk[0] else 1.0
        scale_y = logical_h / desk[1] if desk[1] else 1.0
        result = {
            "x": int((x1 + x2) / 2 * scale_x),
            "y": int((y1 + y2) / 2 * scale_y),
            "source": "dom-win",
            "text": best.get("name", ""),
            "score": round(best.get("score", 0.0), 2),
            "type": best.get("type") or best.get("role"),
        }
        return result, raw, desk
    except Exception as e:
        print(f"[DOM] locate_windows scale failed: {e}")
        return None, raw, desk


def locate_windows(goal: str, op: str = None) -> dict:
    """Best Windows match in the locator contract shape, or None."""
    result, _raw, _desk = windows_locate_cached(goal, op=op)
    return result


def windows_shortlist(goal: str, op: str = None, k: int = None,
                      min_score: float = 0.35):
    """(shortlist, desktop_size) from the live UIA tree. ([], None) on failure."""
    try:
        from tools.uia import uia_nodes
        raw, desk = uia_nodes()
    except Exception as e:
        print(f"[DOM] UIA nodes unavailable: {e}")
        return [], None
    nodes = filter_nodes(uia_candidates_to_nodes(raw))
    return build_shortlist(nodes, goal, op=op, k=k, min_score=min_score,
                           screen=desk), desk


# ---------------------------------------------------------------------------
# Status for diagnostics
# ---------------------------------------------------------------------------
def motor_status() -> dict:
    """Cheap, read-only status for health checks and QA (never raises, never
    blocks on a dead CDP port)."""
    status = {"dom_motor": False, "web_attached": bool(_WEB_INFO.get("attached")),
              "cdp_url": _cdp_url(), "uia_available": False,
              "worker_alive": bool(_WORKER is not None and _WORKER.alive()),
              "warning": _WEB_INFO.get("warning", "")}
    try:
        import config
        status["dom_motor"] = bool(getattr(config, "USE_DOM_MOTOR", False))
        status["send_policy"] = _send_policy()
        status["shortlist_k"] = int(getattr(config, "DOM_MOTOR_SHORTLIST_K", DEFAULT_SHORTLIST_K))
        status["min_score"] = float(getattr(config, "DOM_MOTOR_MIN_SCORE", DEFAULT_MIN_SCORE))
        status["use_real_profile"] = bool(getattr(config, "BROWSER_USE_REAL_PROFILE", False))
    except Exception:
        pass
    try:
        from tools.uia import auto as _auto
        status["uia_available"] = _auto is not None
    except Exception:
        pass
    return status
