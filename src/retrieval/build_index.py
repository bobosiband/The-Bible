"""Build the FTS5 lexical index over the ingested verses table.

Run:
    python -m src.retrieval.build_index

The index is pinned to two fingerprints, both stored in a companion
`retrieval_index_meta` table:

- `corpus_sha256`: copied from `corpus_meta.sha256_local` at build time.
  If the corpus is re-ingested to different bytes, `src.retrieval.query`
  refuses to query the stale index (`IndexOutOfSyncError`).
- `index_version`: SHA256 of this build script. If the build logic
  changes (tokenizer, populated columns) the version fingerprint changes
  and downstream callers can distinguish an old index from a fresh one.

The index is always rebuilt from scratch (DROP + CREATE + INSERT) —
incremental maintenance would be nicer but harder to trust, and 31,086
verses inserts in well under a second on the target hardware.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "corpus" / "bible.db"
DEFAULT_TRANSLATION = "BSB"


class BuildIndexError(RuntimeError):
    """Raised when the index cannot be built (no corpus, no rows,
    no corpus_meta sha to pin against). Distinct so the CLI can exit
    with a clear message."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def index_version() -> str:
    """SHA256 of this file's bytes. Kept as a function (not a module-level
    constant) so tests can monkeypatch it and simulate a version change
    without editing the source."""
    return _sha256_file(Path(__file__))


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS retrieval_index_meta (
            translation   TEXT PRIMARY KEY,
            index_version TEXT NOT NULL,
            corpus_sha256 TEXT NOT NULL,
            built_at      TEXT NOT NULL
        )
        """
    )


def build_index(
    db_path: Path = DEFAULT_DB,
    translation: str = DEFAULT_TRANSLATION,
) -> tuple[int, str]:
    """Rebuild the FTS5 index over the verses table.

    Returns (row_count, corpus_sha256). Raises BuildIndexError if the
    corpus is missing or the corpus_meta sha is absent — we refuse to
    build an index we can't pin to specific corpus bytes.
    """
    if not db_path.exists():
        raise BuildIndexError(
            f"corpus DB not found at {db_path}. "
            f"Run `python -m src.ingest.bsb` first."
        )
    with sqlite3.connect(db_path) as conn:
        has_verses = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='verses'"
        ).fetchone()
        if not has_verses:
            raise BuildIndexError(
                f"{db_path} has no `verses` table. Re-run the ingest."
            )
        (n_verses,) = conn.execute(
            "SELECT COUNT(*) FROM verses WHERE translation = ?",
            (translation,),
        ).fetchone()
        if n_verses == 0:
            raise BuildIndexError(
                f"no verse rows for translation={translation!r}; refusing to "
                f"build an empty index"
            )
        # Pin to the current corpus bytes. Without this, a re-ingest that
        # changes text would silently invalidate the index.
        has_meta = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='corpus_meta'"
        ).fetchone()
        row = None
        if has_meta:
            row = conn.execute(
                "SELECT sha256_local FROM corpus_meta WHERE translation = ?",
                (translation,),
            ).fetchone()
        if not row or not row[0]:
            raise BuildIndexError(
                f"corpus_meta.sha256_local is missing for translation="
                f"{translation!r} — cannot pin the index to specific corpus "
                f"bytes. Re-run the ingest."
            )
        corpus_sha = row[0]

        # Rebuild from scratch. The book/chapter/verse columns are
        # UNINDEXED — we want the FTS index to cover only `text`; the
        # metadata columns are stored for direct return from a MATCH
        # query without a join back to `verses`.
        conn.execute("DROP TABLE IF EXISTS verses_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE verses_fts USING fts5(
                translation UNINDEXED,
                book        UNINDEXED,
                chapter     UNINDEXED,
                verse       UNINDEXED,
                text,
                tokenize = 'porter unicode61 remove_diacritics 2'
            )
            """
        )
        conn.execute(
            "INSERT INTO verses_fts (translation, book, chapter, verse, text) "
            "SELECT translation, book, chapter, verse, text FROM verses "
            "WHERE translation = ?",
            (translation,),
        )
        _ensure_meta_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO retrieval_index_meta "
            "(translation, index_version, corpus_sha256, built_at) "
            "VALUES (?, ?, ?, ?)",
            (
                translation,
                index_version(),
                corpus_sha,
                dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    return n_verses, corpus_sha


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Build the FTS5 lexical index over the corpus."
    )
    p.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    p.add_argument("--translation", default=DEFAULT_TRANSLATION)
    args = p.parse_args(argv)

    try:
        n, sha = build_index(db_path=args.db_path, translation=args.translation)
    except BuildIndexError as e:
        print(f"[refuse] {e}", file=sys.stderr)
        return 2
    print(
        f"[done] verses_fts: {n} rows indexed for {args.translation} "
        f"(corpus_sha={sha[:12]}…, index_version={index_version()[:12]}…)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
