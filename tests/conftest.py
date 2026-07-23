"""Shared pytest fixtures.

`fixture_db` builds a tiny SQLite bible.db in a temp dir with just enough
verses to cover the lookup tests. This lets the reference/lookup tests run
without depending on the full ingested corpus.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_SEED_ROWS = [
    # John 3
    ("BSB", "John", 3, 16, "For God so loved the world..."),
    ("BSB", "John", 3, 17, "For God did not send his Son..."),
    # 1 Corinthians 13
    ("BSB", "1 Corinthians", 13, 4, "Love is patient, love is kind."),
    ("BSB", "1 Corinthians", 13, 5, "It is not rude..."),
    ("BSB", "1 Corinthians", 13, 6, "Love takes no pleasure in evil..."),
    ("BSB", "1 Corinthians", 13, 7, "It bears all things..."),
    # Psalms 23 (whole chapter, 6 verses — content shortened)
    ("BSB", "Psalms", 23, 1, "The Lord is my shepherd..."),
    ("BSB", "Psalms", 23, 2, "He makes me lie down..."),
    ("BSB", "Psalms", 23, 3, "He restores my soul..."),
    ("BSB", "Psalms", 23, 4, "Even though I walk..."),
    ("BSB", "Psalms", 23, 5, "You prepare a table..."),
    ("BSB", "Psalms", 23, 6, "Surely goodness and mercy..."),
    # Revelation 22
    ("BSB", "Revelation", 22, 21, "The grace of the Lord Jesus be with all."),
]


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bible.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE verses (
                translation TEXT NOT NULL,
                book        TEXT NOT NULL,
                chapter     INTEGER NOT NULL,
                verse       INTEGER NOT NULL,
                text        TEXT NOT NULL,
                PRIMARY KEY (translation, book, chapter, verse)
            )
            """
        )
        conn.executemany(
            "INSERT INTO verses VALUES (?, ?, ?, ?, ?)", _SEED_ROWS
        )
        conn.commit()
    return db_path


@pytest.fixture
def empty_db_path(tmp_path: Path) -> Path:
    """A path where no DB file exists — used to verify graceful degradation."""
    return tmp_path / "does_not_exist.db"
