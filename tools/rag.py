import os
import re
from datetime import datetime

import wikipedia
from firecrawl import FirecrawlApp
import config

VAULT_DIR = os.path.join("Aster_Vault", "database")
os.makedirs(VAULT_DIR, exist_ok=True)

_MIN_WIKI_CHARS = 500  # below this Wikipedia returned a stub; fall through to Firecrawl

# ── relevance guards (2026-09-28) ─────────────────────────────────────────────
# Incident: a "did Netanyahu attend the UNGA Sept 27 2026?" question was answered
# from the Wikipedia article on MATTEO RENZI (84k chars), cached under the
# question's own filename, and served for 90 days. Two causes: the search
# fallback accepted anything scoring >= 0.15 Jaccard, and the auto_suggest path
# accepted a page with NO check at all. A page is now only accepted when its
# TITLE shares a distinctive token with the query.
_WIKI_MIN_SCORE = 0.5

# Question words and generic ROLE words (so "Prime Minister of Italy" still
# matches the query "who is the prime minister of italy" via "italy").
_STOPWORDS = {
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "did", "does", "do", "is", "are", "was", "were", "be", "been", "being",
    "the", "this", "that", "these", "those", "and", "or", "but", "for", "with",
    "from", "into", "about", "attend", "attended", "attendance", "meeting",
    "latest", "news", "current", "today", "yesterday", "update", "updates",
    "prime", "minister", "president", "general", "assembly", "session",
}


def _distinctive_words(text: str) -> set[str]:
    """Lowercase words >= 4 chars that carry identity (no question/role stopwords)."""
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower())
            if len(w) >= 4 and w not in _STOPWORDS}


def _tokens_match(a: str, b: str) -> bool:
    """Fuzzy identity match: exact, prefix ("israel"/"israeli"), or a close typo
    ("netanyhau"/"netanyahu"). Guards against both false hits (Renzi) and the
    over-strict rejection of a legitimately relevant page."""
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    if len(a) >= 5 and len(b) >= 5:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio() >= 0.85
    return False


def _wiki_title_relevant(title: str, topic: str) -> bool:
    """True when the article title shares a distinctive token with the query."""
    title_words = _distinctive_words(title)
    topic_words = _distinctive_words(topic)
    return any(_tokens_match(t, q) for t in title_words for q in topic_words)


_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')]+", re.IGNORECASE)

# Scraped markdown opens with menus/logo links; the article body starts at the H1.
_LINK_LINE_RE = re.compile(r"^(?:[-*]\s*)?(?:\[[^\]]*\]\([^)]*\)\s*[-*]?\s*)+$")
_H1_RE = re.compile(r"(?m)^#\s+\S")


def _trim_boilerplate(md: str) -> str:
    """Drop the leading nav/link/logo block from scraped markdown.

    Without this the article's real content (e.g. a list of countries) sits past
    the character cap and the compression summary spends its budget on menu links.
    """
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        l = lines[i].strip()
        if not l or _LINK_LINE_RE.match(l):
            i += 1
            continue
        break
    body = "\n".join(lines[i:]).strip() or md
    m = _H1_RE.search(body[:4000])
    if m:
        body = body[m.start():]
    return body

_CURRENT_EVENT_RE = re.compile(
    r"\b(?:latest|breaking|news|today|yesterday|tonight|this\s+(?:week|month|year)|"
    r"current(?:ly)?|right\s+now|just\s+(?:now|announced|happened)|upcoming)\b",
    re.IGNORECASE,
)


def _is_current_events(topic: str) -> bool:
    """True for time-sensitive topics, where Wikipedia is the WRONG source.

    Fires on recency words and on any year >= the current year — a question about
    September 2026 must never be answered from an encyclopedic snapshot.
    """
    if _CURRENT_EVENT_RE.search(topic or ""):
        return True
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", topic or "")]
    return any(y >= datetime.now().year for y in years)


# ── internal helpers ──────────────────────────────────────────────────────────

