import difflib
import math
import os
import re
import threading
import time
from datetime import datetime
import requests
import config as _config
from config import MEMORY_AVAILABLE, MD_FILE, memory_collection


# ============================================================================
# LAYA WRITE GATE  (Phase 2 of laya-integration.md — IDs 9 / 10 / 11)
# ============================================================================
# The dedup gate used to live only in the brain's `memorize_fact` tool dispatch, so
# gmail_tool, face_server, vision and the Discord sync all bypassed it — that is how
# the duplicate pairs (memory.md:9/11, :10/12) and ~78 "Aster sent an email reply"
# lines got in. It now runs HERE, at the single choke point every writer goes through.
_GATE_MARGIN = 0.5  # stricter than the DOM motor's 0.25: a wrong "skip" loses a fact
_NONE_KEY = "Z"
_NONE_OPTION = "none of these — this is new information"


def _similar_existing_facts(fact: str, k: int = 6) -> list:
    """The k most textually similar existing vault facts (timestamp prefix stripped)."""
    if not os.path.exists(MD_FILE):
        return []
    try:
        with open(MD_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    fact_clean = fact.strip().lower()
    scored = []
    for line in lines:
        m = re.match(r"- \*\*\[.+?\]\*\*\s*(.*)", line)
        text = (m.group(1) if m else line).strip()
        if text:
            scored.append((difflib.SequenceMatcher(None, fact_clean, text.lower()).ratio(), text))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:k]]


def _pick_saved_fact(system1, question: str, fact: str, candidates: list, key: str):
    """Laya candidate-pick over saved facts. Returns (matched_fact_or_None, verdict).

    Laya's STRONG shape: the candidates are the `criteria` and the new fact is the
    state (same shape as `pick_element` / the Wikipedia article pick). Batching
    several of these into one pass DEGRADED the margins — a verbatim duplicate scored
    0.14 batched vs 0.72 alone — so each question gets its own call.
    """
    criteria = {chr(65 + i): c[:200] for i, c in enumerate(candidates[:8])}
    criteria[_NONE_KEY] = _NONE_OPTION
    verdict = system1.choose(question, criteria, key=key,
                             state={"new_fact": fact}, min_margin=_GATE_MARGIN)
    if verdict.get("escalate"):
        return None, verdict
    choice = verdict.get("choice")
    if not choice or choice == _NONE_KEY:
        return None, verdict
    return criteria.get(choice), verdict


def _gate_fact(fact: str) -> dict:
    """Laya write gate: is this junk, a duplicate, or in conflict with a saved fact?

    CONSERVATIVE — any escalation, missing answer, or kernel failure means WRITE: a
    fact is never silently dropped on a weak signal. Shapes and thresholds below were
    picked from live probes (see laya-integration.md Phase 2 status).
    Returns {"skip", "reason", "category", "conflict", "supersedes"}.
    """
    decision = {"skip": False, "reason": "", "category": "", "conflict": "", "supersedes": None}
    try:
        import core.system1 as system1
        if not system1.kernel_enabled():
            return decision

        # ID 10 — junk filter (neutral-key binary). Verified live: the email action log
        # answers False with margin 0.55; real facts escalate, so they are written.
        junk = system1.check_state(
            "Is this a durable personal fact about the owner, or an action log / system "
            "event / status report?",
            yes_description="a durable personal fact about the owner",
            no_description="an action log, system event, or status report — not a personal fact",
            state_text=f"item: {fact[:400]}",
            min_margin=_GATE_MARGIN,
        )
        if not junk.get("escalate") and junk.get("answer") is False:
            decision.update(skip=True, category="junk",
                            reason="classified as an action log / system event, not a personal fact")
            return decision

        candidates = _similar_existing_facts(fact, 6)
        if not candidates:
            return decision

        # ID 11 — duplicate / paraphrase. Verified live: a verbatim duplicate picks the
        # matching saved fact with margin 0.72; a new detail picks "none".
        matched, _ = _pick_saved_fact(
            system1,
            "Which saved fact already contains this information? (Same meaning counts, "
            "even if the wording differs.)",
            fact, candidates, key="duplicate")
        if matched:
            decision.update(skip=True, category="fact",
                            reason="already saved — the same information is in the vault")
            return decision

        # ID 9 — contradiction / supersede detection is NOT enabled. Measured live
        # 2026-09-28: the candidate-pick shape ("which saved fact does this REPLACE /
        # CONTRADICT?") returned margins 0.00-0.07, and a binary pair check returned
        # 0.16-0.28 with a wrong "contradicts" on unrelated text — both inert at the
        # 0.5 write threshold. Shipping it would add two Laya calls per write for
        # nothing, so the fields below stay empty and the plumbing in `memorize_fact`
        # (the SUPERSEDE / CONTRADICTS notes) is kept as the seam for a future,
        # calibrated detector. Do not re-add without a fresh measurement.
        return decision
    except Exception as e:
        decision["reason"] = f"gate error ({e.__class__.__name__}: {e})"
        return decision


