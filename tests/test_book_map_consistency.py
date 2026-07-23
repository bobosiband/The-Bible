"""Cross-check the parser's hand-written canonical book names against the
DB's ingested book names. A mismatch on either side makes `normalize_book`
succeed and the subsequent lookup return nothing — a silent hole in every
citation check. This test closes it.

Marked `@pytest.mark.corpus`: needs the real bible.db.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.corpus.references import BOOKS, get_range, normalize_book

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "corpus" / "bible.db"


@pytest.mark.corpus
def test_every_canonical_book_resolves_against_real_db():
    """Every one of the 66 canonical names in BOOKS must return a non-empty
    chapter 1 from the ingested BSB corpus."""
    failures = []
    for canonical in BOOKS:
        try:
            rows = get_range(canonical, 1)
        except Exception as e:  # noqa: BLE001 — we want the diagnosis
            failures.append((canonical, f"raised {type(e).__name__}: {e}"))
            continue
        if not rows:
            failures.append((canonical, "get_range(book, 1) returned []"))
    assert not failures, (
        f"{len(failures)} of {len(BOOKS)} canonical names do not resolve:\n"
        + "\n".join(f"  - {b}: {reason}" for b, reason in failures)
    )


@pytest.mark.corpus
def test_every_db_book_name_normalises_back_to_itself():
    """Every distinct book value the DB actually holds must round-trip
    through normalize_book to the same string. A DB-side name the parser
    doesn't know is a citation the parser will silently fail to find."""
    with sqlite3.connect(DB_PATH) as conn:
        db_books = [row[0] for row in conn.execute(
            "SELECT DISTINCT book FROM verses WHERE translation = 'BSB'"
        )]
    failures = []
    for name in db_books:
        normalised = normalize_book(name)
        if normalised != name:
            failures.append((name, normalised))
    assert not failures, (
        "DB book names do not round-trip through normalize_book:\n"
        + "\n".join(f"  - {name!r} → {norm!r}" for name, norm in failures)
    )


@pytest.mark.corpus
def test_book_counts_agree():
    """Number of canonical names in the parser must equal the number of
    distinct book values in the DB. A count mismatch means at least one
    side is wrong even if both sides individually round-trip."""
    with sqlite3.connect(DB_PATH) as conn:
        (db_count,) = conn.execute(
            "SELECT COUNT(DISTINCT book) FROM verses WHERE translation = 'BSB'"
        ).fetchone()
    assert len(BOOKS) == db_count == 66, (
        f"parser BOOKS has {len(BOOKS)}, DB has {db_count}, expected 66"
    )
