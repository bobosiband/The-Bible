"""Tests for src.retrieval.query — the retrieve() API.

Behavioural contract:
- Direct-lookup for a parseable reference wins over keyword scoring.
- A parseable reference that doesn't exist returns empty, NOT keyword
  fallback (the user was explicit; a wrong-passage keyword hit would be
  worse than nothing).
- Keyword search returns [] on no lexical overlap, not garbage.
- Context windows are symmetric and clipped to chapter boundaries.
- Same query + same corpus + same index → same passages in same order.
- The index-sync guard refuses to query when corpus bytes drift.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.corpus.references import CorpusUnavailableError
from src.retrieval import (
    IndexOutOfSyncError,
    RetrievalIndexMissingError,
    RetrievedPassage,
    retrieve,
)
from src.retrieval.build_index import build_index

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DB = REPO_ROOT / "data" / "corpus" / "bible.db"


def _seed_corpus_meta(db_path: Path, sha: str = "fixturesha0000") -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corpus_meta (
                translation      TEXT PRIMARY KEY,
                source_url       TEXT,
                retrieved_at     TEXT,
                sha256_local     TEXT,
                sha256_upstream  TEXT,
                book_count       INTEGER,
                chapter_count    INTEGER,
                verse_count      INTEGER,
                loader_version   TEXT
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO corpus_meta "
            "(translation, sha256_local) VALUES ('BSB', ?)",
            (sha,),
        )
        conn.commit()


@pytest.fixture
def indexed_fixture_db(fixture_db):
    _seed_corpus_meta(fixture_db)
    build_index(db_path=fixture_db)
    return fixture_db


# ---------------------------------------------------------------------------
# Direct-lookup path
# ---------------------------------------------------------------------------

def test_direct_lookup_single_verse(indexed_fixture_db):
    results = retrieve("John 3:16", db_path=indexed_fixture_db)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, RetrievedPassage)
    assert r.reference == "John 3:16"
    assert r.book == "John"
    assert r.chapter == 3
    assert r.verse_start == 16
    assert r.verse_end == 16
    assert r.rank == 1
    assert r.text.startswith("For God so loved")


def test_direct_lookup_beats_keyword_scoring(indexed_fixture_db):
    """A query that contains an explicit reference must not have its
    result decided by keyword ranking against the other verses."""
    results = retrieve(
        "What does John 3:16 say about love and patience?",
        db_path=indexed_fixture_db, k=5,
    )
    assert len(results) == 1
    assert results[0].reference == "John 3:16"
    assert results[0].rank == 1


def test_direct_lookup_range(indexed_fixture_db):
    results = retrieve("1 Corinthians 13:4-7", db_path=indexed_fixture_db)
    assert len(results) == 1
    r = results[0]
    assert r.reference == "1 Corinthians 13:4-7"
    assert r.verse_start == 4
    assert r.verse_end == 7


def test_direct_lookup_whole_chapter(indexed_fixture_db):
    results = retrieve("Psalm 23", db_path=indexed_fixture_db)
    assert len(results) == 1
    r = results[0]
    assert r.reference == "Psalms 23"
    assert r.verse_start == 1
    assert r.verse_end == 6


def test_direct_lookup_nonexistent_ref_returns_empty(indexed_fixture_db):
    """Genesis 51:1 parses but the passage does not exist in the corpus.
    The retriever returns [], not a keyword fallback."""
    results = retrieve("Genesis 51:1", db_path=indexed_fixture_db)
    assert results == []


def test_direct_lookup_multiple_refs(indexed_fixture_db):
    results = retrieve(
        "Compare John 3:16 with 1 Corinthians 13:4-7.",
        db_path=indexed_fixture_db,
    )
    assert len(results) == 2
    assert [r.reference for r in results] == ["John 3:16", "1 Corinthians 13:4-7"]
    assert [r.rank for r in results] == [1, 2]


def test_direct_lookup_context_expands_symmetrically(indexed_fixture_db):
    """Requesting John 3:16 with context=1 pulls in John 3:15 and 3:17;
    the fixture only has 3:16 and 3:17, so the window becomes 16-17."""
    results = retrieve("John 3:16", db_path=indexed_fixture_db, context=1)
    assert len(results) == 1
    r = results[0]
    assert r.verse_start == 16
    assert r.verse_end == 17
    assert r.reference == "John 3:16-17"


def test_direct_lookup_context_clipped_at_chapter_end(indexed_fixture_db):
    """1 Cor 13 in the fixture only has verses 4-7. Asking for verse 5
    with a huge context still stops at the chapter's own boundary."""
    results = retrieve(
        "1 Corinthians 13:5", db_path=indexed_fixture_db, context=1000,
    )
    assert len(results) == 1
    r = results[0]
    assert r.chapter == 13
    assert r.verse_start == 4
    assert r.verse_end == 7


def test_direct_lookup_score_is_zero(indexed_fixture_db):
    """Direct-lookup passages carry a sentinel score of 0.0 so they sit
    above any BM25 abstention threshold in the grounded pipeline."""
    results = retrieve("John 3:16", db_path=indexed_fixture_db)
    assert results[0].score == 0.0


# ---------------------------------------------------------------------------
# Keyword path (fixture DB)
# ---------------------------------------------------------------------------

def test_keyword_no_overlap_returns_empty(indexed_fixture_db):
    results = retrieve("quxxx zorrrp fribble", db_path=indexed_fixture_db)
    assert results == []


