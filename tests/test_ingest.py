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
    LoaderChangedError,
    UpstreamHashMismatchError,
    load_from_raw,
    sha256_file,
    verify_counts,
    verify_download_hash,
)
from src.ingest import bsb as ingest_module

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


# ---------------------------------------------------------------------------
# Task 2a — force reingest and loader_version guard
# ---------------------------------------------------------------------------

def _mutate_one_verse(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE verses SET text = 'TAMPERED' WHERE book='Genesis' AND chapter=1 AND verse=1"
        )
        conn.commit()


def test_reload_without_force_and_unchanged_loader_leaves_text_alone(tmp_path):
    """Baseline: with the same loader version and no force, a re-run does
    NOT re-extract — this is the existing idempotency behaviour and it must
    stay in place so ordinary re-runs are cheap."""
    db_path = tmp_path / "bible.db"
    load_from_raw(TINY_RAW, db_path, **TINY_EXPECTED)
    _mutate_one_verse(db_path)
    load_from_raw(TINY_RAW, db_path, **TINY_EXPECTED)
    with _open(db_path) as conn:
        (text,) = conn.execute(
            "SELECT text FROM verses WHERE book='Genesis' AND chapter=1 AND verse=1"
        ).fetchone()
    assert text == "TAMPERED"


def test_reload_refuses_when_loader_changed_without_force(tmp_path, monkeypatch):
    """Simulate a loader source edit: the stored loader_version no longer
    matches the running one. Without --force the loader MUST refuse rather
    than leave stale text in the DB — that was the Stage 2 failure mode."""
    db_path = tmp_path / "bible.db"
    load_from_raw(TINY_RAW, db_path, **TINY_EXPECTED)
    _mutate_one_verse(db_path)

    monkeypatch.setattr(ingest_module, "_loader_version", lambda: "new-loader-hash-" + "0" * 48)
    with pytest.raises(LoaderChangedError) as exc:
        load_from_raw(TINY_RAW, db_path, **TINY_EXPECTED)
    msg = str(exc.value)
    assert "new-loader-hash" in msg
    assert "--force" in msg


def test_force_reload_restores_text_after_loader_change(tmp_path, monkeypatch):
    """With --force, a changed loader wipes and re-extracts. The tampered
    verse gets its original text back and there are no duplicate rows."""
    db_path = tmp_path / "bible.db"
    load_from_raw(TINY_RAW, db_path, **TINY_EXPECTED)
    _mutate_one_verse(db_path)

    monkeypatch.setattr(ingest_module, "_loader_version", lambda: "new-loader-hash-" + "0" * 48)
    load_from_raw(TINY_RAW, db_path, force=True, **TINY_EXPECTED)

    with _open(db_path) as conn:
        (text,) = conn.execute(
            "SELECT text FROM verses WHERE book='Genesis' AND chapter=1 AND verse=1"
        ).fetchone()
        (n,) = conn.execute("SELECT COUNT(*) FROM verses").fetchone()
    assert text.startswith("In the beginning")
    assert n == 3  # no duplicates after wipe-and-reload
    with _open(db_path) as conn:
        meta = conn.execute("SELECT loader_version FROM corpus_meta").fetchone()
    assert meta["loader_version"] == "new-loader-hash-" + "0" * 48


# ---------------------------------------------------------------------------
# Task 2e — download hash enforcement
# ---------------------------------------------------------------------------

def test_verify_download_hash_accepts_expected_bytes(tmp_path):
    import hashlib
    payload = b"exactly this content"
    p = tmp_path / "file.bin"
    p.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert verify_download_hash(p, expected=expected) == expected


def test_verify_download_hash_refuses_mismatch(tmp_path):
    p = tmp_path / "file.bin"
    p.write_bytes(b"unexpected content")
    with pytest.raises(UpstreamHashMismatchError) as exc:
        verify_download_hash(p, expected="0" * 64)
    msg = str(exc.value)
    assert "expected: " in msg and "actual: " in msg
    assert "--allow-hash-change" in msg


def test_verify_download_hash_allow_change_bypasses(tmp_path, capsys):
    p = tmp_path / "file.bin"
    p.write_bytes(b"unexpected content")
    returned = verify_download_hash(p, expected="0" * 64, allow_change=True)
    # Returns the actual hash so the caller can proceed with it.
    assert returned != "0" * 64
    err = capsys.readouterr().err
    assert "[warn]" in err and "--allow-hash-change" in err