def _laya_pick_article(topic: str, titles: list) -> str | None:
    """Laya: which candidate Wikipedia article is actually about `topic`? (ID 22)

    Returns a title from `titles`, or None meaning "none / not sure / kernel off" —
    the caller then falls back to the lexical gate. `Z` is the none-key (candidate
    keys only go A..R, so it cannot collide).
    """
    titles = [str(t) for t in (titles or [])][:18]
    if not titles:
        return None
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return None
        criteria = {chr(ord("A") + i): t for i, t in enumerate(titles)}
        criteria["Z"] = "none of these — no article is about this topic"
        verdict = system1.choose(
            f"Which of these Wikipedia articles is about: {topic}?",
            criteria, key="article", state={"topic": topic},
        )
    except Exception:
        return None
    if verdict.get("escalate"):
        return None
    choice = verdict.get("choice")
    if not choice or choice == "Z":
        return None
    return criteria.get(choice)


_ANSWER_LEVELS = ["irrelevant", "background only", "partly answers", "answers directly"]


def _answerability(question: str, content: str) -> str:
    """Laya: does this fetched content answer the question? (ID 22)

    Returns "answers" | "background" | "irrelevant" | "" (unknown/kernel off/escalated).
    The empty string means "make no judgement" — the caller behaves as before.

    Scored (not a 3-way choice): a live probe showed the choice form calling an
    unrelated Matteo-Renzi paragraph "answers", while the ordinal score ranked the
    same content low. Levels run worst -> best; normalized thresholds map the score.
    """
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return ""
        verdict = system1.score_candidates(
            _ANSWER_LEVELS,
            "How well does this content answer the user's question?",
            {"candidate": (content or "")[:800]},
            state={"question": question},
        )
    except Exception:
        return ""
    if verdict.get("escalate"):
        return ""
    normalized = (verdict.get("normalized") or {}).get("candidate")
    if normalized is None:
        return ""
    if normalized < 0.4:
        return "irrelevant"
    if normalized < 0.7:
        return "background"
    return "answers"


def _check_vault(topic: str) -> str | None:
    """Returns vault content if fresh (<90 days), else None."""
    topic_clean = re.sub(r'[^a-zA-Z0-9]', '', topic).lower()
    try:
        for filename in os.listdir(VAULT_DIR):
            if not filename.endswith(".md"):
                continue
            name_part = filename.rsplit('_', 3)[0]
            name_clean = re.sub(r'[^a-zA-Z0-9]', '', name_part).lower()
            if topic_clean not in name_clean and name_clean not in topic_clean:
                continue
            date_parts = filename.replace(".md", "").split("_")[-3:]
            try:
                file_date = datetime.strptime("_".join(date_parts), "%m_%d_%y")
            except ValueError:
                continue
            if (datetime.now() - file_date).days <= 90:
                with open(os.path.join(VAULT_DIR, filename), "r", encoding="utf-8") as f:
                    return f.read()
    except Exception as e:
        print(f"[Aster Internal: Vault check error: {e}]")
    return None


