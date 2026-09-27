"""End-to-end harness for the DOM motor (+ optional Laya System-1 kernel).

Drives a real Chromium over CDP (the production attach path) and exercises
`tools/dom.py` + `core/system1.py` on a live task. This is the artifact the
Stage-1/3 live QA checklists call for.

Usage (repo root):
  python engine_testing/e2e_dom_motor.py inspect [--headed] [--goal "search box"] [--op type]
      Launch, navigate to the task start page, dump interactive aria nodes and
      the shortlist the motor would hand to the decision step.

  python engine_testing/e2e_dom_motor.py run [--headed] [--query ball]
      Full task: open amazon.eg -> search -> read the first result's price,
      routing element decisions through the production motor (Laya if enabled).

The scratch browser uses a TEMP profile and a dedicated debug port; it never
touches the owner's real Chrome profile.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

START_URL = "https://www.amazon.eg"
UA_HINT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")


def _wait_port(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def _chromium_exe() -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        return p.chromium.executable_path


def launch(port: int, headed: bool):
    user_data = tempfile.mkdtemp(prefix="aster-e2e-profile-")
    args = [
        _chromium_exe(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=Translate",
        "--lang=en-US",
        "--window-size=1400,950",
        "about:blank",
    ]
    if not headed:
        args.append("--headless=new")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _wait_port(port):
        proc.terminate()
        raise RuntimeError(f"debug port {port} did not open")
    return proc, user_data


def _connect(port: int):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(20000)
    return pw, page


def _install_stealth(page):
    """Reduce the most obvious automation tells (no Chrome launch flags can be
    changed after attach, so patch the JS surface instead)."""
    try:
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
    except Exception:
        pass


def _dismiss_overlays(page):
    """Cookie / language interstitials on amazon.eg, best-effort."""
    for sel in ("#sp-cc-accept", "input#sp-cc-accept", "button#sp-cc-accept",
                "input[aria-labelledby='sp-cc-accept']"):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2000)
                page.wait_for_timeout(500)
                print("[e2e] dismissed cookie overlay")
                return
        except Exception:
            pass


def _page_ready(page) -> str:
    try:
        return page.title() or ""
    except Exception:
        return ""


def inspect(page, goal, op):
    import tools.dom as dom
    nodes = dom._snapshot_impl(page)
    interactive = dom.filter_nodes(nodes)
    print(f"[inspect] {len(nodes)} aria nodes, {len(interactive)} interactive")
    for n in interactive[:40]:
        print(f"  {n['role']:12s} {n['name']!r}")
    shortlist = dom.build_shortlist(interactive, goal, op=op)
    print(f"\n[inspect] shortlist for goal={goal!r} op={op!r}: {len(shortlist)}")
    for n in shortlist[:18]:
        print(f"  {n['score']:.2f}  {n['role']:12s} {n['name']!r}")


def run_task(page, query, headed):
    import config
    import tools.dom as dom
    import core.system1 as s1

    print(f"[e2e] kernel_enabled={s1.kernel_enabled()} "
          f"model={getattr(config, 'LAYA_MODEL_ID', '?')}")

    page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
    page.bring_to_front()
    page.wait_for_timeout(2500)
    _dismiss_overlays(page)
    title = _page_ready(page)
    print(f"[e2e] opened {START_URL} -> title={title!r} url={page.url}")

    if any(k in title.lower() for k in ("robot", "sorry", "captcha")):
        raise RuntimeError(f"bot challenge on landing page: title={title!r}")

    # ── Step 0: Laya decides the next operation (kernel demonstration) ───────
    op_decision = s1.pick_operation(f"search amazon.eg for a {query}")
    print(f"[e2e] Laya pick_operation(landing) -> {op_decision['choice']} "
          f"margin={op_decision['margin']} escalate={op_decision['escalate']}")

    # ── Step 1: the search box, via the production motor ────────────────────
    typed = dom.web_type("search box", query)
    print(f"[e2e] motor web_type('search box', {query!r}) -> {typed}")
    if not typed:
        print("[e2e] motor could not find the search box; inspecting DOM names:")
        inspect(page, "search", "type")
        raise RuntimeError("search box not found by the motor")

    # ── Step 2: submit, via the production motor; then Enter fallback ───────
    # A planner that can see the page targets the button by its label ("Go").
    clicked = dom.web_click("Go", confirm_send=True)
    print(f"[e2e] motor web_click('Go') -> {clicked}")
    if not clicked:
        clicked = dom.web_click("search button", confirm_send=True)
        print(f"[e2e] motor web_click('search button') -> {clicked}")
    if not clicked:
        print("[e2e] no motor match; pressing Enter (code path)")
        page.keyboard.press("Enter")
    page.wait_for_timeout(4000)
    page.bring_to_front()
    print(f"[e2e] results page: title={page.title()!r} url={page.url}")

    # ── Step 3: deterministic extraction of the first result's price ────────
    results = page.locator("div[data-component-type='s-search-result']")
    count = results.count()
    print(f"[e2e] result cards: {count}")
    if count == 0:
        # Save evidence and report the page state honestly.
        shot = os.path.join(HERE, "qa", "artifacts", "e2e_amazon_no_results.png")
        os.makedirs(os.path.dirname(shot), exist_ok=True)
        page.screenshot(path=shot, full_page=False)
        raise RuntimeError(f"no result cards (saved {shot})")

    first = results.first
    title_txt = ""
    for sel in ("h2 span", "h2 a span", "[data-cy='title-recipe'] span", "h2"):
        try:
            if first.locator(sel).count():
                title_txt = first.locator(sel).first.inner_text().strip()
                if title_txt:
                    break
        except Exception:
            continue
    price_txt = ""
    for sel in (".a-price .a-offscreen", ".a-price-whole", ".a-price"):
        try:
            if first.locator(sel).count():
                price_txt = first.locator(sel).first.inner_text().strip()
                if price_txt:
                    break
        except Exception:
            continue

    print(f"[e2e] FIRST RESULT: {title_txt!r}  PRICE: {price_txt!r}")

    # ── Step 4: Laya state gate on the real page state ──────────────────────
    state_text = f"url={page.url}\ntitle={page.title()}\nfirst_card={title_txt[:160]}"
    verdict = s1.check_state(
        "Are we on Amazon search results for the requested item?",
        "yes, this is a search results page with product cards",
        "no, this is not a search results page",
        state_text=state_text,
    )
    print(f"[e2e] Laya check_state -> answer={verdict['answer']} "
          f"margin={verdict['margin']} escalate={verdict['escalate']}")

    result = {
        "task": "Go to amazon.eg and check how much a new ball is",
        "query": query,
        "url": page.url,
        "title": page.title(),
        "first_product": title_txt,
        "price": price_txt,
        "results_count": count,
        "laya_state_gate": {"answer": verdict["answer"], "margin": verdict["margin"],
                            "escalate": verdict["escalate"]},
        "laya_operation": {"choice": op_decision["choice"], "margin": op_decision["margin"],
                           "escalate": op_decision["escalate"]},
    }
    out = os.path.join(HERE, "qa", "artifacts", "e2e_amazon_result.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[e2e] wrote {out}")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["inspect", "run"])
    ap.add_argument("--headed", action="store_true", default=True)
    ap.add_argument("--headless", dest="headed", action="store_false")
    ap.add_argument("--goal", default="search box")
    ap.add_argument("--op", default="type")
    ap.add_argument("--query", default="ball")
    ap.add_argument("--port", type=int, default=9222)
    args = ap.parse_args()

    import config
    port = args.port or int(getattr(config, "BROWSER_CDP_PORT", 9222))
    proc, _udd = launch(port, args.headed)
    pw = None
    try:
        pw, page = _connect(port)
        _install_stealth(page)
        if args.mode == "inspect":
            page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
            page.bring_to_front()
            page.wait_for_timeout(2500)
            _dismiss_overlays(page)
            print(f"[e2e] title={page.title()!r} url={page.url}")
            inspect(page, args.goal, args.op)
        else:
            run_task(page, args.query, args.headed)
    finally:
        try:
            import tools.dom as dom
            dom.close_web_context()
        except Exception:
            pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    main()
