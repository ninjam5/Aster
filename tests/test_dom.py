"""
DOM Motor Test Suite — Stage 1 of the DOM-first automation plan.

Per-feature test classes (Mohamed's standing preference):
  Feature 1 — ARIA snapshot parsing (Playwright YAML -> nodes)
  Feature 2 — structural filter + role/target rank + shortlist invariants
  Feature 3 — web execution safety (send gate, focused page, resolve, no refs)
  Feature 4 — Windows/UIA node conversion, shortlist, walk reuse
  Feature 5 — CDP lifecycle (leak-free failure, retry cooldown, focus gate)
  Feature 6 — browser debug launch (open_application path)
  Feature 7 — degradation guards (no browser/playwright -> None/[])

Run: pytest tests/test_dom.py -v
No browser, no models, no network — everything is synthetic or mocked.
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.dom as dom
from tools.dom import (
    _web_click_on_page,
    _web_type_on_page,
    best_match,
    build_shortlist,
    filter_nodes,
    is_interactive,
    is_send_like,
    motor_status,
    parse_aria_snapshot,
    rank_nodes,
    resolve_locator,
    snapshot_web,
    uia_candidates_to_nodes,
    web_click,
    web_type,
)


def _page_with(snapshot: str, url: str = "https://example.test/"):
    page = MagicMock()
    page.aria_snapshot.return_value = snapshot
    page.url = url
    return page


# ============================================================================
# Feature 1 — ARIA snapshot parsing
# ============================================================================

class TestAriaSnapshotParsing:
    def test_refs_names_and_roles(self):
        nodes = parse_aria_snapshot('- button "Submit" [ref=e7]\n- link "Settings" [ref=e11]')
        assert [(n["role"], n["name"], n["ref"]) for n in nodes] == [
            ("button", "Submit", "e7"),
            ("link", "Settings", "e11"),
        ]

    def test_nested_and_colon_forms(self):
        snap = (
            "- listitem:\n"
            '  - checkbox "Toggle Todo" [ref=e10]\n'
            '  - text: "Buy groceries"'
        )
        nodes = parse_aria_snapshot(snap)
        assert [n["role"] for n in nodes] == ["listitem", "checkbox", "text"]
        assert nodes[1]["name"] == "Toggle Todo" and nodes[1]["ref"] == "e10"
        assert nodes[2]["text"] == "Buy groceries"

    def test_flags_and_disabled(self):
        nodes = parse_aria_snapshot('- textbox "Search" [ref=e3] [disabled]')
        assert nodes[0]["disabled"] is True
        assert nodes[0]["ref"] == "e3"

    def test_escaped_quotes(self):
        nodes = parse_aria_snapshot('- button "Say \\"hi\\"" [ref=e1]')
        assert nodes[0]["name"] == 'Say "hi"'

    def test_ref_inside_name_is_not_the_element_ref(self):
        """A quoted name containing '[ref=zz]' must not shadow the real ref."""
        nodes = parse_aria_snapshot('- button "see [ref=zz] here" [ref=e7]')
        assert nodes[0]["ref"] == "e7"

    def test_crlf_and_empty_quoted_names(self):
        nodes = parse_aria_snapshot('- button "" [ref=a]\r\n- link "Ok" [ref=b]\r\n')
        assert nodes[0]["name"] == "" and nodes[1]["name"] == "Ok"

    def test_garbage_and_empty(self):
        assert parse_aria_snapshot("") == []
        assert parse_aria_snapshot("not a snapshot at all") == []

    def test_documented_mcp_snapshot_shape(self):
        """The exact shape Playwright MCP documents for AI consumption."""
        snap = (
            '- heading "todos" [level=1]\n'
            '- textbox "What needs to be done?" [ref=e5]\n'
            "- listitem:\n"
            '  - checkbox "Toggle Todo" [ref=e10]\n'
            '  - text: "Buy groceries"\n'
            '- link "All" [ref=e20]'
        )
        nodes = parse_aria_snapshot(snap)
        assert len(nodes) == 6
        assert nodes[1]["name"] == "What needs to be done?"
        assert nodes[1]["ref"] == "e5"
        assert nodes[3]["ref"] == "e10" and nodes[4]["text"] == "Buy groceries"


# ============================================================================
# Feature 2 — filter, rank, shortlist invariants
# ============================================================================

class TestFilterAndRank:
    def test_only_interactive_survive(self):
        nodes = parse_aria_snapshot(
            '- heading "Aster QA Fixture" [level=1]\n'
            '- textbox "Search" [ref=e3]\n'
            '- text: "idle"\n'
            '- button "Submit" [ref=e7]'
        )
        kept = filter_nodes(nodes)
        assert [n["role"] for n in kept] == ["textbox", "button"]

    def test_disabled_and_unnamed_dropped(self):
        nodes = parse_aria_snapshot('- textbox "Search" [disabled]\n- button ""')
        assert filter_nodes(nodes) == []

    def test_rank_prefers_exact_name(self):
        nodes = filter_nodes(parse_aria_snapshot(
            '- button "Cancel" [ref=a]\n'
            '- button "Submit" [ref=b]\n'
            '- button "Subtotal" [ref=c]'
        ))
        assert rank_nodes(nodes, "Submit button")[0]["name"] == "Submit"

    def test_op_affinity_picks_textbox_for_type(self):
        nodes = filter_nodes(parse_aria_snapshot(
            '- button "Open" [ref=a]\n- textbox "Open" [ref=b]'
        ))
        assert rank_nodes(nodes, "Open", op="type")[0]["role"] == "textbox"
        assert rank_nodes(nodes, "Open", op="click")[0]["role"] == "button"

    def test_spatial_hint_bias(self):
        nodes = [
            {"role": "button", "name": "File", "text": "", "ref": None,
             "bbox": (1700, 10, 1800, 40), "type": None, "source": "win", "score": 0.0},
            {"role": "button", "name": "File", "text": "", "ref": None,
             "bbox": (100, 900, 200, 930), "type": None, "source": "win", "score": 0.0},
        ]
        top = rank_nodes(nodes, "file menu at the top right", screen=(1920, 1080))[0]
        assert top["bbox"] == (1700, 10, 1800, 40)

    def test_shortlist_cap(self):
        nodes = filter_nodes(parse_aria_snapshot(
            "\n".join(f'- button "Save" [ref=e{i}]' for i in range(30))
        ))
        assert len(build_shortlist(nodes, "Save button", k=18)) == 18

    def test_min_score_rejects_nonsense(self):
        nodes = filter_nodes(parse_aria_snapshot('- button "Submit" [ref=a]'))
        assert build_shortlist(nodes, "zorblatt frobnicator") == []
        assert best_match(nodes, "zorblatt frobnicator") is None

    def test_best_match_accepts_clear_goal(self):
        nodes = filter_nodes(parse_aria_snapshot('- button "Submit" [ref=a]'))
        assert best_match(nodes, "Submit button") is not None

    def test_deterministic_order(self):
        nodes = filter_nodes(parse_aria_snapshot(
            '- button "Open" [ref=a]\n- button "Open" [ref=b]'
        ))
        first = [n["ref"] for n in build_shortlist(nodes, "Open")]
        second = [n["ref"] for n in build_shortlist(nodes, "Open")]
        assert first == second == ["a", "b"]


# ============================================================================
# Feature 3 — web execution safety
# ============================================================================

class TestWebExecutionSafety:
    def test_send_like_detection(self):
        assert is_send_like({"name": "Send", "text": ""}) is True
        assert is_send_like({"name": "Resend", "text": ""}) is True
        assert is_send_like({"name": "Forward", "text": ""}) is True
        assert is_send_like({"name": "Reply", "text": ""}) is True
        assert is_send_like({"name": "Delete account", "text": ""}) is True
        assert is_send_like({"name": "Submit", "text": ""}) is True
        assert is_send_like({"name": "Search", "text": ""}) is False
        assert is_send_like({"name": "Sensitivity", "text": ""}) is False

    def test_confirm_policy_blocks_send_before_click(self):
        page = _page_with('- button "Send" [ref=e1]')
        loc = MagicMock()
        page.get_by_role.return_value = loc
        with patch("config.DOM_MOTOR_SEND_POLICY", "confirm"):
            out = _web_click_on_page(page, "Send button")
        assert out.startswith("FAILED — BLOCKED")
        assert not loc.click.called

    def test_confirm_true_executes_after_owner_approval(self):
        page = _page_with('- button "Send" [ref=e1]')
        loc = MagicMock()
        loc.count.return_value = 1
        page.get_by_role.return_value = loc
        with patch("config.DOM_MOTOR_SEND_POLICY", "confirm"):
            out = _web_click_on_page(page, "Send button", confirm_send=True)
        assert out.startswith("Clicked")
        assert loc.click.called

    def test_allow_policy_executes_without_confirm(self):
        page = _page_with('- button "Send" [ref=e1]')
        loc = MagicMock()
        loc.count.return_value = 1
        page.get_by_role.return_value = loc
        with patch("config.DOM_MOTOR_SEND_POLICY", "allow"):
            out = _web_click_on_page(page, "Send button")
        assert out.startswith("Clicked")

    def test_invalid_policy_fails_closed(self):
        page = _page_with('- button "Send" [ref=e1]')
        with patch("config.DOM_MOTOR_SEND_POLICY", "confirmed"):
            out = _web_click_on_page(page, "Send button")
        assert out.startswith("FAILED — BLOCKED")

    def test_resolve_falls_back_to_text(self):
        page = MagicMock()
        role_loc = MagicMock()
        role_loc.count.return_value = 0
        text_loc = MagicMock()
        text_loc.count.return_value = 1
        page.get_by_role.return_value = role_loc
        page.get_by_text.return_value = text_loc
        node = {"role": "button", "name": "Weird Widget", "text": "", "ref": "e1",
                "bbox": None, "type": None, "source": "web", "score": 1.0}
        found, ambiguous = resolve_locator(page, node)
        assert found is text_loc and ambiguous is False

    def test_resolve_flags_ambiguous(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 2
        page.get_by_role.return_value = loc
        node = {"role": "button", "name": "Open", "text": "", "ref": "e1",
                "bbox": None, "type": None, "source": "web", "score": 1.0}
        found, ambiguous = resolve_locator(page, node)
        assert found is loc.first and ambiguous is True

    def test_web_type_fills_field(self):
        page = _page_with('- textbox "Search" [ref=e3]')
        loc = MagicMock()
        loc.count.return_value = 1
        page.get_by_role.return_value = loc
        out = _web_type_on_page(page, "Search box", "hello")
        assert out.startswith("Typed")
        assert loc.fill.called

    def test_no_match_returns_none_for_fallthrough(self):
        page = _page_with('- button "Submit" [ref=e1]')
        assert _web_click_on_page(page, "zorblatt frobnicator") is None
        assert _web_type_on_page(page, "zorblatt frobnicator", "x") is None

    def test_invisible_top_candidate_is_skipped(self):
        """Live amazon.eg regression: a hidden shortcut link outranked the real
        button and the click burned a timeout. Must skip to the next visible one."""
        page = _page_with('- button "Open" [ref=a]\n- button "Open" [ref=b]')
        first = MagicMock()
        first.count.return_value = 1
        first.is_visible.return_value = False
        second = MagicMock()
        second.count.return_value = 1
        second.is_visible.return_value = True
        side = [first, second]

        def resolver(_page, _node):
            return side.pop(0), False

        with patch.object(dom, "resolve_locator", side_effect=resolver):
            out = _web_click_on_page(page, "Open")
        assert out.startswith("Clicked")
        assert out.count("non-actionable") == 1
        assert second.click.called and not first.click.called

    def test_all_invisible_click_returns_none(self):
        page = _page_with('- button "Open" [ref=a]')
        loc = MagicMock()
        loc.count.return_value = 1
        loc.is_visible.return_value = False
        with patch.object(dom, "resolve_locator", return_value=(loc, False)):
            assert _web_click_on_page(page, "Open") is None

    def test_invisible_field_skipped_for_type(self):
        page = _page_with('- textbox "Search" [ref=a]')
        loc = MagicMock()
        loc.count.return_value = 1
        loc.is_visible.return_value = False
        with patch.object(dom, "resolve_locator", return_value=(loc, False)):
            assert _web_type_on_page(page, "Search", "x") is None


# ============================================================================
# Feature 4 — Windows/UIA nodes
# ============================================================================

class TestWindowsNodes:
    def test_uia_conversion_and_interactivity(self):
        nodes = uia_candidates_to_nodes([
            {"name": "Save", "type": "ButtonControl", "rect": (10, 10, 60, 30)},
            {"name": "Title", "type": "TextControl", "rect": (0, 0, 100, 20)},
        ])
        assert nodes[0]["role"] == "button" and nodes[0]["source"] == "win"
        assert nodes[0]["bbox"] == (10, 10, 60, 30)
        assert is_interactive(nodes[0]) and not is_interactive(nodes[1])

    def test_rank_uses_type_hint_bonus(self):
        nodes = filter_nodes(uia_candidates_to_nodes([
            {"name": "Save as", "type": "MenuItemControl", "rect": (0, 0, 50, 20)},
            {"name": "Save", "type": "ButtonControl", "rect": (0, 0, 50, 20)},
        ]))
        assert rank_nodes(nodes, "Save button", op="click")[0]["name"] == "Save"

    def test_bad_candidates_are_skipped(self):
        nodes = uia_candidates_to_nodes([
            {"name": "Broken", "type": "ButtonControl"},           # no rect
            {"name": "Good", "type": "ButtonControl", "rect": (1, 2, 3, 4)},
        ])
        assert [n["name"] for n in nodes] == ["Good"]

    def test_windows_locate_cached_reuses_walk(self):
        cands = [{"name": "Save", "type": "ButtonControl", "rect": (0, 0, 50, 20)}]
        with patch("tools.uia.uia_nodes", return_value=(cands, (1920, 1080))):
            result, raw, desk = dom.windows_locate_cached("Save button", op="click")
        assert result is not None and result["source"] == "dom-win"
        assert raw is cands and desk == (1920, 1080)

    def test_windows_locate_cached_miss_keeps_raw_for_reuse(self):
        cands = [{"name": "Save", "type": "ButtonControl", "rect": (0, 0, 50, 20)}]
        with patch("tools.uia.uia_nodes", return_value=(cands, (1920, 1080))):
            result, raw, desk = dom.windows_locate_cached("zorblatt frobnicator")
        assert result is None and raw is cands and desk == (1920, 1080)

    def test_uia_exact_scoring_accepts_reused_candidates(self):
        from tools.uia import uia_locate_candidates

        cands = [{"name": "Save", "type": "ButtonControl", "rect": (0, 0, 50, 20)}]
        result = uia_locate_candidates("Save button", cands, (1920, 1080))
        assert result is not None and result["source"] == "uia"


# ============================================================================
# Feature 5 — CDP lifecycle
# ============================================================================

class TestCdpLifecycle:
    def setup_method(self):
        dom._WEB = None
        dom._WEB_FAILED_AT = 0.0
        dom._WEB_INFO["attached"] = False
        dom._WEB_INFO["mode"] = None

    def teardown_method(self):
        dom._WEB = None
        dom._WEB_FAILED_AT = 0.0
        dom._WEB_INFO["attached"] = False
        dom._WEB_INFO["mode"] = None

    def test_attach_failure_stops_driver_and_rate_limits(self):
        driver = MagicMock()
        with patch("playwright.sync_api.sync_playwright") as sp, \
             patch("config.DOM_MOTOR_OWN_BROWSER", False):
            sp.return_value.start.return_value = driver
            driver.chromium.connect_over_cdp.side_effect = RuntimeError("refused")
            assert dom._attach_impl() is None
        assert driver.stop.called, "leaked Playwright driver must be stopped"
        assert dom._WEB_FAILED_AT > 0
        with patch("playwright.sync_api.sync_playwright") as sp2:
            assert dom._attach_impl() is None
            sp2.assert_not_called(), "cooldown must skip re-attempts"

    def test_own_browser_launched_when_attach_fails(self, tmp_path):
        driver = MagicMock()
        ctx = MagicMock()
        driver.chromium.connect_over_cdp.side_effect = RuntimeError("refused")
        driver.chromium.launch_persistent_context.return_value = ctx
        with patch("playwright.sync_api.sync_playwright") as sp, \
             patch("config.DOM_MOTOR_OWN_BROWSER", True), \
             patch("config.BROWSER_PROFILE_DIR", str(tmp_path / "prof")), \
             patch("tools.system._find_app_path", return_value=None):
            sp.return_value.start.return_value = driver
            got = dom._attach_impl()
        assert got is not None and got["mode"] == "own" and got["context"] is ctx
        assert driver.chromium.launch_persistent_context.called

    def test_attach_recovers_after_cooldown(self):
        dom._WEB_FAILED_AT = time.time() - (dom._WEB_RETRY_S + 1)
        browser = MagicMock()
        driver = MagicMock()
        driver.chromium.connect_over_cdp.return_value = browser
        with patch("playwright.sync_api.sync_playwright") as sp:
            sp.return_value.start.return_value = driver
            ctx = dom._attach_impl()
        assert ctx is not None and ctx["browser"] is browser
        assert dom._WEB_FAILED_AT == 0.0

    def test_active_page_requires_focus(self):
        page = MagicMock()
        page.url = "https://x.test/"
        page.evaluate.return_value = False
        browser = MagicMock()
        browser.contexts[0].pages = [page]
        fake_ctx = {"browser": browser}
        with patch.object(dom, "_attach_impl", return_value=fake_ctx):
            assert dom._active_page_impl() is None, "background tab must not be actionable"
        page.evaluate.return_value = True
        with patch.object(dom, "_attach_impl", return_value=fake_ctx):
            assert dom._active_page_impl() is page

    def test_active_page_skips_blank_tabs(self):
        blank = MagicMock()
        blank.url = "about:blank"
        real = MagicMock()
        real.url = "https://x.test/"
        real.evaluate.return_value = True
        browser = MagicMock()
        browser.contexts[0].pages = [blank, real]
        with patch.object(dom, "_attach_impl", return_value={"browser": browser}):
            assert dom._active_page_impl() is real


class TestBrowseNavigation:
    def test_url_passthrough_and_scheme(self):
        assert dom._resolve_target("https://x.test/a", "", "") == "https://x.test/a"
        assert dom._resolve_target("x.test", "", "") == "https://x.test"

    def test_amazon_site_query(self):
        assert dom._resolve_target("", "potatoes", "amazon.eg") == \
            "https://www.amazon.eg/s?k=potatoes"

    def test_wikipedia_site_query(self):
        assert "en.wikipedia.org" in dom._resolve_target("", "potato", "wikipedia")

    def test_bare_domain_restricts_web_search(self):
        target = dom._resolve_target("", "deals", "example.com")
        assert target.startswith("https://www.bing.com/search") and "site%3Aexample.com" in target

    def test_query_only_uses_bing(self):
        assert dom._resolve_target("", "potato price", "").startswith("https://www.bing.com/search")

    def test_site_home_when_no_query(self):
        """'check youtube recommendations' must open the homepage, not an empty search."""
        assert dom._resolve_target("", "", "youtube") == "https://www.youtube.com/"
        assert dom._resolve_target("", "", "youtube.com") == "https://www.youtube.com/"
        assert dom._resolve_target("", "", "amazon.eg") == "https://www.amazon.eg/"
        assert dom._resolve_target("", "", "en.wikipedia.org") == "https://en.wikipedia.org/"

    def test_bare_name_searches(self):
        """A site name that isn't a domain must search, not build a dead host."""
        target = dom._resolve_target("", "", "opencode")
        assert target.startswith("https://www.bing.com/search") and "opencode" in target
        assert dom._resolve_target("", "", "opencode.ai") == "https://opencode.ai"

    def test_linkedin_targets(self):
        assert dom._resolve_target("", "Magda Shahata", "linkedin") == \
            "https://www.linkedin.com/search/results/people/?keywords=Magda+Shahata"
        assert dom._resolve_target("", "", "linkedin") == "https://www.linkedin.com/"

    def test_human_search_opens_home_for_the_model_to_drive(self):
        """automation.human_search ON (the Astra pattern): site+query lands on
        the HOME page so the model drives the site's own search bar like a
        human, instead of a pre-mapped search URL."""
        with patch("config.BROWSER_HUMAN_SEARCH", True):
            t = dom._resolve_target("", "eggs", "amazon.eg")
            assert t.startswith("https://") and "amazon.eg" in t and "k=" not in t
            t = dom._resolve_target("", "eggs", "amazon")
            assert t.startswith("https://www.amazon")
            assert dom._resolve_target("", "jokra", "myntra.com") == "https://myntra.com"
            assert dom._resolve_target("", "Magda Shahata", "linkedin") == "https://www.linkedin.com/"
        # Flag OFF (conftest default): the mapped search URLs still exist.
        assert dom._resolve_target("", "eggs", "amazon.eg").endswith("/s?k=eggs")

    def test_human_search_unknown_bare_name_still_searches(self):
        """Discovery is not the map's job: an unknown bare name must search so
        the model learns the real domain — a guessed https://name is a dead host."""
        with patch("config.BROWSER_HUMAN_SEARCH", True):
            target = dom._resolve_target("", "", "opencode")
        assert target.startswith("https://www.bing.com/search") and "opencode" in target


