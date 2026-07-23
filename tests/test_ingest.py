"""Idempotency and integrity tests for the BSB loader.

These use a tiny hand-crafted raw dict (three verses across two chapters
in one book) rather than the real 7 MB JSON, so the tests are fast and
don't need network access.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.ingest.bsb import (
    CorpusIntegrityError,
    load_from_raw,
    sha256_file,
    verify_counts,
)

# Minimal raw dict in the same shape as helloao's complete.json.
TINY_RAW = {
    "translation": {
        "id": "TINY",
        "name": "Tiny Test Bible",
        "sha256": "upstream-hash",
    },
    "books": [
        {
            "id": "GEN",
            "name": "Genesis",
            "commonName": "Genesis",
            "chapters": [
                {
                    "chapter": {
                        "number": 1,
                        "content": [
                            {"type": "verse", "number": 1, "content": [
                                "In the beginning God created the heavens and the earth."
                            ]},
                            {"type": "verse", "number": 2, "content": [
                                "Now the earth was formless and void."
                            ]},
                        ],
                    }
                },
                {
                    "chapter": {
                        "number": 2,
                        "content": [
                            {"type": "verse", "number": 1, "content": [
                                "Thus the heavens and the earth were completed."
                            ]},
                        ],
                    }
                },
            ],
        }
    ],
}

TINY_EXPECTED = dict(expected_books=1, expected_chapters=2, expected_verses=3)


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_load_populates_verses_and_meta(tmp_path):
    db_path = tmp_path / "bible.db"
    books, chapters, verses = load_from_raw(
        TINY_RAW, db_path,
        source_url="http://example/tiny.json",
        sha256_local="local-hash",
        retrieved_at="2026-07-24T00:00:00+00:00",
        **TINY_EXPECTED,
    )
    assert (books, chapters, verses) == (1, 2, 3)

    with _open(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM verses").fetchone()
        assert rows["n"] == 3
        meta = conn.execute("SELECT * FROM corpus_meta").fetchone()
        assert meta["translation"] == "TINY"
        assert meta["sha256_local"] == "local-hash"
        assert meta["sha256_upstream"] == "upstream-hash"
        assert meta["book_count"] == 1
        assert meta["verse_count"] == 3
        assert meta["retrieved_at"] == "2026-07-24T00:00:00+00:00"


def test_load_is_idempotent(tmp_path):
    """Loading twice must not duplicate rows and must leave one meta row."""
    db_path = tmp_path / "bible.db"
    load_from_raw(TINY_RAW, db_path, sha256_local="v1", **TINY_EXPECTED)
    load_from_raw(TINY_RAW, db_path, sha256_local="v2", **TINY_EXPECTED)

    with _open(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM verses").fetchone()[0] == 3
        meta_rows = conn.execute("SELECT * FROM corpus_meta").fetchall()
        assert len(meta_rows) == 1
        # The second load should have refreshed the hash column.
        assert meta_rows[0]["sha256_local"] == "v2"


def test_count_mismatch_raises(tmp_path):
    """A raw dict that produces fewer verses than declared must fail loudly."""
    db_path = tmp_path / "bible.db"
    truncated = {
        "translation": {"id": "TINY", "sha256": ""},
        "books": [
            {
                "id": "GEN", "name": "Genesis", "commonName": "Genesis",
                "chapters": [
                    {"chapter": {"number": 1, "content": [
                        {"type": "verse", "number": 1, "content": ["only verse"]},
                    ]}},
                ],
            }
        ],
    }
    with pytest.raises(CorpusIntegrityError) as exc:
        load_from_raw(truncated, db_path, expected_books=1,
                      expected_chapters=1, expected_verses=99)
    msg = str(exc.value)
    assert "verses" in msg and "1" in msg and "99" in msg


def test_missing_book_or_chapter_raises(tmp_path):
    """Wrong book or chapter count is also fatal."""
    db_path = tmp_path / "bible.db"
    with pytest.raises(CorpusIntegrityError):
        load_from_raw(TINY_RAW, db_path,
                      expected_books=66,       # actual is 1
                      expected_chapters=2, expected_verses=3)


def test_sha256_file_matches_stdlib(tmp_path):
    """Round-trip check: our streaming hasher matches the direct hash."""
    import hashlib
    payload = b"the quick brown fox jumps over the lazy dog" * 10_000
    p = tmp_path / "blob.bin"
    p.write_bytes(payload)
    assert sha256_file(p) == hashlib.sha256(payload).hexdigest()


def test_verify_counts_reports_multiple_problems(tmp_path):
    """A wrong-on-every-axis load should list all three problems in the error."""
    db_path = tmp_path / "bible.db"
    load_from_raw(TINY_RAW, db_path, **TINY_EXPECTED)
    with _open(db_path) as conn, pytest.raises(CorpusIntegrityError) as exc:
        verify_counts(conn, "TINY",
                      expected_books=2, expected_chapters=3, expected_verses=4)
    msg = str(exc.value)
    assert "books" in msg and "chapters" in msg and "verses" in msg
