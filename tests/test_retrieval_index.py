"""Tests for src.retrieval.build_index — the FTS5 build step and its
corpus-hash version guard.

The build step's contract:
- Refuses to build against a missing DB or an empty verses table.
- Refuses to build without a corpus_meta.sha256_local to pin against.
- Populates a `verses_fts` virtual table AND a `retrieval_index_meta`
  row carrying (index_version, corpus_sha256, built_at).
- Is idempotent — rebuilding drops and recreates cleanly.

Query-side sync guard (that a corpus SHA drift raises) is covered in
tests/test_retrieval.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.retrieval.build_index import (
    BuildIndexError,
    build_index,
    index_version,
    main,
)


def _add_corpus_meta(db_path: Path, sha: str = "testsha000000000000") -> None:
    """The tests/conftest.py fixture_db doesn't ship corpus_meta because
    the parser tests don't need it. build_index does — it must pin
    against a corpus SHA — so seed a minimal row per-test."""
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


def test_build_creates_fts_table_and_meta_row(fixture_db):
    _add_corpus_meta(fixture_db, sha="cafebabe" * 8)
    n, sha = build_index(db_path=fixture_db)
    assert n == 13   # count of _SEED_ROWS in tests/conftest.py
    assert sha == "cafebabe" * 8
    with sqlite3.connect(fixture_db) as conn:
        has_fts = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type IN ('table','view') AND name='verses_fts'"
        ).fetchone()
        assert has_fts is not None
        row = conn.execute(
            "SELECT index_version, corpus_sha256, built_at "
            "FROM retrieval_index_meta WHERE translation='BSB'"
        ).fetchone()
        assert row is not None
        assert row[0] == index_version()
        assert row[1] == "cafebabe" * 8
        assert row[2]   # non-empty ISO timestamp


def test_build_populates_all_seed_rows(fixture_db):
    _add_corpus_meta(fixture_db)
    build_index(db_path=fixture_db)
    with sqlite3.connect(fixture_db) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM verses_fts").fetchone()
    assert n == 13


def test_build_is_idempotent(fixture_db):
    _add_corpus_meta(fixture_db)
    build_index(db_path=fixture_db)
    build_index(db_path=fixture_db)   # must not raise
    with sqlite3.connect(fixture_db) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM verses_fts").fetchone()
    assert n == 13   # not doubled


def test_build_raises_when_db_missing(tmp_path):
    with pytest.raises(BuildIndexError) as exc:
        build_index(db_path=tmp_path / "does_not_exist.db")
    assert "corpus DB not found" in str(exc.value)


def test_build_raises_when_verses_table_missing(tmp_path):
    p = tmp_path / "bare.db"
    with sqlite3.connect(p) as conn:
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
    with pytest.raises(BuildIndexError) as exc:
        build_index(db_path=p)
    assert "verses" in str(exc.value)


def test_build_raises_when_no_rows_for_translation(fixture_db):
    _add_corpus_meta(fixture_db)
    with pytest.raises(BuildIndexError):
        build_index(db_path=fixture_db, translation="XYZ")


def test_build_raises_when_corpus_sha_missing(fixture_db):
    # fixture_db has verses but no corpus_meta row. Refuse to build an
    # unpinnable index — the whole point of the guard is to catch stale
    # indices, and it can't do that without a corpus SHA to compare to.
    with pytest.raises(BuildIndexError) as exc:
        build_index(db_path=fixture_db)
    assert "corpus_meta" in str(exc.value)


def test_cli_returns_zero_on_success(fixture_db, capsys):
    _add_corpus_meta(fixture_db)
    rc = main(["--db-path", str(fixture_db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verses_fts" in out
    assert "13 rows indexed" in out


def test_cli_returns_nonzero_on_missing_db(tmp_path, capsys):
    rc = main(["--db-path", str(tmp_path / "nope.db")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "corpus DB not found" in err
