"""Download the Berean Standard Bible and load it into SQLite.

Source: https://bible.helloao.org  (public-domain BSB)
Run:    python -m src.ingest.bsb

Design notes:
- The loader asserts exact 66 / 1,189 / 31,086 counts after load. A
  truncated download or a silently-dropped verse must never be tolerated,
  because everything downstream — parser correctness, eval accuracy,
  citation checking — assumes the corpus text is exact.
- A `corpus_meta` table stores the locally-computed SHA256 of the raw
  file so any eval run can be pinned back to the exact corpus bytes.
- SOURCES.md is human-authored (see data/corpus/SOURCES.md); the loader
  intentionally does not write to it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

import requests

BSB_URL = "https://bible.helloao.org/api/BSB/complete.json"
TRANSLATION_ID = "BSB"

# The expected content counts for BSB. These are asserted after every load;
# a mismatch means either the download is incomplete or the extractor is
# silently dropping content — both are fatal for downstream accuracy.
EXPECTED_BOOKS = 66
EXPECTED_CHAPTERS = 1189
EXPECTED_VERSES = 31086

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "data" / "corpus"
RAW_PATH = CORPUS_DIR / "bsb_complete.json"
DB_PATH = CORPUS_DIR / "bible.db"


class CorpusIntegrityError(RuntimeError):
    """Raised when the loaded corpus does not match expected counts."""


def download(url: str, dest: Path) -> None:
    """Fetch `url` to `dest` unless `dest` already exists (idempotent)."""
    if dest.exists():
        print(f"[skip] {dest.name} already downloaded")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[get ] {url}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"[save] {dest} ({dest.stat().st_size // 1024} KiB)")


def sha256_file(path: Path) -> str:
    """SHA256 of a file's bytes on disk. Streams to avoid loading all in RAM."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verse_text(content: list) -> str:
    """Flatten a verse's `content` array into plain text.

    The BSB JSON mixes plain strings with objects that carry inline notation
    (footnote markers, line breaks, section headings). We keep the strings
    and pull `text` out of any object that has it; everything else is dropped.
    """
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return " ".join(p.strip() for p in parts if p.strip())