def test_keyword_returns_relevant_verse(indexed_fixture_db):
    # "patient" appears in "Love is patient" (1 Cor 13:4)
    results = retrieve("patient", db_path=indexed_fixture_db, k=5)
    assert results
    assert results[0].book == "1 Corinthians"
    assert results[0].chapter == 13
    assert results[0].verse_start == 4


def test_keyword_respects_k(indexed_fixture_db):
    results = retrieve("Lord", db_path=indexed_fixture_db, k=2)
    assert len(results) <= 2


def test_keyword_context_within_chapter(indexed_fixture_db):
    # "shepherd" appears in Psalms 23:1. context=1 pulls verses 1-2.
    results = retrieve("shepherd", db_path=indexed_fixture_db, context=1, k=1)
    assert results
    r = results[0]
    assert r.book == "Psalms" and r.chapter == 23
    assert r.verse_start == 1
    assert r.verse_end == 2


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_determinism_across_repeated_runs(indexed_fixture_db):
    # Use a term that hits multiple fixture verses so the test proves
    # ORDER stability, not just empty-list equality.
    a = retrieve("Lord", db_path=indexed_fixture_db, k=5)
    b = retrieve("Lord", db_path=indexed_fixture_db, k=5)
    c = retrieve("Lord", db_path=indexed_fixture_db, k=5)
    assert len(a) > 1
    assert a == b == c


# ---------------------------------------------------------------------------
# Empty / invalid input
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_empty_query_returns_empty(indexed_fixture_db, bad):
    assert retrieve(bad, db_path=indexed_fixture_db) == []


def test_non_str_query_returns_empty(indexed_fixture_db):
    assert retrieve(None, db_path=indexed_fixture_db) == []
    assert retrieve(123, db_path=indexed_fixture_db) == []


# ---------------------------------------------------------------------------
# Missing DB, missing index, out-of-sync index
# ---------------------------------------------------------------------------

def test_missing_db_raises(tmp_path):
    with pytest.raises(CorpusUnavailableError):
        retrieve("John 3:16", db_path=tmp_path / "nope.db")


def test_missing_index_raises(fixture_db):
    """fixture_db has verses but no verses_fts — must refuse to query."""
    with pytest.raises(RetrievalIndexMissingError):
        retrieve("John 3:16", db_path=fixture_db)


def test_out_of_sync_index_raises(indexed_fixture_db):
    """After the index is built, mutate corpus_meta.sha256_local to
    simulate a re-ingest. The query must refuse to run — not silently
    score against text that may no longer match."""
    with sqlite3.connect(indexed_fixture_db) as conn:
        conn.execute(
            "UPDATE corpus_meta SET sha256_local = 'DIFFERENT_SHA' "
            "WHERE translation = 'BSB'"
        )
        conn.commit()
    with pytest.raises(IndexOutOfSyncError):
        retrieve("love", db_path=indexed_fixture_db)


# ---------------------------------------------------------------------------
# Real corpus (marked @corpus)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_indexed_db():
    if not REAL_DB.exists():
        pytest.skip("no corpus DB present")
    # Idempotent build against the real corpus. Safe to run: DROP + CREATE
    # + INSERT, and retrieval_index_meta is INSERT OR REPLACE.
    build_index(db_path=REAL_DB)
    return REAL_DB


@pytest.mark.corpus
def test_real_direct_lookup_returns_exact_verse(real_indexed_db):
    results = retrieve("Romans 8:28", db_path=real_indexed_db)
    assert len(results) == 1
    r = results[0]
    assert r.reference == "Romans 8:28"
    assert r.text.startswith("And we know that God works all things")


@pytest.mark.corpus
def test_real_psalms_distinctive_phrase(real_indexed_db):
    """A distinctive Psalms phrase must retrieve Ps 23:1 at rank 1.
    A single-word "shepherd" query is too ambiguous (many books contain
    the word); the phrase 'shepherd I shall not want' is uniquely Ps 23:1
    in BSB."""
    results = retrieve(
        "shepherd I shall not want", db_path=real_indexed_db, k=5,
    )
    assert results
    top = results[0]
    assert top.book == "Psalms"
    assert top.chapter == 23
    assert top.verse_start == 1


@pytest.mark.corpus
def test_real_epistles_distinctive_phrase(real_indexed_db):
    """1 Cor 13:4's 'Love is patient, love is kind' — a distinctive
    phrase that under AND-semantics uniquely identifies the verse."""
    results = retrieve(
        "love is patient love is kind", db_path=real_indexed_db, k=5,
    )
    assert results
    top = results[0]
    assert top.book == "1 Corinthians"
    assert top.chapter == 13
    assert top.verse_start == 4


@pytest.mark.corpus
def test_real_determinism(real_indexed_db):
    """Same query on the real index yields the same passages in the
    same order across three consecutive runs."""
    a = retrieve("faith hope love", db_path=real_indexed_db, k=5)
    b = retrieve("faith hope love", db_path=real_indexed_db, k=5)
    c = retrieve("faith hope love", db_path=real_indexed_db, k=5)
    assert a == b == c


@pytest.mark.corpus
def test_real_context_never_crosses_chapter(real_indexed_db):
    """Ask for a first-verse-of-chapter hit with a huge context window;
    the returned verse_start must be within the same chapter (never 0
    or the previous chapter's last verse). The BSB phrase 'In the
    beginning God created' is uniquely Gen 1:1."""
    results = retrieve("In the beginning God created", db_path=real_indexed_db,
                        k=1, context=1000)
    assert results
    r = results[0]
    assert r.book == "Genesis"
    assert r.chapter == 1
    assert r.verse_start >= 1