# ============================================================================
# HYBRID MEMORY TOOLS
# ============================================================================
def memorize_fact(fact, source: str = "agent", skip_gate: bool = False):
    """Dual-writes a fact to both the Markdown vault and ChromaDB.

    Runs the Laya write gate first (Phase 2) unless `skip_gate` is set. The gate can
    SKIP a write (confident duplicate or junk) and reports a detected conflict — it
    never deletes or rewrites existing facts.
    """
    if not MEMORY_AVAILABLE: return "Error: Memory system offline."
    fact = str(fact or "").strip()
    if not fact:
        return "Error: empty fact."

    decision = {"skip": False, "reason": "", "category": "", "conflict": "", "supersedes": None}
    if not skip_gate:
        decision = _gate_fact(fact)
        if decision.get("skip"):
            print(f"[Aster Internal: Memory write skipped by Laya gate "
                  f"({decision['reason']}): '{fact[:80]}']")
            try:
                import core.instrumentation as _inst
                _inst.record_event("memory_write_skip", source=source,
                                   reason=decision.get("reason", ""), fact=fact[:200])
            except Exception:
                pass
            return (f"[System Note: NOT saved — {decision['reason']}. The information is "
                    f"already covered, or it is not a durable personal fact.]")

    try:
        # 1. Write to Human-Readable Markdown
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(MD_FILE, "a", encoding="utf-8") as f:
            f.write(f"- **[{timestamp}]** {fact}\n")

        # 2. Write to Semantic Vector Database. The id used to be second-granular
        #    (`mem_{int(time.time())}`), so facts written in the same second collided
        #    and were silently dropped by ChromaDB.
        doc_id = f"mem_{int(time.time() * 1000)}_{abs(hash(fact)) % 100000}"
        memory_collection.add(documents=[fact], ids=[doc_id])

        result = f"Successfully committed to Vault and Neural DB: '{fact}'"
        if decision.get("conflict") == "supersedes":
            result += (f"\n[NOTE: this appears to SUPERSEDE an earlier fact: "
                       f"'{str(decision.get('supersedes'))[:120]}'. Tell {_config.OWNER_NAME} "
                       f"and offer to forget the old one.]")
        elif decision.get("conflict") == "contradicts":
            result += (f"\n[NOTE: this CONTRADICTS an earlier fact: "
                       f"'{str(decision.get('supersedes'))[:120]}'. Surface it to "
                       f"{_config.OWNER_NAME}; do not silently treat both as true.]")
        return result
    except Exception as e:
        return f"Failed to memorize: {e}"