def iter_verses(raw: dict):
    """Yield (translation, book, chapter, verse, text) tuples from the JSON."""
    translation_id = raw["translation"]["id"]
    for book in raw["books"]:
        book_name = book.get("commonName") or book["name"]
        for chapter_entry in book["chapters"]:
            chapter_num = chapter_entry["chapter"]["number"]
            for node in chapter_entry["chapter"]["content"]:
                if isinstance(node, dict) and node.get("type") == "verse":
                    text = _verse_text(node.get("content", []))
                    if text:
                        yield (
                            translation_id,
                            book_name,
                            chapter_num,
                            node["number"],
                            text,
                        )


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS verses (
            translation TEXT NOT NULL,
            book        TEXT NOT NULL,
            chapter     INTEGER NOT NULL,
            verse       INTEGER NOT NULL,
            text        TEXT NOT NULL,
            PRIMARY KEY (translation, book, chapter, verse)
        );
        CREATE INDEX IF NOT EXISTS idx_verses_lookup
            ON verses (translation, book, chapter, verse);
        CREATE TABLE IF NOT EXISTS corpus_meta (
            translation      TEXT PRIMARY KEY,
            source_url       TEXT,
            retrieved_at     TEXT,
            sha256_local     TEXT,
            sha256_upstream  TEXT,
            book_count       INTEGER,
            chapter_count    INTEGER,
            verse_count      INTEGER
        );
        """
    )


def _count_rows(conn: sqlite3.Connection, translation: str) -> tuple[int, int, int]:
    """Return (books, chapters, verses) for a translation as counted from the
    loaded rows. Chapter and book counts are DISTINCT rather than SUM."""
    (verses,) = conn.execute(
        "SELECT COUNT(*) FROM verses WHERE translation = ?", (translation,)
    ).fetchone()
    (chapters,) = conn.execute(
        "SELECT COUNT(DISTINCT book || ':' || chapter) FROM verses "
        "WHERE translation = ?",
        (translation,),
    ).fetchone()
    (books,) = conn.execute(
        "SELECT COUNT(DISTINCT book) FROM verses WHERE translation = ?",
        (translation,),
    ).fetchone()
    return books, chapters, verses


def verify_counts(
    conn: sqlite3.Connection,
    translation: str = TRANSLATION_ID,
    expected_books: int = EXPECTED_BOOKS,
    expected_chapters: int = EXPECTED_CHAPTERS,
    expected_verses: int = EXPECTED_VERSES,
) -> tuple[int, int, int]:
    """Assert exact book/chapter/verse counts, else raise CorpusIntegrityError."""
    books, chapters, verses = _count_rows(conn, translation)
    problems = []
    if books != expected_books:
        problems.append(f"books: got {books}, expected {expected_books}")
    if chapters != expected_chapters:
        problems.append(f"chapters: got {chapters}, expected {expected_chapters}")
    if verses != expected_verses:
        problems.append(f"verses: got {verses}, expected {expected_verses}")
    if problems:
        raise CorpusIntegrityError(
            f"[{translation}] corpus counts do not match: " + "; ".join(problems)
        )
    return books, chapters, verses


def write_corpus_meta(
    conn: sqlite3.Connection,
    translation: str,
    source_url: str,
    retrieved_at: str,
    sha256_local: str,
    sha256_upstream: str | None,
    books: int,
    chapters: int,
    verses: int,
) -> None:
    """Upsert one row into corpus_meta so downstream tools can pin a run
    to the exact corpus bytes that produced it."""
    conn.execute(
        """
        INSERT INTO corpus_meta
            (translation, source_url, retrieved_at, sha256_local, sha256_upstream,
             book_count, chapter_count, verse_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(translation) DO UPDATE SET
            source_url       = excluded.source_url,
            retrieved_at     = excluded.retrieved_at,
            sha256_local     = excluded.sha256_local,
            sha256_upstream  = excluded.sha256_upstream,
            book_count       = excluded.book_count,
            chapter_count    = excluded.chapter_count,
            verse_count      = excluded.verse_count
        """,
        (translation, source_url, retrieved_at, sha256_local, sha256_upstream,
         books, chapters, verses),
    )


def load_from_raw(
    raw: dict,
    db_path: Path,
    source_url: str = BSB_URL,
    sha256_local: str = "",
    retrieved_at: str = "",
    expected_books: int = EXPECTED_BOOKS,
    expected_chapters: int = EXPECTED_CHAPTERS,
    expected_verses: int = EXPECTED_VERSES,
) -> tuple[int, int, int]:
    """Load an already-parsed raw dict into SQLite. Idempotent — re-running
    against a fully-loaded DB inserts nothing new and asserts counts.

    Returns (books, chapters, verses)."""
    translation_id = raw["translation"]["id"]
    sha_upstream = raw["translation"].get("sha256")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        init_schema(conn)
        _, _, existing = _count_rows(conn, translation_id)
        if existing != expected_verses:
            rows = list(iter_verses(raw))
            conn.executemany(
                "INSERT OR REPLACE INTO verses "
                "(translation, book, chapter, verse, text) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()

        books, chapters, verses = verify_counts(
            conn,
            translation_id,
            expected_books=expected_books,
            expected_chapters=expected_chapters,
            expected_verses=expected_verses,
        )
        write_corpus_meta(
            conn,
            translation=translation_id,
            source_url=source_url,
            retrieved_at=retrieved_at,
            sha256_local=sha256_local,
            sha256_upstream=sha_upstream,
            books=books,
            chapters=chapters,
            verses=verses,
        )
        conn.commit()
    return books, chapters, verses


def main() -> int:
    download(BSB_URL, RAW_PATH)
    raw = json.loads(RAW_PATH.read_text())
    sha_local = sha256_file(RAW_PATH)
    # Use the raw file's mtime as retrieval time (that's when the bytes
    # arrived; wall clock at *load* is later and less useful for provenance).
    retrieved_at = (
        dt.datetime.fromtimestamp(RAW_PATH.stat().st_mtime, tz=dt.timezone.utc)
        .isoformat(timespec="seconds")
    )
    books, chapters, verses = load_from_raw(
        raw, DB_PATH,
        source_url=BSB_URL,
        sha256_local=sha_local,
        retrieved_at=retrieved_at,
    )
    print(
        f"[done] {DB_PATH.name}: {books} books, {chapters} chapters, "
        f"{verses} verses (sha256={sha_local[:12]}…)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
