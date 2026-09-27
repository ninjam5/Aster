"""Tests for RRF hybrid memory recall (core.memory: _bm25_rank, _rrf_fuse,
_load_md_facts, recall_memory). No llama-server or real ChromaDB required —
memory_collection is a fake object with a canned .query() return."""
import pytest

import config
import core.memory as memory


class FakeCollection:
    """Stand-in for ChromaDB's collection.query()."""
    def __init__(self, canned_docs):
        self._canned = canned_docs

    def query(self, query_texts, n_results=3):
        return {"documents": [self._canned[:n_results]]}


FACTS = [
    "Mohamed likes pizza on Fridays",
    "The GPU serial number is XJ992-ALPHA-7",
    "Mohamed prefers dark mode in all apps",
    "The office wifi password rotates monthly",
]


@pytest.fixture
def md_file(tmp_path, monkeypatch):
    path = tmp_path / "memory.md"
    with open(path, "w", encoding="utf-8") as f:
        for i, fact in enumerate(FACTS):
            f.write(f"- **[2026-01-0{i + 1} 10:00:00]** {fact}\n")
    monkeypatch.setattr(memory, "MD_FILE", str(path))
    memory._md_facts_cache["mtime"] = None
    memory._md_facts_cache["facts"] = []
    return FACTS


# ── _load_md_facts ────────────────────────────────────────────────────────────

def test_load_md_facts_strips_timestamp_prefix(md_file):
    assert memory._load_md_facts() == md_file


def test_load_md_facts_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "MD_FILE", str(tmp_path / "nope.md"))
    assert memory._load_md_facts() == []


# ── BM25 ──────────────────────────────────────────────────────────────────────

def test_bm25_rank_keyword_exact_wins(md_file):
    ranked = memory._bm25_rank("XJ992-ALPHA-7 serial number", md_file)
    assert ranked
    assert ranked[0][0] == 1  # index of the serial-number fact


def test_bm25_rank_no_overlap_returns_empty():
    corpus = ["apples and oranges", "bananas and grapes"]
    assert memory._bm25_rank("zebra giraffe", corpus) == []


def test_bm25_rank_empty_corpus():
    assert memory._bm25_rank("anything", []) == []


# ── RRF fusion ────────────────────────────────────────────────────────────────

def test_rrf_fuse_hand_computed():
    dense = ["docA", "docC", "docB"]
    sparse = ["docB", "docA"]
    # docA: 1/61 (dense r1) + 1/62 (sparse r2) ≈ 0.03252
    # docB: 1/63 (dense r3) + 1/61 (sparse r1) ≈ 0.03226
    # docC: 1/62 (dense r2 only)              ≈ 0.01613
    fused = memory._rrf_fuse(dense, sparse, k=60)
    assert fused == ["docA", "docB", "docC"]


def test_rrf_fuse_dedupes_by_equality():
    fused = memory._rrf_fuse(["x", "y"], ["x"], k=60)
    assert fused.count("x") == 1
    assert set(fused) == {"x", "y"}


def test_rrf_fuse_empty_inputs():
    assert memory._rrf_fuse([], []) == []


# ── recall_memory integration ────────────────────────────────────────────────

def test_recall_memory_offline(monkeypatch):
    monkeypatch.setattr(memory, "MEMORY_AVAILABLE", False)
    assert memory.recall_memory("anything") == "Error: Memory system offline."


def test_recall_memory_dense_only_format_is_byte_identical_when_flag_off(monkeypatch, md_file):
    monkeypatch.setattr(config, "HYBRID_RECALL", False)
    monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True)
    monkeypatch.setattr(memory, "memory_collection", FakeCollection([md_file[0], md_file[2]]))
    result = memory.recall_memory("pizza")
    assert result == f"Recalled context: {md_file[0]} | {md_file[2]}"


def test_recall_memory_dense_only_no_match(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HYBRID_RECALL", False)
    monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True)
    monkeypatch.setattr(memory, "memory_collection", FakeCollection([]))
    assert memory.recall_memory("anything") == "No relevant memories found."


def test_recall_memory_hybrid_surfaces_keyword_exact_dense_miss(monkeypatch, md_file):
    """The core value proposition: dense retrieval 'misses' the serial-number
    fact entirely (simulating a semantic-embedding gap), but BM25 over
    memory.md finds it on an exact keyword match, and RRF fusion surfaces it
    in the final top-3."""
    monkeypatch.setattr(config, "HYBRID_RECALL", True)
    monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True)
    dense_miss = [md_file[0], md_file[2], md_file[3]]  # no serial-number fact
    monkeypatch.setattr(memory, "memory_collection", FakeCollection(dense_miss))

    result = memory.recall_memory("XJ992-ALPHA-7 serial number")
    assert "XJ992-ALPHA-7" in result
    assert result.startswith("Recalled context: ")


def test_recall_memory_hybrid_no_match_string(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HYBRID_RECALL", True)
    monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True)
    monkeypatch.setattr(memory, "MD_FILE", str(tmp_path / "empty.md"))
    monkeypatch.setattr(memory, "memory_collection", FakeCollection([]))
    assert memory.recall_memory("anything") == "No relevant memories found."


def test_recall_memory_hybrid_return_prefix_exact(monkeypatch, md_file):
    monkeypatch.setattr(config, "HYBRID_RECALL", True)
    monkeypatch.setattr(memory, "MEMORY_AVAILABLE", True)
    monkeypatch.setattr(memory, "memory_collection", FakeCollection([md_file[0]]))
    result = memory.recall_memory("pizza")
    assert result.startswith("Recalled context: ")
    assert " | " in result or result.count("Recalled context: ") == 1
