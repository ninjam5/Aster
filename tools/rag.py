import os
import re
from datetime import datetime

import wikipedia
from firecrawl import FirecrawlApp
import config

VAULT_DIR = os.path.join("Aster_Vault", "database")
os.makedirs(VAULT_DIR, exist_ok=True)

_MIN_WIKI_CHARS = 500  # below this Wikipedia returned a stub; fall through to Firecrawl


# ── internal helpers ──────────────────────────────────────────────────────────

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


def _wikipedia_lookup(topic: str) -> str | None:
    """Returns Wikipedia article content for topic, or None if not found/too short/irrelevant.

    Two-step strategy:
    1. Direct page lookup with auto_suggest — handles exact titles and common phrasings fast.
    2. Search fallback — scores all candidates by word-overlap and picks the best match,
       NOT the first result. Falls through to Firecrawl if no candidate is relevant enough.
    """
    print(f"[Aster Internal: Wikipedia lookup for '{topic}']")
    wikipedia.set_user_agent("Aster/1.0 (local autonomous agent)")

    # Step 1: direct lookup — try exact title first, then let Wikipedia suggest.
    # auto_suggest=True can mangle proper nouns (e.g. "Elden Ring" → "elder ring"),
    # so we try auto_suggest=False first, then True as a fallback for misspellings.
    for auto_suggest in (False, True):
        try:
            page = wikipedia.page(topic, auto_suggest=auto_suggest)
            if len(page.content) >= _MIN_WIKI_CHARS:
                print(f"[Aster Internal: Wikipedia direct hit -> '{page.title}']")
                return page.content
        except wikipedia.DisambiguationError as e:
            try:
                page = wikipedia.page(e.options[0], auto_suggest=False)
                if len(page.content) >= _MIN_WIKI_CHARS:
                    return page.content
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

        best_title, best_score = None, 0.0
        for title in candidates:
            title_words = set(re.sub(r"[^a-z0-9]", " ", title.lower()).split())
            union = query_words | title_words
            if not union:
                continue
            score = len(query_words & title_words) / len(union)
            if score > best_score:
                best_score, best_title = score, title

        if best_title and best_score >= 0.15:
            try:
                page = wikipedia.page(best_title, auto_suggest=False)
                if len(page.content) >= _MIN_WIKI_CHARS:
                    print(f"[Aster Internal: Wikipedia search hit '{best_title}' (score={best_score:.2f})]")
                    return page.content
            except wikipedia.DisambiguationError as e:
                try:
                    page = wikipedia.page(e.options[0], auto_suggest=False)
                    if len(page.content) >= _MIN_WIKI_CHARS:
                        return page.content
                except Exception:
                    pass
            except Exception:
                pass
        else:
            print(f"[Aster Internal: Wikipedia — no relevant result "
                  f"(best='{best_title}', score={best_score:.2f}), falling through to Firecrawl]")
    except Exception as e:
        print(f"[Aster Internal: Wikipedia search error: {e}]")

    return None


def _firecrawl_search(topic: str) -> str | None:
    """Firecrawl 3-query scrape. Returns dossier string or None on failure."""
    print(f"[Aster Internal: Falling back to Firecrawl for '{topic}']")
    queries = [
        topic,
        f"what is {topic} and how does it work",
        f"{topic} latest news updates {datetime.now().year}",
    ]
    dossier = f"--- DEEP RESEARCH DOSSIER: {topic} ---\n\n"
    try:
        app = FirecrawlApp(api_key=config.FIRECRAWL_API_KEY)
        for q in queries:
            dossier += f"SEARCH QUERY: [{q}]\n"
            result = app.search(q, limit=3, scrape_options={"formats": ["markdown"]})
            for item in (result.data or [])[:3]:
                title = item.get("title", "")
                url = item.get("url", "")
                body = item.get("markdown") or item.get("description", "")
                body = body[:5000]  # 9 results × 5k chars ≈ 11k tokens, well within 128k context
                dossier += f"- {title} ({url})\n{body}\n\n"
        return dossier.strip()
    except Exception as e:
        print(f"[Aster Internal: Firecrawl error: {e}]")
        return None


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
    """Single knowledge-lookup tool exposed to Gemma.

    Pipeline: vault cache → Wikipedia → Firecrawl fallback.
    Auto-saves every successful result so repeat queries are instant.
    Splits comparison queries ("X vs Y") into two entity lookups automatically.
    """
    print(f"\n[Aster Internal: research('{topic}')]")

    entities = _sanitize_topic(topic)

    # For comparison queries resolved to 2 entities, look up each and combine.
    if len(entities) == 2 and entities[0].lower() != entities[1].lower():
        results = []
        for entity in entities:
            result = research(entity)  # recursive — each entity goes through full pipeline
            results.append(result)
        return "\n\n---\n\n".join(results)

    entity = entities[0] if entities else topic

    # 1. Vault cache
    cached = _check_vault(entity)
    if cached:
        print(f"[Aster Internal: Vault hit for '{entity}']")
        return f"[Source: Vault cache]\n\n{cached}"

    # 2. Wikipedia (fast, free, encyclopedic)
    wiki_content = _wikipedia_lookup(entity)
    if wiki_content:
        header = f"[Source: Wikipedia | {entity}]\n\n"
        _save_to_vault(entity, header + wiki_content)
        return header + wiki_content

    # 3. Firecrawl fallback (current events, niche web content)
    firecrawl_content = _firecrawl_search(entity)
    if firecrawl_content:
        header = f"[Source: Firecrawl | {entity}]\n\n"
        _save_to_vault(entity, header + firecrawl_content)
        return header + firecrawl_content

    return f"[System: Could not find information on '{entity}' via Wikipedia or web search. Try rephrasing or asking a more specific question.]"