class TestWebInteraction:
    def test_type_submit_presses_enter(self):
        page = _page_with('- searchbox "Search" [ref=e1]')
        loc = MagicMock()
        loc.count.return_value = 1
        page.get_by_role.return_value = loc
        out = dom._web_type_on_page(page, "the search box", "Magda Shahata", submit=True)
        assert out.startswith("Typed") and "submitted with Enter" in out
        page.keyboard.press.assert_called_with("Enter")

    def test_read_current_does_not_navigate(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/feed/"
        page.title.return_value = "Feed"
        page.inner_text.return_value = "Magda Shahata\nToronto, Canada"
        context = MagicMock()
        context.pages = [page]
        ctx = {"mode": "own", "context": context, "browser": None, "pw": MagicMock()}
        with patch.object(dom, "_attach_impl", return_value=ctx), \
             patch.object(dom, "_detach_impl"):
            result = dom._browse_on_worker("", "", "", 4000)
        assert result["ok"] is True and "Magda Shahata" in result["text"]
        page.goto.assert_not_called()


class TestWebLiveness:
    def test_alive_and_dead_attached_browser(self):
        alive = MagicMock()
        alive.is_connected.return_value = True
        dead = MagicMock()
        dead.is_connected.return_value = False
        assert dom._is_web_alive({"mode": "attach", "browser": alive}) is True
        assert dom._is_web_alive({"mode": "attach", "browser": dead}) is False

    def test_closed_persistent_context_detected(self):
        class _ClosedCtx:
            @property
            def pages(self):
                raise RuntimeError("Target page, context or browser has been closed")

        assert dom._is_web_alive({"mode": "own", "context": _ClosedCtx()}) is False

    def test_browse_recovers_after_closed_browser(self):
        """Owner closed the window → first ctx is dead, second (relaunched) works."""
        stale = {"mode": "attach", "browser": MagicMock(), "context": MagicMock()}
        page = MagicMock()
        page.url = "https://www.youtube.com/"
        page.title.return_value = "YouTube"
        page.inner_text.return_value = "Recommended\nVideo A\n\n\nVideo B"
        good_ctx = MagicMock()
        good_ctx.pages = [page]
        good = {"mode": "own", "context": good_ctx, "browser": None}
        seen = {"n": 0}

        def attach():
            seen["n"] += 1
            return stale if seen["n"] == 1 else good

        def pages_impl(ctx):
            if ctx is stale:
                raise RuntimeError("Target closed")
            return [page]

        with patch.object(dom, "_attach_impl", side_effect=attach), \
             patch.object(dom, "_pages_impl", side_effect=pages_impl), \
             patch.object(dom, "_detach_impl"):
            result = dom._browse_on_worker("", "", "youtube", 4000)
        assert result["ok"] is True and "Recommended" in result["text"]
        assert seen["n"] == 2, "must reconnect exactly once"

    def test_browse_reads_visible_text(self):
        page = MagicMock()
        page.url = "https://www.amazon.eg/s?k=potatoes"
        page.title.return_value = "Amazon.eg : potatoes"
        page.goto.return_value = MagicMock(status=200)
        page.inner_text.return_value = "Potatoes\nEGP 25.00\n\n\nSeed potatoes"
        context = MagicMock()
        context.pages = [page]
        ctx = {"mode": "own", "context": context, "browser": None, "pw": MagicMock()}
        with patch.object(dom, "_attach_impl", return_value=ctx):
            result = dom._browse_on_worker("", "potatoes", "amazon.eg", 6000)
        assert result["ok"] is True
        assert result["title"].startswith("Amazon") and "EGP 25.00" in result["text"]
        assert result["status"] == 200, "HTTP status is surfaced for up/down checks"
        assert "\n\n\n" not in result["text"], "blank-line runs are collapsed"
        page.goto.assert_called_once()


# ============================================================================
# Feature 6 — browser debug launch
# ============================================================================

class TestRealProfilePolicy:
    """use_real_profile is IMPOSSIBLE (Chrome >=136 blocks the debug port on the
    default user-data-dir; copies/junctions lose the app-bound logins). The
    contract: fail LOUDLY with the login_once alternative — never a silent
    logged-out guest fallback."""

    def setup_method(self):
        dom._WEB_INFO["warning"] = ""
        dom._WEB = None
        dom._WEB_FAILED_AT = 0.0

    def teardown_method(self):
        dom._WEB_INFO["warning"] = ""
        dom._WEB = None
        dom._WEB_FAILED_AT = 0.0

    def test_real_profile_request_raises_actionable_error(self):
        with patch("config.BROWSER_USE_REAL_PROFILE", True):
            try:
                dom._launch_own_browser(MagicMock(), 9222)
                raise AssertionError("must raise RealProfileUnavailable")
            except dom.RealProfileUnavailable as e:
                assert "login_once" in str(e), "must point at the one-time login flow"
                assert "use_real_profile" in str(e), "must say how to stop the error"

    def test_browse_returns_error_dict_not_guest(self):
        with patch("config.BROWSER_USE_REAL_PROFILE", True):
            result = dom._browse_on_worker("", "", "linkedin", 4000)
        assert result.get("ok") is False
        assert "login_once" in result.get("error", "")
        assert result.get("warning") == result.get("error")

    def test_browse_error_survives_the_worker_thread(self):
        """The public browse() entry must carry the refusal, not None."""
        with patch("config.BROWSER_USE_REAL_PROFILE", True):
            result = dom.browse(site="linkedin")
        assert isinstance(result, dict) and result.get("ok") is False
        assert "login_once" in result.get("error", "")

    def test_web_click_type_relay_the_refusal(self):
        with patch("config.BROWSER_USE_REAL_PROFILE", True):
            click_txt = dom.web_click("the Sign in button")
            type_txt = dom.web_type("the search bar", "hello")
        for txt in (click_txt, type_txt):
            assert txt and txt.startswith("FAILED") and "login_once" in txt

    def test_warning_surfaced_in_read_result(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/"
        page.title.return_value = "LinkedIn"
        page.inner_text.return_value = "Sign in"
        context = MagicMock()
        context.pages = [page]
        ctx = {"mode": "own", "context": context, "browser": None, "pw": MagicMock()}
        dom._WEB_INFO["warning"] = "used a fresh profile (no logins)"
        with patch.object(dom, "_attach_impl", return_value=ctx), \
             patch.object(dom, "_detach_impl"):
            result = dom._browse_on_worker("", "", "", 2000)
        assert "no logins" in result.get("warning", "")

    def test_spawn_passes_user_data_and_background_mode(self, tmp_path):
        driver = MagicMock()
        with patch("socket.create_connection", side_effect=OSError), \
             patch.object(dom, "_wait_cdp", return_value=True), \
             patch("subprocess.Popen") as popen:
            result = dom._spawn_cdp_browser(driver, r"C:\fake\chrome.exe",
                                            str(tmp_path / "ud"), 9222, False)
        assert result is not None and result[2] == "own-launch"
        assert result[3] is popen.return_value, "the spawned process must be tracked for cleanup"
        argv = popen.call_args[0][0]
        assert any(a.startswith("--user-data-dir=") for a in argv)
        assert "--disable-background-mode" in argv, "closed windows must not leave background squatters"
        assert "--remote-allow-origins=*" in argv

    def test_dedicated_launch_works_and_returns_proc(self, tmp_path):
        """use_real_profile OFF: the dedicated profile launch still works."""
        driver = MagicMock()
        sentinel = (MagicMock(), MagicMock(), "own-launch", MagicMock())
        with patch("config.BROWSER_USE_REAL_PROFILE", False), \
             patch("config.BROWSER_PROFILE_DIR", str(tmp_path / "prof")), \
             patch("tools.system._find_app_path") as find, \
             patch.object(dom, "_spawn_cdp_browser", return_value=sentinel) as spawn:
            find.side_effect = lambda name: r"C:\fake\chrome.exe" if name == "chrome.exe" else None
            result = dom._launch_own_browser(driver, 9222)
        assert result == sentinel
        assert spawn.call_args[0][2] == str(tmp_path / "prof")

    def test_detach_kills_spawned_guest_tree(self):
        browser = MagicMock()
        dom._WEB = {"pw": MagicMock(), "browser": browser, "context": MagicMock(),
                    "mode": "own-launch", "proc": MagicMock()}
        with patch("config.BROWSER_PROFILE_DIR", r"C:\fake\Aster_Vault\browser_profile"), \
             patch.object(dom, "_kill_browser_tree") as kill:
            dom._detach_impl()
        kill.assert_called_once_with(r"C:\fake\Aster_Vault\browser_profile")
        browser.close.assert_called_once()
        assert dom._WEB is None

    def test_detach_never_kills_attached_browser(self):
        browser = MagicMock()
        dom._WEB = {"pw": MagicMock(), "browser": browser, "context": MagicMock(),
                    "mode": "attach", "proc": None}
        with patch.object(dom, "_kill_browser_tree") as kill:
            dom._detach_impl()
        kill.assert_not_called()
        browser.close.assert_not_called()

    def test_kill_browser_tree_matches_only_our_profile(self):
        """Kills exactly the processes whose --user-data-dir is OUR dedicated
        dir — never the owner's browser (no/other user-data-dir)."""
        ours = MagicMock(pid=101)
        theirs = MagicMock(pid=202)
        procs = [
            ("chrome.exe", ["chrome.exe", "--user-data-dir=C:\\fake\\Aster_Vault\\browser_profile"], ours),
            ("chrome.exe", ["chrome.exe"], theirs),  # owner's default-dir Chrome
            ("msedge.exe", ["msedge.exe", "--user-data-dir=C:\\some\\other"], MagicMock(pid=303)),
            ("notepad.exe", ["notepad.exe"], MagicMock(pid=404)),
        ]

        def fake_iter(_attrs):
            for name, cmd, proc in procs:
                info = {"name": name, "cmdline": cmd}
                proc.info = info
                yield proc

        with patch("psutil.process_iter", side_effect=fake_iter), \
             patch("subprocess.run") as run:
            dom._kill_browser_tree(r"C:\fake\Aster_Vault\browser_profile")
        killed = {call.args[0][2] for call in run.call_args_list}
        assert killed == {"101"}, "only the process on OUR profile dir may die"

    def test_web_cookies_reports_refusal(self):
        with patch("config.BROWSER_USE_REAL_PROFILE", True):
            r = dom.web_cookies("https://www.linkedin.com")
        assert r.get("ok") is False and "login_once" in r.get("error", "")


class TestBrowserDebugLaunch:
    def test_alias_and_registry_launch_dedicated_profile(self, tmp_path):
        from tools import system

        with patch.object(system, "_find_app_path", return_value=r"C:\fake\chrome.exe"), \
             patch.object(system.config, "USE_DOM_MOTOR", True), \
             patch.object(system.config, "BROWSER_USE_REAL_PROFILE", False), \
             patch.object(system.config, "BROWSER_PROFILE_DIR", str(tmp_path / "prof")), \
             patch.object(system.subprocess, "Popen") as popen:
            assert system._launch_browser_with_debug("Google Chrome") is True
        argv = popen.call_args[0][0]
        assert argv[0] == r"C:\fake\chrome.exe"
        assert any("--remote-debugging-port=" in a for a in argv)
        assert any(a.startswith(f"--user-data-dir={tmp_path / 'prof'}") for a in argv), \
            "debug launches must use the DEDICATED profile (real profile is impossible)"
        assert "--disable-background-mode" in argv

    def test_real_profile_mode_falls_back_to_normal_launch(self):
        """The real profile cannot be debugged — open the owner's browser the
        normal way instead of a dead debug attempt."""
        from tools import system

        with patch.object(system, "_find_app_path", return_value=r"C:\fake\chrome.exe"), \
             patch.object(system.config, "USE_DOM_MOTOR", True), \
             patch.object(system.config, "BROWSER_USE_REAL_PROFILE", True), \
             patch.object(system.subprocess, "Popen") as popen:
            assert system._launch_browser_with_debug("chrome") is False
        popen.assert_not_called()

    def test_flag_off_is_noop(self):
        from tools import system

        with patch.object(system.config, "USE_DOM_MOTOR", False):
            assert system._launch_browser_with_debug("chrome") is False

    def test_unknown_app_falls_through(self):
        from tools import system

        with patch.object(system.config, "USE_DOM_MOTOR", True):
            assert system._launch_browser_with_debug("notepad") is False


class TestLoginOnce:
    """The one-time login flow is the ONLY supported way to browse logged-in."""

    def test_every_site_has_a_detection_method(self):
        import login_once
        for site, cfg in login_once.SITES.items():
            assert cfg.get("login_url"), site
            assert cfg.get("cookie") or cfg.get("marker"), \
                f"{site} needs a cookie name or a page-text marker"

    def test_check_cookie_mode(self):
        import login_once
        with patch.object(login_once.dom, "web_cookies",
                          return_value={"ok": True, "names": ["li_at", "bcookie"]}):
            assert login_once.check("linkedin") == {"logged_in": True, "error": None}
        with patch.object(login_once.dom, "web_cookies",
                          return_value={"ok": True, "names": ["bcookie"]}):
            assert login_once.check("linkedin")["logged_in"] is False

    def test_check_marker_mode(self):
        import login_once
        with patch.object(login_once.dom, "browse",
                          return_value={"ok": True, "text": "Chats\nSearch or start a new chat"}):
            assert login_once.check("whatsapp") == {"logged_in": True, "error": None}

    def test_check_relay_refusal_error(self):
        import login_once
        with patch.object(login_once.dom, "web_cookies",
                          return_value={"ok": False, "error": "use login_once"}):
            r = login_once.check("linkedin")
            assert r["logged_in"] is False and r["error"]

    def test_missing_exe_falls_through(self):
        from tools import system

        with patch.object(system.config, "USE_DOM_MOTOR", True), \
             patch.object(system, "_find_app_path", return_value=None), \
             patch("shutil.which", return_value=None):
            assert system._launch_browser_with_debug("chrome") is False


# ============================================================================
# Feature 7 — degradation guards
# ============================================================================

class TestDegradation:
    def test_motor_status_shape(self):
        status = motor_status()
        assert isinstance(status, dict)
        assert {"dom_motor", "web_attached", "cdp_url", "uia_available",
                "worker_alive", "send_policy"} <= set(status)

    def test_web_wrappers_degrade_without_page(self):
        with patch.object(dom, "_web_call", side_effect=lambda fn, timeout=12.0: fn()), \
             patch.object(dom, "_active_page_impl", return_value=None):
            assert web_click("Submit button") is None
            assert web_type("Search", "hello") is None

    def test_snapshot_web_without_page(self):
        with patch.object(dom, "_web_call", side_effect=lambda fn, timeout=12.0: fn()), \
             patch.object(dom, "_active_page_impl", return_value=None):
            assert snapshot_web() == []


class TestBrowserMemoryGuard:
    """Feature 8 - low-RAM guard before launching Aster's browser (2026-09-28).

    Launching Chrome beside llama-server's mmap'd MoE experts OOM'd the box
    (2.1 GB free, commit 41.9/47.5). The guard refuses deterministically with a
    readable message instead of dying mid-turn.
    """

    def test_headroom_ok_when_ram_is_ample(self):
        fake = MagicMock()
        fake.virtual_memory.return_value = MagicMock(available=8 * 1024 ** 3)
        with patch.dict(sys.modules, {"psutil": fake}):
            ok, why = dom._memory_headroom_ok()
        assert ok is True and why == ""

    def test_headroom_blocks_when_ram_is_low(self):
        fake = MagicMock()
        fake.virtual_memory.return_value = MagicMock(available=int(0.4 * 1024 ** 3))
        with patch.dict(sys.modules, {"psutil": fake}):
            ok, why = dom._memory_headroom_ok()
        assert ok is False
        assert "RAM free" in why and "close some applications" in why

    def test_launch_own_browser_refuses_when_ram_is_low(self):
        with patch.object(dom, "_memory_headroom_ok", return_value=(False, "low RAM")):
            try:
                dom._launch_own_browser(MagicMock(), 1234)
            except dom.InsufficientMemoryForBrowser as e:
                assert "low RAM" in str(e)
            else:
                raise AssertionError("expected InsufficientMemoryForBrowser")

    def test_browse_returns_failure_without_retry_on_low_ram(self):
        with patch.object(dom, "_attach_impl",
                          side_effect=dom.InsufficientMemoryForBrowser("low RAM")):
            with patch.object(dom, "_web_call", side_effect=lambda fn, timeout=None: fn()):
                out = dom._browse_on_worker("https://example.com", "", "", 500)
        assert out["ok"] is False
        assert "low RAM" in out["error"]
