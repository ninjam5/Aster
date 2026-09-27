"""One-time login helper for Aster's dedicated browser profile.

Chrome >=136 makes it impossible to debug-drive the owner's real browser
profile (the port is ignored on the default user-data-dir, and any copy or
link of that dir loses the app-bound encrypted logins — verified
2026-09-26, see .claude/skills/aster-failure-archaeology). The supported way
for Aster to browse LOGGED IN is a one-time manual login inside Aster's own
persistent browser profile (automation.browser_profile_dir): the sessions are
saved on disk and every later browse_web / smart_click uses them.

Usage:
  python login_once.py linkedin     # opens the LinkedIn login page in Aster's
                                    # browser; sign in once in that window
  python login_once.py --status     # shows which known sites have sessions

The window closes itself once the session cookie appears (or after 15 min).
Ctrl+C leaves the browser open so you can finish logging in.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools.dom as dom  # noqa: E402

# site -> how to open the login page and how to detect a saved session.
# cookie-mode sites are checked without touching the page (web_cookies);
# marker-mode sites (WhatsApp Web: the session lives in IndexedDB) are checked
# by reading the current page text for a logged-in marker.
SITES = {
    "linkedin": {"login_url": "https://www.linkedin.com/login",
                 "check_url": "https://www.linkedin.com", "cookie": "li_at"},
    "google":   {"login_url": "https://accounts.google.com",
                 "check_url": "https://www.google.com", "cookie": "SID"},
    "youtube":  {"login_url": "https://www.youtube.com",
                 "check_url": "https://www.youtube.com", "cookie": "SID"},
    "github":   {"login_url": "https://github.com/login",
                 "check_url": "https://github.com", "cookie": "user_session"},
    "whatsapp": {"login_url": "https://web.whatsapp.com",
                 "check_url": None, "cookie": None,
                 "marker": "Search or start a new chat"},
}

WAIT_MINUTES = 15
POLL_SECONDS = 5


def check(site: str) -> dict:
    """{"logged_in": bool, "error": str|None} for a known site."""
    cfg = SITES[site]
    if cfg.get("cookie"):
        r = dom.web_cookies(cfg["check_url"])
        if not r.get("ok"):
            return {"logged_in": False, "error": r.get("error")}
        return {"logged_in": cfg["cookie"] in (r.get("names") or []),
                "error": None}
    r = dom.browse()  # read the CURRENT page (no navigation)
    if not r.get("ok"):
        return {"logged_in": False, "error": r.get("error")}
    marker = cfg.get("marker") or ""
    return {"logged_in": marker in (r.get("text") or ""), "error": None}


def print_status() -> None:
    print("Saved sessions in Aster's browser profile:")
    for site in SITES:
        r = check(site)
        if r["error"]:
            state = f"error: {r['error'][:100]}"
        else:
            state = "LOGGED IN" if r["logged_in"] else "not logged in"
        print(f"  {site:<10} {state}")
    dom.close_web_context()


def run_login(site: str) -> int:
    cfg = SITES[site]
    print(f"Opening {site} login in Aster's browser profile...")
    r = dom.browse(url=cfg["login_url"])
    if not r.get("ok"):
        print(f"Could not open the login page: {r.get('error')}")
        return 1
    print("Sign in inside the window that just opened.")
    print(f"Waiting up to {WAIT_MINUTES} minutes (Ctrl+C leaves it open)...")
    deadline = time.time() + WAIT_MINUTES * 60
    next_note = time.time() + 30
    try:
        while time.time() < deadline:
            time.sleep(POLL_SECONDS)
            c = check(site)
            if c["error"]:
                print(f"Check failed: {c['error']}")
                return 1
            if c["logged_in"]:
                print(f"\n{site} session saved — every later browse_web "
                      f"browses {site} logged in. Closing the window...")
                time.sleep(3)
                dom.close_web_context()
                return 0
            if time.time() >= next_note:
                left = int((deadline - time.time()) / 60)
                print(f"  still waiting for the {site} login... ({left} min left)")
                next_note = time.time() + 30
        print(f"Timed out after {WAIT_MINUTES} minutes — the browser stays open; "
              f"run 'python login_once.py --status' later to verify.")
        return 1
    except KeyboardInterrupt:
        print(f"\nInterrupted — the browser stays open. Finish logging in and "
              f"run 'python login_once.py --status' later.")
        return 1


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--status":
        print_status()
        return 0
    site = argv[0].strip().lower()
    if site not in SITES:
        print(f"Unknown site {site!r}. Known: {', '.join(SITES)}")
        return 1
    return run_login(site)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