def forget_fact(text: str) -> str:
    """Remove a fact from both the Markdown vault and ChromaDB (matched by text).

    Uses text-based matching because the UI's `mem-{i}` ids are reverse-line
    indices and don't correspond to ChromaDB's `mem_{timestamp}` doc ids.
    """
    if not MEMORY_AVAILABLE:
        return "Error: Memory system offline."
    text = text.strip()
    if not text:
        return "Error: empty text."

    removed_md = False
    try:
        if os.path.exists(MD_FILE):
            with open(MD_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            kept = []
            for line in lines:
                # Format: "- **[timestamp]** fact text\n"
                # Strip the leading "- **[...]** " prefix to get the fact text.
                import re as _re
                m = _re.match(r"- \*\*\[.+?\]\*\*\s*(.*)", line)
                fact_text = m.group(1).strip() if m else line.strip()
                if not removed_md and fact_text == text:
                    removed_md = True
                    continue
                kept.append(line)
            if removed_md:
                with open(MD_FILE, "w", encoding="utf-8") as f:
                    f.writelines(kept)
    except Exception as e:
        return f"Failed to remove from Markdown vault: {e}"

    # Remove from ChromaDB — match by document content substring.
    removed_chroma = 0
    try:
        result = memory_collection.get(
            where_document={"$contains": text}
        )
        ids = result.get("ids", [])
        if ids:
            memory_collection.delete(ids=ids)
            removed_chroma = len(ids)
    except Exception as e:
        return f"Removed from Markdown (removed_md={removed_md}) but ChromaDB failed: {e}"

    if removed_md or removed_chroma:
        return f"Forgotten: '{text}' (md={removed_md}, chroma={removed_chroma})"
    return f"Fact not found: '{text}'"


# ============================================================================
# RRF HYBRID RECALL — BM25 (keyword, over memory.md) fused with ChromaDB dense
# retrieval via Reciprocal Rank Fusion. Catches keyword-exact queries (serial
# numbers, proper names) that dense embeddings alone can miss. Pure-Python
# BM25 rather than a pip dependency — no requirements.txt exists in this repo
# (packages are installed ad-hoc/globally per CLAUDE.md), so a hard import
# here would break installs that skip an extra pip step; the corpus is only
# hundreds of memory.md facts, so performance is a non-issue either way.
# ============================================================================
_MD_FACT_PREFIX_RE = re.compile(r"- \*\*\[.+?\]\*\*\s*(.*)")
_BM25_TOKEN_RE = re.compile(r"[a-z0-9]+")

_md_facts_cache: dict = {"mtime": None, "facts": []}


def _tokenize(text: str) -> list[str]:
    return _BM25_TOKEN_RE.findall((text or "").lower())


def _load_md_facts() -> list[str]:
    """Parse memory.md into a flat list of fact strings (timestamp prefix
    stripped). Cached by file mtime — cheap to call on every recall_memory()."""
    try:
        mtime = os.path.getmtime(MD_FILE)
    except OSError:
        return []

    if _md_facts_cache["mtime"] == mtime:
        return _md_facts_cache["facts"]

    facts: list[str] = []
    try:
        with open(MD_FILE, "r", encoding="utf-8") as f:
            for line in f:
                m = _MD_FACT_PREFIX_RE.match(line)
                text = m.group(1).strip() if m else line.strip()
                if text:
                    facts.append(text)
    except Exception:
        return []

    _md_facts_cache["mtime"] = mtime
    _md_facts_cache["facts"] = facts
    return facts


def _bm25_rank(query: str, corpus: list[str], k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
    """Pure-Python Okapi BM25. Returns [(doc_index, score), ...] for documents
    with score > 0, sorted by score descending."""
    query_terms = _tokenize(query)
    if not query_terms or not corpus:
        return []

    doc_tokens = [_tokenize(doc) for doc in corpus]
    doc_lens = [len(toks) for toks in doc_tokens]
    n_docs = len(corpus)
    avg_len = (sum(doc_lens) / n_docs) if n_docs else 0.0

    doc_freq: dict[str, int] = {}
    for toks in doc_tokens:
        for term in set(toks):
            doc_freq[term] = doc_freq.get(term, 0) + 1

    scores = [0.0] * n_docs
    for term in set(query_terms):
        df = doc_freq.get(term)
        if not df:
            continue
        idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        for i, toks in enumerate(doc_tokens):
            tf = toks.count(term)
            if tf == 0:
                continue
            norm_len = (doc_lens[i] / avg_len) if avg_len else 0.0
            denom = tf + k1 * (1 - b + b * norm_len)
            scores[i] += idf * (tf * (k1 + 1)) / denom

    ranked = [(i, s) for i, s in enumerate(scores) if s > 0]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked


def _rrf_fuse(*ranked_lists: list, k: int = 60) -> list:
    """Reciprocal Rank Fusion: RRF(d) = sum(1/(k+rank_i)) over every input
    list the item (by equality) appears in, rank 1-indexed. Each ranked_list
    is already ordered best-first. Returns items sorted by fused score
    descending, deduped, first-seen order used as a stable tiebreak."""
    fused_scores: dict = {}
    order: list = []
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            if item not in fused_scores:
                order.append(item)
                fused_scores[item] = 0.0
            fused_scores[item] += 1.0 / (k + rank)
    order.sort(key=lambda item: fused_scores[item], reverse=True)
    return order


def recall_memory(query):
    """Searches memory for fuzzy matches.

    Dense-only (ChromaDB) by default. When config.HYBRID_RECALL is on, fuses
    BM25 keyword ranking over memory.md with the ChromaDB dense results via
    Reciprocal Rank Fusion — the two stores hold identical fact strings
    (memorize_fact dual-writes), so fusion dedupes by exact string equality.
    Return format is byte-identical in both modes; only selection/ordering of
    the top-3 recalled facts can differ.
    """
    if not MEMORY_AVAILABLE: return "Error: Memory system offline."
    try:
        if not _config.HYBRID_RECALL:
            results = memory_collection.query(query_texts=[query], n_results=3)
            if results['documents'] and results['documents'][0]:
                retrieved = " | ".join(results['documents'][0])
                return f"Recalled context: {retrieved}"
            return "No relevant memories found."

        dense_results = memory_collection.query(query_texts=[query], n_results=10)
        dense_docs = dense_results['documents'][0] if dense_results.get('documents') else []

        md_facts = _load_md_facts()
        bm25_ranked = _bm25_rank(query, md_facts)[:10]
        sparse_docs = [md_facts[i] for i, _score in bm25_ranked]

        fused = _rrf_fuse(dense_docs, sparse_docs)[:3]
        if fused:
            retrieved = " | ".join(fused)
            return f"Recalled context: {retrieved}"
        return "No relevant memories found."
    except Exception as e:
        return f"Memory search failed: {e}"


def get_collection_stats() -> dict:
    """Return live stats about Aster's memory stores — used by self_knowledge."""
    stats: dict = {
        "chromadb_path": getattr(_config, "CHROMADB_PATH", None),
        "fact_collection": getattr(_config, "FACT_COLLECTION", None),
        "memory_file": MD_FILE,
        "memory_available": bool(MEMORY_AVAILABLE),
    }
    if MEMORY_AVAILABLE and memory_collection is not None:
        try:
            stats["fact_count"] = memory_collection.count()
        except Exception as e:
            stats["fact_count_error"] = str(e)

    try:
        if os.path.exists(MD_FILE):
            with open(MD_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            stats["memory_file_lines"] = len(lines)
            stats["memory_file_size_kb"] = round(os.path.getsize(MD_FILE) / 1024, 1)
        else:
            stats["memory_file_lines"] = 0
            stats["memory_file_size_kb"] = 0.0
    except Exception as e:
        stats["memory_file_error"] = str(e)

    try:
        rag_dir = getattr(_config, "RAG_VAULT_DIR", None)
        if rag_dir and os.path.isdir(rag_dir):
            stats["rag_vault_documents"] = sum(
                1 for n in os.listdir(rag_dir) if n.lower().endswith(".md")
            )
    except Exception:
        pass

    return stats


# ============================================================================
# RAW CONVERSATION ARCHIVE — daily flight recorder (fallback for failed recall)
# ============================================================================
def log_raw_turn(user_text: str, assistant_text: str) -> None:
    """Append the verbatim user/assistant exchange to today's dated transcript
    file (Aster_Vault/Conversations/YYYY-MM-DD.md). Best-effort; never raises.

    Independent of the ChromaDB/memory.md fact pipeline above — this keeps the
    full raw exchange rather than extracted facts, so a failed recall_memory()
    query has something to grep as a last resort. Skips system-internal
    nudges; native image payloads are collapsed to a short placeholder
    instead of dumping base64 into the file.
    """
    try:
        text = str(user_text)
        if text.startswith("[System Internal"):
            return

        if text.startswith("[NATIVE_IMAGE_PAYLOAD:"):
            bracket_end = text.find("]")
            user_display = text[bracket_end + 1:].strip() if bracket_end != -1 else "(image)"
        else:
            user_display = text
        user_display = re.sub(r"\[Speaker:[^\]]*\]", "", user_display)
        user_display = re.sub(r"\[Mood:[^\]]*\]", "", user_display).strip()
        if not user_display:
            return

        assistant_display = str(assistant_text or "").strip()

        now = datetime.now()
        os.makedirs(_config.CONVERSATIONS_DIR, exist_ok=True)
        day_file = os.path.join(_config.CONVERSATIONS_DIR, now.strftime("%Y-%m-%d") + ".md")
        with open(day_file, "a", encoding="utf-8") as f:
            f.write(f"### {now.strftime('%H:%M:%S')}\n")
            f.write(f"**{_config.OWNER_NAME}:** {user_display}\n\n")
            f.write(f"**Aster:** {assistant_display}\n\n---\n\n")
    except Exception:
        pass


# ============================================================================
# MEMORY MANAGER: Sliding Window with (optionally) calibrated token counting
# ============================================================================
# The primary estimator is still the char/ratio heuristic — there is no local
# tokenizer since the engine is a REST-only llama-server. When
# config.EXACT_TOKEN_COUNT is on, the chars-per-token ratio is calibrated
# against llama-server's /tokenize endpoint (not the generation endpoint — no
# LLM inference, just tokenization), cached, and refreshed at most once every
# _RATIO_REFRESH_SECONDS AND only when the running estimate is already close
# to the trim budget. This bounds it to well under one network call per turn
# even though trim_memory() itself is called ~15+ times per turn.
_TOKENIZE_URL = "http://localhost:8080/tokenize"
_RATIO_REFRESH_SECONDS = 60.0
_IMAGE_TOKEN_ESTIMATE = 256  # flat per-image-part charge, not len(base64)//ratio

_ratio_lock = threading.Lock()
_token_ratio = {"value": 4.0, "last_calibrated": 0.0}


def _extract_text_and_image_count(msg_list) -> tuple[str, int]:
    """Concatenate only {'type':'text'} content parts across messages; count
    image_url parts separately instead of stringifying their base64 payload."""
    parts: list[str] = []
    image_count = 0
    for m in msg_list:
        content = m.get("content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    parts.append(str(part))
                    continue
                if part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
                elif part.get("type") == "image_url":
                    image_count += 1
        else:
            parts.append(str(content))
    return "\n".join(parts), image_count


def _tokenize_count(text: str) -> int | None:
    """Exact token count via llama-server's /tokenize endpoint. None on any
    failure (server down, endpoint missing, timeout) — callers must fall back
    to the heuristic ratio."""
    if not text:
        return 0
    try:
        r = requests.post(_TOKENIZE_URL, json={"content": text}, timeout=5)
        r.raise_for_status()
        tokens = r.json().get("tokens")
        return len(tokens) if isinstance(tokens, list) else None
    except Exception:
        return None


def _maybe_recalibrate_ratio(sample_text: str) -> None:
    """Refresh the cached chars-per-token ratio if stale. The refresh slot is
    claimed before the network call so a failure doesn't cause a retry storm."""
    now = time.time()
    with _ratio_lock:
        if now - _token_ratio["last_calibrated"] < _RATIO_REFRESH_SECONDS:
            return
        _token_ratio["last_calibrated"] = now

    exact = _tokenize_count(sample_text)
    if exact and len(sample_text) > 0:
        with _ratio_lock:
            _token_ratio["value"] = len(sample_text) / exact


def _calibrated_ratio() -> float:
    with _ratio_lock:
        return _token_ratio["value"]


def estimate_tokens_text(text: str, budget: int | None = None) -> int:
    """Estimate token count for a single text blob. When
    config.EXACT_TOKEN_COUNT is on and the heuristic estimate is already
    within 75% of `budget` (if given), the ratio is (re)calibrated. Shared by
    trim_memory() and core.brain.check_context_health() so both use the same
    calibrated ratio."""
    text = text or ""
    ratio = _calibrated_ratio()
    estimate = int(len(text) / ratio)

    if _config.EXACT_TOKEN_COUNT and (budget is None or estimate > budget * 0.75):
        _maybe_recalibrate_ratio(text)
        ratio = _calibrated_ratio()
        estimate = int(len(text) / ratio)

    return estimate


def _estimate_tokens(msg_list, budget: int | None = None) -> int:
    """Token count estimate for a full message list — see estimate_tokens_text
    for the calibration rule. Image parts are charged a flat estimate instead
    of counting their base64 payload as text (a pre-existing over-trim bug in
    the old fixed-heuristic path)."""
    text, image_count = _extract_text_and_image_count(msg_list)
    return estimate_tokens_text(text, budget=budget) + image_count * _IMAGE_TOKEN_ESTIMATE


def _pop_oldest_turn_unit(msg_list) -> None:
    """Pop the oldest non-system message *unit* from msg_list (in place).

    In the native tool-calling path an assistant message carrying tool_calls
    is followed by role:"tool" messages whose tool_call_id points at it.
    Popping only the assistant half leaves orphaned tool messages that the
    model/template can choke on — so the pair is one unit:
    the assistant message and every immediately-following role:"tool" message
    go together. A role:"tool" message found alone at index 1 is already an
    orphan and is likewise popped so it never survives a trim.
    """
    removed = msg_list.pop(1)
    if removed.get("role") == "assistant" and removed.get("tool_calls"):
        while len(msg_list) > 1 and msg_list[1].get("role") == "tool":
            msg_list.pop(1)


def trim_memory(msg_list, max_tokens=None):
    """Sliding window that trims oldest messages to fit within the context budget.

    Always preserves index 0 (system prompt). Primary estimator is the
    character-based heuristic (calibrated against /tokenize when
    config.EXACT_TOKEN_COUNT is on — see _estimate_tokens). Assistant
    tool_calls messages and their role:"tool" results are popped as one unit
    (see _pop_oldest_turn_unit) so trimming never orphans a tool message.
    """
    if max_tokens is None:
        max_tokens = int(_config.N_CTX * 0.9)  # Leave 10% headroom for generation

    if len(msg_list) <= 2:
        return msg_list

    total = _estimate_tokens(msg_list, budget=max_tokens)

    while total > max_tokens and len(msg_list) > 2:
        _pop_oldest_turn_unit(msg_list)
        # Re-count after removal (cheaper than per-message tokenization)
        total = _estimate_tokens(msg_list, budget=max_tokens)

    return msg_list
