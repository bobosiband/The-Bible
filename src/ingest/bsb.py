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
import re
import sqlite3
import sys
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


class LoaderChangedError(RuntimeError):
    """Raised when the on-disk loader's SHA256 differs from the value that
    produced the currently-loaded rows, and --force was not supplied.

    This is the guard against the Stage 2 failure mode: a loader fix (e.g.
    the closing-quote spacing patch) that would silently leave old, wrong
    text in the DB because the row count still matches expected.
    """


def _loader_version() -> str:
    """SHA256 of this module's source file. Kept as a function (not a
    module-level constant) so tests can monkeypatch it to simulate a
    loader change without editing the source."""
    return sha256_file(Path(__file__))


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


# Characters that must not be preceded by whitespace when they appear at
# the start of a following segment: sentence terminators, closing brackets,
# and closing curly quotes. Straight quotes are ambiguous (open vs close)
# so are NOT included — the BSB uses curly quotes consistently.
_ATTACHING_PUNCT = re.compile(r"\s+(?=[,.;:?!)\]}”’])")


def _verse_text(content: list) -> str:
    """Flatten a verse's `content` array into plain text.

    The BSB JSON mixes plain strings with objects that carry inline notation
    (`{noteId}`, `{lineBreak}`, section headings). We keep the strings and
    pull `text` out of any object that has it; everything else is dropped.

    Segments are joined with a single space, but when the next segment
    starts with attaching punctuation — most commonly a closing curly quote
    that lives in its own segment because a footnote sat between it and its
    sentence — that space is collapsed back out so the quote attaches to
    the sentence, e.g. "born again.”" not "born again. ”".
    """
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            s = item.strip()
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            s = item["text"].strip()
        else:
            continue
        if s:
            parts.append(s)
    return _ATTACHING_PUNCT.sub("", " ".join(parts))


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
    # Additive migration for older DBs: loader_version was added in Stage 3.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(corpus_meta)")}
    if "loader_version" not in cols:
        conn.execute("ALTER TABLE corpus_meta ADD COLUMN loader_version TEXT")


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
    loader_version: str,
) -> None:
    """Upsert one row into corpus_meta so downstream tools can pin a run
    to the exact corpus bytes AND the exact loader logic that produced it."""
    conn.execute(
        """
        INSERT INTO corpus_meta
            (translation, source_url, retrieved_at, sha256_local, sha256_upstream,
             book_count, chapter_count, verse_count, loader_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(translation) DO UPDATE SET
            source_url       = excluded.source_url,
            retrieved_at     = excluded.retrieved_at,
            sha256_local     = excluded.sha256_local,
            sha256_upstream  = excluded.sha256_upstream,
            book_count       = excluded.book_count,
            chapter_count    = excluded.chapter_count,
            verse_count      = excluded.verse_count,
            loader_version   = excluded.loader_version
        """,
        (translation, source_url, retrieved_at, sha256_local, sha256_upstream,
         books, chapters, verses, loader_version),
    )


def _stored_loader_version(conn: sqlite3.Connection, translation: str) -> str | None:
    row = conn.execute(
        "SELECT loader_version FROM corpus_meta WHERE translation = ?",
        (translation,),
    ).fetchone()
    return row[0] if row and row[0] else None


def load_from_raw(
    raw: dict,
    db_path: Path,
    source_url: str = BSB_URL,
    sha256_local: str = "",
    retrieved_at: str = "",
    expected_books: int = EXPECTED_BOOKS,
    expected_chapters: int = EXPECTED_CHAPTERS,
    expected_verses: int = EXPECTED_VERSES,
    force: bool = False,
) -> tuple[int, int, int]:
    """Load an already-parsed raw dict into SQLite. Idempotent — re-running
    against a fully-loaded DB inserts nothing new and asserts counts.

    If `force=True`, wipes every existing row for the translation and
    re-extracts from `raw`. Use this when the extractor changed and the
    DB's stored text is stale even though the row count still matches.

    If the loader source has changed since the last load and `force=False`,
    raises LoaderChangedError with instructions. This closes the Stage 2
    failure mode where a fix in `_verse_text` had no effect on a full DB.

    Returns (books, chapters, verses)."""
    translation_id = raw["translation"]["id"]
    sha_upstream = raw["translation"].get("sha256")
    current_loader = _loader_version()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        init_schema(conn)
        _, _, existing = _count_rows(conn, translation_id)
        stored_loader = _stored_loader_version(conn, translation_id)

        loader_changed = (
            existing > 0
            and stored_loader is not None
            and stored_loader != current_loader
        )
        if loader_changed and not force:
            raise LoaderChangedError(
                f"loader source has changed since the current corpus was loaded.\n"
                f"  stored loader_version: {stored_loader}\n"
                f"  current loader_version: {current_loader}\n"
                f"Re-run with --force (or force=True) to wipe and re-extract; "
                f"the extractor may produce different text and the DB's stored "
                f"text is stale until you do."
            )

        if force:
            conn.execute(
                "DELETE FROM verses WHERE translation = ?", (translation_id,)
            )
            existing = 0

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
            loader_version=current_loader,
        )
        conn.commit()
    return books, chapters, verses


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--force", action="store_true",
        help="Wipe existing rows and re-extract from the raw file. Required "
             "after a loader change; otherwise refused if a mismatch is detected.",
    )
    args = p.parse_args(argv)

    download(BSB_URL, RAW_PATH)
    raw = json.loads(RAW_PATH.read_text())
    sha_local = sha256_file(RAW_PATH)
    retrieved_at = (
        dt.datetime.fromtimestamp(RAW_PATH.stat().st_mtime, tz=dt.timezone.utc)
        .isoformat(timespec="seconds")
    )
    try:
        books, chapters, verses = load_from_raw(
            raw, DB_PATH,
            source_url=BSB_URL,
            sha256_local=sha_local,
            retrieved_at=retrieved_at,
            force=args.force,
        )
    except LoaderChangedError as e:
        print(f"[refuse] {e}", file=sys.stderr)
        return 2
    print(
        f"[done] {DB_PATH.name}: {books} books, {chapters} chapters, "
        f"{verses} verses (sha256={sha_local[:12]}…)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
