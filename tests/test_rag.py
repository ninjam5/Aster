"""Tests for the research tool (tools/rag.py) after the 2026-09-28 incident.

The live failure: "did the prime minister of israel attend the United nations
meeting on september 27th 2026?" was answered from the Wikipedia article on
Matteo Renzi, cached under the question's filename for 90 days, and live web
search was silently dead (empty key + v1 SDK result access on a v2 SDK).

These tests are offline: every network path is mocked.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.rag as rag

RENZI_Q = "Israeli Prime Minister Netanyhau United Nations General Assembly September 27 2026"


# ── current-events routing ────────────────────────────────────────────────────

class TestCurrentEventsRouting:
    def test_current_year_is_time_sensitive(self):
        assert rag._is_current_events(RENZI_Q) is True

    def test_future_year_is_time_sensitive(self):
        assert rag._is_current_events("the 2027 election results") is True

    def test_recency_words_are_time_sensitive(self):
        for t in ["latest news on fusion", "what happened today", "current GPU prices",
                  "breaking: market crash", "this week in tech"]:
            assert rag._is_current_events(t) is True, t

    def test_past_year_is_not_time_sensitive(self):
        assert rag._is_current_events("who won the 2024 US election") is False

    def test_evergreen_topics_are_not_time_sensitive(self):
        for t in ["Newton's laws of motion", "RTX 5060", "quantum computing"]:
            assert rag._is_current_events(t) is False, t


# ── Wikipedia relevance gate ──────────────────────────────────────────────────

class TestWikipediaRelevance:
    def test_renzi_is_rejected(self):
        assert rag._wiki_title_relevant("Matteo Renzi", RENZI_Q) is False

    def test_related_titles_are_accepted(self):
        for title in ["Prime Minister of Israel", "Benjamin Netanyahu", "Israel",
                      "United Nations General Assembly"]:
            assert rag._wiki_title_relevant(title, RENZI_Q) is True, title

    def test_typo_tolerance(self):
        assert rag._tokens_match("netanyhau", "netanyahu") is True

    def test_prefix_tolerance(self):
        assert rag._tokens_match("israel", "israeli") is True

    def test_unrelated_tokens_do_not_match(self):
        assert rag._tokens_match("renzi", "israel") is False
        assert rag._tokens_match("italy", "quantum") is False

    def test_encyclopedic_sanity(self):
        assert rag._wiki_title_relevant("Prime Minister of Italy", "who is the prime minister of italy") is True
        assert rag._wiki_title_relevant("Newton's laws of motion", "Newton laws of motion") is True
        assert rag._wiki_title_relevant("Quantum computing", "quantum computing") is True


# ── Firecrawl v2 result parsing ───────────────────────────────────────────────

class TestFirecrawlResultFields:
    def _document(self):
        m = MagicMock()
        m.model_dump.return_value = {
            "markdown": "body text",
            "metadata": {"title": "Israel - General Debate, 81st Session",
                         "url": "https://webtv.un.org/x", "source_url": "https://webtv.un.org/x"},
        }
        return m

    def _web_result(self):
        m = MagicMock()
        m.model_dump.return_value = {"title": "T", "url": "https://e.com", "description": "desc"}
        return m

    def test_document_shape(self):
        title, url, body = rag._firecrawl_result_fields(self._document())
        assert title == "Israel - General Debate, 81st Session"
        assert url == "https://webtv.un.org/x"
        assert body == "body text"

    def test_search_result_web_shape(self):
        title, url, body = rag._firecrawl_result_fields(self._web_result())
        assert (title, url, body) == ("T", "https://e.com", "desc")

    def test_plain_dict(self):
        title, url, body = rag._firecrawl_result_fields(
            {"title": "t", "url": "u", "markdown": "m"})
        assert (title, url, body) == ("t", "u", "m")

    def test_unknown_object_is_safe(self):
        assert rag._firecrawl_result_fields(object()) == ("", "", "")


# ── the pipeline ──────────────────────────────────────────────────────────────

class TestResearchPipeline:
    def test_current_events_skip_vault_and_wikipedia(self, monkeypatch):
        vault = MagicMock(return_value="STALE CACHE")
        wiki = MagicMock(return_value={"title": "Matteo Renzi", "content": "italian politics"})
        fc = MagicMock(return_value="LIVE: Netanyahu addressed the 81st session")
        monkeypatch.setattr(rag, "_check_vault", vault)
        monkeypatch.setattr(rag, "_wikipedia_lookup", wiki)
        monkeypatch.setattr(rag, "_firecrawl_search", fc)
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research(RENZI_Q)

        vault.assert_not_called()
        wiki.assert_not_called()
        assert "LIVE: Netanyahu" in out
        assert "Renzi" not in out
        assert out.startswith("[Source: Firecrawl | live web]")

    def test_firecrawl_failure_falls_back_to_browse_web(self, monkeypatch):
        monkeypatch.setattr(rag, "_firecrawl_search", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_browse_web",
                            MagicMock(return_value=("BROWSER: bing results", "")))
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research(RENZI_Q)
        assert "BROWSER: bing results" in out
        assert out.startswith("[Source: browse_web | live web]")

    def test_both_live_paths_fail_reports_the_reason(self, monkeypatch):
        monkeypatch.setattr(rag, "_firecrawl_search", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_browse_web",
                            MagicMock(return_value=(None, "only 0.3 GB RAM free")))
        monkeypatch.setattr(rag, "_wikipedia_lookup", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research(RENZI_Q)
        assert "Could not find information" in out
        assert "only 0.3 GB RAM free" in out
        assert "do not guess" in out

    def test_encyclopedic_topic_uses_wikipedia_and_labels_real_title(self, monkeypatch):
        monkeypatch.setattr(rag, "_check_vault", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_wikipedia_lookup",
                            MagicMock(return_value={"title": "Quantum computing", "content": "qubits..."}))
        fc = MagicMock()
        monkeypatch.setattr(rag, "_firecrawl_search", fc)
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research("quantum computing")
        fc.assert_not_called()
        assert out.startswith("[Source: Wikipedia | Quantum computing]")
        assert "qubits..." in out

    def test_vault_cache_used_for_encyclopedic_topics(self, monkeypatch):
        monkeypatch.setattr(rag, "_check_vault", MagicMock(return_value="cached facts"))
        monkeypatch.setattr(rag, "_wikipedia_lookup", MagicMock())
        out = rag.research("quantum computing")
        assert out.startswith("[Source: Vault cache]")
        assert "cached facts" in out

    def test_current_events_not_saved_to_vault(self, monkeypatch):
        save = MagicMock()
        monkeypatch.setattr(rag, "_firecrawl_search", MagicMock(return_value="live stuff"))
        monkeypatch.setattr(rag, "_save_to_vault", save)
        rag.research(RENZI_Q)
        save.assert_not_called()

    def test_current_events_wikipedia_only_as_labelled_background(self, monkeypatch):
        monkeypatch.setattr(rag, "_firecrawl_search", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_browse_web", MagicMock(return_value=(None, "no browser")))
        monkeypatch.setattr(rag, "_wikipedia_lookup",
                            MagicMock(return_value={"title": "Benjamin Netanyahu", "content": "bio"}))
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research(RENZI_Q)
        assert "BACKGROUND only" in out
        assert "Benjamin Netanyahu" in out


# ── browse_web fallback ───────────────────────────────────────────────────────

class TestBrowseWebFallback:
    def test_success_returns_dossier(self):
        fake = MagicMock(return_value={"ok": True, "title": "Bing", "url": "https://bing.com/x",
                                       "text": "x" * 500})
        with patch("tools.dom.browse", fake):
            dossier, err = rag._browse_web(query="some topic")
        assert err == ""
        assert "Bing" in dossier and len(dossier) > 200

    def test_memory_guard_error_is_passed_through(self):
        fake = MagicMock(return_value={"ok": False, "error": "only 0.3 GB RAM free"})
        with patch("tools.dom.browse", fake):
            dossier, err = rag._browse_web(query="some topic")
        assert dossier is None
        assert "0.3 GB RAM free" in err

    def test_thin_text_is_rejected(self):
        fake = MagicMock(return_value={"ok": True, "title": "t", "url": "u", "text": "short"})
        with patch("tools.dom.browse", fake):
            dossier, err = rag._browse_web(query="some topic")
        assert dossier is None
        assert "too little text" in err

    def test_exception_never_propagates(self):
        with patch("tools.dom.browse", MagicMock(side_effect=RuntimeError("boom"))):
            dossier, err = rag._browse_web(query="some topic")
        assert dossier is None
        assert "RuntimeError" in err


# ── URL reading (the "use firecrawl to open that link" path) ──────────────────

LINK = ("https://www.thelondoneconomic.com/news/full-list-of-countries-to-walk-out-"
        "of-netanyahu-speech-at-united-nations-409984/")


class TestUrlExtraction:
    def test_extracts_https_url(self):
        assert rag._extract_url(f"check this list: {LINK}") == LINK

    def test_extracts_bare_www(self):
        assert rag._extract_url("see www.example.com/x") == "https://www.example.com/x"

    def test_strips_trailing_punctuation(self):
        assert rag._extract_url("look at https://example.com/a.") == "https://example.com/a"

    def test_no_url_returns_none(self):
        assert rag._extract_url("what is quantum computing") is None


class TestUrlReading:
    def test_research_with_a_url_scrapes_via_firecrawl(self, monkeypatch):
        scrape = MagicMock(return_value="- Full list of countries\nTurkey walked out...")
        monkeypatch.setattr(rag, "scrape_url", scrape)
        monkeypatch.setattr(rag, "_check_vault", MagicMock())
        monkeypatch.setattr(rag, "_firecrawl_search", MagicMock())

        out = rag.research(LINK)
        scrape.assert_called_once_with(LINK)
        assert out.startswith("[Source: Firecrawl | live web]")
        assert "Turkey walked out" in out

    def test_read_url_falls_back_to_the_browser(self, monkeypatch):
        monkeypatch.setattr(rag, "scrape_url", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_browse_web",
                            MagicMock(return_value=("PAGE: [url]\n- t (u)\n" + "x" * 300, "")))
        out = rag.read_url(LINK)
        assert out.startswith("[Source: browse_web | live web]")

    def test_read_url_reports_both_failures(self, monkeypatch):
        monkeypatch.setattr(rag, "scrape_url", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_browse_web",
                            MagicMock(return_value=(None, "only 0.4 GB RAM free")))
        out = rag.read_url(LINK)
        assert "Could not read" in out
        assert "only 0.4 GB RAM free" in out
        assert "do not guess" in out

    def test_scrape_url_without_a_key_returns_none(self, monkeypatch):
        monkeypatch.setattr(rag.config, "FIRECRAWL_API_KEY", "")
        assert rag.scrape_url(LINK) is None


class TestBoilerplateTrim:
    MD = (
        "- [Privacy policy](https://x/pp) - [T&Cs](https://x/tc) - [About Us](https://x/a)\n"
        "![logo](https://x/logo.png)\n"
        "\n"
        "# Full list of countries to walk out of Netanyahu speech\n"
        "## He said what was happening in Gaza is the 'opposite of genocide.'\n"
        "Turkey and Spain were among the delegations that left the hall.\n"
    )

    def test_starts_at_the_article_h1(self):
        out = rag._trim_boilerplate(self.MD)
        assert out.startswith("# Full list of countries")
        assert "Privacy policy" not in out

    def test_content_survives(self):
        out = rag._trim_boilerplate(self.MD)
        assert "Turkey and Spain" in out

    def test_no_heading_falls_back_to_full_text(self):
        md = "Just prose with no heading at all, long enough to matter.\nSecond line."
        assert rag._trim_boilerplate(md) == md

    def test_empty_input_is_safe(self):
        assert rag._trim_boilerplate("") == ""


# ── ID 22: Laya article pick + answerability gate ─────────────────────────────

class TestLayaPickArticle:
    def test_kernel_off_returns_none(self, monkeypatch):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert rag._laya_pick_article("q", ["A article", "B article"]) is None

    def test_picks_a_title(self, monkeypatch):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "B", "escalate": False})
        assert rag._laya_pick_article("q", ["first", "second"]) == "second"

    def test_none_key_returns_none(self, monkeypatch):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "Z", "escalate": False})
        assert rag._laya_pick_article("q", ["first", "second"]) is None

    def test_escalation_returns_none(self, monkeypatch):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "choose",
                            lambda *a, **k: {"choice": "A", "escalate": True})
        assert rag._laya_pick_article("q", ["first"]) is None

    def test_kernel_failure_returns_none(self, monkeypatch):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(system1, "choose", boom)
        assert rag._laya_pick_article("q", ["first"]) is None

    def test_empty_titles_returns_none(self):
        assert rag._laya_pick_article("q", []) is None


class TestAnswerability:
    """Scored, not a 3-way choice: the choice form called unrelated content "answers"."""

    def _with(self, monkeypatch, normalized, escalate=False):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "score_candidates",
                            lambda *a, **k: {"normalized": {"candidate": normalized},
                                             "escalate": escalate})

    def test_maps_normalized_scores_to_verdicts(self, monkeypatch):
        for norm, expected in [(0.0, "irrelevant"), (0.33, "irrelevant"),
                               (0.67, "background"), (1.0, "answers")]:
            self._with(monkeypatch, norm)
            assert rag._answerability("q", "content") == expected, norm

    def test_escalation_is_unknown(self, monkeypatch):
        self._with(monkeypatch, 1.0, escalate=True)
        assert rag._answerability("q", "content") == ""

    def test_missing_score_is_unknown(self, monkeypatch):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: True)
        monkeypatch.setattr(system1, "score_candidates",
                            lambda *a, **k: {"normalized": {}, "escalate": False})
        assert rag._answerability("q", "content") == ""

    def test_kernel_off_is_unknown(self, monkeypatch):
        import core.system1 as system1
        monkeypatch.setattr(system1, "kernel_enabled", lambda: False)
        assert rag._answerability("q", "content") == ""


class TestAnswerabilityRouting:
    def test_irrelevant_wikipedia_falls_through_to_live_web(self, monkeypatch):
        monkeypatch.setattr(rag, "_check_vault", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_wikipedia_lookup",
                            MagicMock(return_value={"title": "Matteo Renzi", "content": "italy"}))
        monkeypatch.setattr(rag, "_answerability", MagicMock(return_value="irrelevant"))
        fc = MagicMock(return_value="LIVE: the real answer")
        monkeypatch.setattr(rag, "_firecrawl_search", fc)
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research("who is the prime minister of italy")
        fc.assert_called_once()
        assert out.startswith("[Source: Firecrawl | live web]")
        assert "Renzi" not in out

    def test_background_wikipedia_is_labelled(self, monkeypatch):
        monkeypatch.setattr(rag, "_check_vault", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_wikipedia_lookup",
                            MagicMock(return_value={"title": "Quantum computing", "content": "qubits"}))
        monkeypatch.setattr(rag, "_answerability", MagicMock(return_value="background"))
        monkeypatch.setattr(rag, "_firecrawl_search", MagicMock())
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research("quantum computing")
        assert out.startswith("[Source: Wikipedia | Quantum computing] — background only")
        assert "qubits" in out

    def test_answers_verdict_returns_plain_wikipedia(self, monkeypatch):
        monkeypatch.setattr(rag, "_check_vault", MagicMock(return_value=None))
        monkeypatch.setattr(rag, "_wikipedia_lookup",
                            MagicMock(return_value={"title": "Quantum computing", "content": "qubits"}))
        monkeypatch.setattr(rag, "_answerability", MagicMock(return_value="answers"))
        monkeypatch.setattr(rag, "_save_to_vault", MagicMock())

        out = rag.research("quantum computing")
        assert out.startswith("[Source: Wikipedia | Quantum computing]\n")