def _save_to_vault(topic: str, content: str) -> None:
    """Saves content to the vault, replacing any older file for the same topic."""
    try:
        topic_clean = re.sub(r'[^a-zA-Z0-9_]', '', topic.replace(' ', '_'))
        date_str = datetime.now().strftime("%m_%d_%y")
        new_path = os.path.join(VAULT_DIR, f"{topic_clean}_{date_str}.md")
        topic_base = re.sub(r'[^a-zA-Z0-9]', '', topic).lower()
        for filename in os.listdir(VAULT_DIR):
            name_part = filename.rsplit('_', 3)[0]
            if re.sub(r'[^a-zA-Z0-9]', '', name_part).lower() == topic_base:
                os.remove(os.path.join(VAULT_DIR, filename))
        with open(new_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[Aster Internal: Saved to vault -> {os.path.basename(new_path)}]")
    except Exception as e:
        print(f"[Aster Internal: Vault save error: {e}]")


def _wikipedia_lookup(topic: str) -> dict | None:
    """Returns {"title", "content"} for a RELEVANT Wikipedia article, else None.

    Two-step strategy:
    1. Direct page lookup with auto_suggest — handles exact titles and common phrasings fast.
    2. Search fallback — scores all candidates by word-overlap and picks the best match,
       NOT the first result. Falls through to live web if no candidate is relevant enough.

    Both paths require the article TITLE to share a distinctive token with the query
    (`_wiki_title_relevant`) — relevance, not just length.
    """
    print(f"[Aster Internal: Wikipedia lookup for '{topic}']")
    wikipedia.set_user_agent("Aster/1.0 (local autonomous agent)")

    # Step 1: direct lookup — try exact title first, then let Wikipedia suggest.
    # auto_suggest=True can mangle proper nouns (e.g. "Elden Ring" → "elder ring"),
    # so we try auto_suggest=False first, then True as a fallback for misspellings.
    for auto_suggest in (False, True):
        try:
            page = wikipedia.page(topic, auto_suggest=auto_suggest)
            if len(page.content) >= _MIN_WIKI_CHARS and _wiki_title_relevant(page.title, topic):
                print(f"[Aster Internal: Wikipedia direct hit -> '{page.title}']")
                return {"title": page.title, "content": page.content}
            if len(page.content) >= _MIN_WIKI_CHARS:
                print(f"[Aster Internal: Wikipedia rejected unrelated hit "
                      f"'{page.title}' for '{topic}']")
        except wikipedia.DisambiguationError as e:
            try:
                page = wikipedia.page(e.options[0], auto_suggest=False)
                if len(page.content) >= _MIN_WIKI_CHARS and _wiki_title_relevant(page.title, topic):
                    return {"title": page.title, "content": page.content}
            except Exception:
                pass
        except (wikipedia.PageError, wikipedia.WikipediaException):
            pass
        except Exception as e:
            print(f"[Aster Internal: Wikipedia direct lookup error (auto_suggest={auto_suggest}): {e}]")

    # Step 2: search fallback — pick the BEST-scoring candidate, not the first.
    # Jaccard word-overlap score; anything below threshold is likely an unrelated article.
    try:
        query_words = set(re.sub(r"[^a-z0-9]", " ", topic.lower()).split())
        candidates = wikipedia.search(topic, results=5)

        # Laya semantic pick first (Phase 1 / ID 22): choose the article that is
        # actually about the topic, or "none". Any doubt falls through to the
        # lexical gate below, which is what caught the Matteo Renzi incident.
        picked = _laya_pick_article(topic, candidates)
        if picked:
            try:
                page = wikipedia.page(picked, auto_suggest=False)
                if len(page.content) >= _MIN_WIKI_CHARS:
                    print(f"[Aster Internal: Wikipedia Laya pick -> '{page.title}']")
                    return {"title": page.title, "content": page.content}
            except Exception:
                pass

        best_title, best_score = None, 0.0
        for title in candidates:
            title_words = set(re.sub(r"[^a-z0-9]", " ", title.lower()).split())
            union = query_words | title_words
            if not union:
                continue
            score = len(query_words & title_words) / len(union)
            if score > best_score:
                best_score, best_title = score, title

        if best_title and best_score >= _WIKI_MIN_SCORE and _wiki_title_relevant(best_title, topic):
            try:
                page = wikipedia.page(best_title, auto_suggest=False)
                if len(page.content) >= _MIN_WIKI_CHARS:
                    print(f"[Aster Internal: Wikipedia search hit '{best_title}' (score={best_score:.2f})]")
                    return {"title": page.title, "content": page.content}
            except wikipedia.DisambiguationError as e:
                try:
                    page = wikipedia.page(e.options[0], auto_suggest=False)
                    if len(page.content) >= _MIN_WIKI_CHARS:
                        return {"title": page.title, "content": page.content}
                except Exception:
                    pass
            except Exception:
                pass
        else:
            print(f"[Aster Internal: Wikipedia — no relevant result "
                  f"(best='{best_title}', score={best_score:.2f}), falling through to live web]")
    except Exception as e:
        print(f"[Aster Internal: Wikipedia search error: {e}]")

    return None


def _firecrawl_result_fields(item) -> tuple[str, str, str]:
    """(title, url, body) from a Firecrawl v2 search result.

    The installed SDK (firecrawl 4.x) returns `SearchData` with `.web`/`.news`
    whose entries are `Document` (markdown + metadata.title/url) or
    `SearchResultWeb` (title/url/description). The old code read `result.data`
    and `item["markdown"]` (the v1 shape) — that raised AttributeError, was
    swallowed as "Firecrawl error", and silently disabled live search.
    """
    if hasattr(item, "model_dump"):
        d = item.model_dump()
    elif isinstance(item, dict):
        d = item
    else:
        d = {}
    meta = d.get("metadata") or {}
    title = d.get("title") or meta.get("title") or ""
    url = d.get("url") or meta.get("url") or meta.get("source_url") or ""
    body = (d.get("markdown") or d.get("description")
            or d.get("snippet") or d.get("summary") or "")
    return str(title), str(url), str(body)


def _firecrawl_search(topic: str) -> str | None:
    """Firecrawl live-web search (3 queries). Returns dossier string or None on failure."""
    print(f"[Aster Internal: Live web search (Firecrawl) for '{topic}']")
    queries = [
        topic,
        f"{topic} latest news {datetime.now().year}",
        f"{topic} what happened",
    ]
    dossier = f"--- LIVE WEB DOSSIER: {topic} ---\n\n"
    got_any = False
    try:
        app = FirecrawlApp(api_key=config.FIRECRAWL_API_KEY)
        for q in queries:
            result = app.search(q, limit=3, sources=["web"],
                                scrape_options={"formats": ["markdown"]})
            items = list(getattr(result, "web", None) or []) + \
                    list(getattr(result, "news", None) or [])
            block = f"SEARCH QUERY: [{q}]\n"
            for item in items[:3]:
                title, url, body = _firecrawl_result_fields(item)
                if not body:
                    continue
                body = body[:5000]  # 9 results x 5k chars ~ 11k tokens, inside 60k
                block += f"- {title} ({url})\n{body}\n\n"
                got_any = True
            if got_any:
                dossier += block
        if not got_any:
            print("[Aster Internal: Firecrawl returned no usable results]")
            return None
        return dossier.strip()
    except Exception as e:
        print(f"[Aster Internal: Firecrawl error: {e.__class__.__name__}: {e}]")
        return None


def _browse_web(query: str = "", url: str = "") -> tuple[str | None, str]:
    """Live read through Aster's own browser (DOM motor + Chrome).

    Returns (dossier_or_None, error_text). Requires free RAM — the browser guard
    in tools/dom refuses the launch when there is not enough, and that reason is
    passed back so the model can tell the owner what to close.
    """
    what = url or query
    print(f"[Aster Internal: Live read via browse_web for '{what}']")
    try:
        from tools.dom import browse
    except Exception as e:
        return None, f"browser module unavailable ({e.__class__.__name__})"
    try:
        res = browse(url=url, query=query, max_chars=6000)
    except Exception as e:
        return None, f"{e.__class__.__name__}: {e}"
    if not isinstance(res, dict) or not res.get("ok"):
        err = res.get("error") if isinstance(res, dict) else "no result"
        print(f"[Aster Internal: browse_web failed: {err}]")
        return None, str(err or "unknown browser error")
    text = (res.get("text") or "").strip()
    if len(text) < 200:
        return None, "browser returned too little text"
    title = res.get("title", "")
    got_url = res.get("url", "")
    label = "SEARCH QUERY" if query and not url else "PAGE"
    return f"{label}: [{what}]\n- {title} ({got_url})\n{text[:5000]}", ""


def _extract_url(text: str) -> str | None:
    """First http(s)/www URL in the text, or None. Trailing punctuation stripped."""
    m = _URL_RE.search(text or "")
    if not m:
        return None
    u = m.group(0).rstrip(".,;:!?)\"'")
    if u.lower().startswith("www."):
        u = "https://" + u
    return u


def scrape_url(url: str) -> str | None:
    """Read ONE page through the Firecrawl API only — no browser, no RAM cost.

    The path for "open this link": Firecrawl renders the page server-side, so it
    works when the browser guard is refusing a launch for low RAM.
    """
    print(f"[Aster Internal: Live page read (Firecrawl) for '{url}']")
    if not config.FIRECRAWL_API_KEY:
        return None
    try:
        app = FirecrawlApp(api_key=config.FIRECRAWL_API_KEY)
        doc = app.scrape(url, formats=["markdown"])
    except Exception as e:
        print(f"[Aster Internal: Firecrawl scrape error: {e.__class__.__name__}: {e}]")
        return None
    title, got_url, body = _firecrawl_result_fields(doc)
    body = _trim_boilerplate(body)
    if not body or len(body.strip()) < 200:
        print("[Aster Internal: Firecrawl scrape returned too little content]")
        return None
    # 16k chars ~ 4k tokens: enough for a full article (the country-list page's
    # content runs to ~12k after the nav block is trimmed).
    return f"- {title} ({got_url or url})\n{body[:16000]}"


def read_url(url: str) -> str:
    """Read one specific URL: Firecrawl API first (no RAM), browser second."""
    scraped = scrape_url(url)
    if scraped:
        return f"[Source: Firecrawl | live web]\n\n{scraped}"
    dossier, err = _browse_web(url=url)
    if dossier:
        return f"[Source: browse_web | live web]\n\n{dossier}"
    detail = f" ({err})" if err else ""
    return (f"[System: Could not read {url}{detail}. Tell {config.OWNER_NAME} plainly that "
            "the page could not be read — do not guess its contents.]")


# ── public tool ───────────────────────────────────────────────────────────────

_COMPARISON_PREFIX_RE = re.compile(
    r"^(?:difference(?:s)?\s+between\s+|compare\s+|comparison\s+(?:of|between)\s+|"
    r"what(?:'s|\s+is)\s+(?:the\s+)?(?:difference|comparison)\s+(?:between\s+)?)",
    re.IGNORECASE,
)
_AND_SPLIT_RE = re.compile(r"\s+(?:and|vs\.?|versus)\s+", re.IGNORECASE)


def _sanitize_topic(topic: str) -> list[str]:
    """Normalize a research topic into one or more clean entity strings.

    Strips comparison prefixes ("difference between X and Y") and splits
    "X and Y" / "X vs Y" into separate lookups so Wikipedia can find both.
    Returns a list with 1 or 2 entity strings.
    """
    cleaned = _COMPARISON_PREFIX_RE.sub("", topic.strip())
    parts = _AND_SPLIT_RE.split(cleaned)
    return [p.strip() for p in parts if p.strip()][:2]  # cap at 2 entities


def research(topic: str) -> str:
    """Single knowledge-lookup tool exposed to the model.

    Pipeline:
      current-events topic  → live web FIRST (Firecrawl → browse_web), Wikipedia never
      anything else         → vault cache → Wikipedia → live web (Firecrawl → browse_web)

    Auto-saves successful results so repeat queries are instant — except
    current-events topics, which must never be served from a 90-day cache.
    Splits comparison queries ("X vs Y") into two entity lookups automatically.
    """
    print(f"\n[Aster Internal: research('{topic}')]")

    # A URL is a direct read, not a topic lookup: Firecrawl API (no RAM), then browser.
    url = _extract_url(topic)
    if url:
        return read_url(url)

    entities = _sanitize_topic(topic)

    # For comparison queries resolved to 2 entities, look up each and combine.
    if len(entities) == 2 and entities[0].lower() != entities[1].lower():
        results = []
        for entity in entities:
            result = research(entity)  # recursive — each entity goes through full pipeline
            results.append(result)
        return "\n\n---\n\n".join(results)

    entity = entities[0] if entities else topic
    time_sensitive = _is_current_events(entity)

    # 1. Vault cache (skipped for current events — a cached news answer is stale by design)
    if not time_sensitive:
        cached = _check_vault(entity)
        if cached:
            print(f"[Aster Internal: Vault hit for '{entity}']")
            return f"[Source: Vault cache]\n\n{cached}"

    # 2. Wikipedia (fast, free, encyclopedic) — never for current events
    if not time_sensitive:
        wiki = _wikipedia_lookup(entity)
        if wiki:
            verdict = _answerability(entity, wiki["content"])
            if verdict == "irrelevant":
                print("[Aster Internal: Wikipedia result judged IRRELEVANT by Laya — going live]")
            else:
                header = f"[Source: Wikipedia | {wiki['title']}]"
                if verdict == "background":
                    header += " — background only; it does not answer the question directly"
                header += "\n\n"
                _save_to_vault(entity, header + wiki["content"])
                return header + wiki["content"]

    # 3. Live web — Firecrawl, then Aster's own browser (Laya/DOM motor + Chrome)
    live = _firecrawl_search(entity)
    source = "Firecrawl"
    browse_err = ""
    if not live:
        live, browse_err = _browse_web(query=entity)
        source = "browse_web"
    if live:
        header = f"[Source: {source} | live web]\n\n"
        if not time_sensitive:
            _save_to_vault(entity, header + live)
        return header + live

    # 4. Current events with no live path: encyclopedic background is better than nothing,
    #    clearly labelled as background (not the live answer).
    if time_sensitive:
        wiki = _wikipedia_lookup(entity)
        if wiki:
            header = (f"[Source: Wikipedia | {wiki['title']}] — encyclopedic BACKGROUND only; "
                      "this is NOT the current answer.\n\n")
            return header + wiki["content"]

    detail = f" Live web failed: {browse_err}." if browse_err else ""
    return (f"[System: Could not find information on '{entity}' via Wikipedia or live web."
            f"{detail} Tell {config.OWNER_NAME} plainly that the lookup failed — do not guess.]")
