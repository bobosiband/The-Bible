"""Download the Berean Standard Bible and load it into SQLite.

Source: https://bible.helloao.org  (public-domain BSB)
Run:    python -m src.ingest.bsb
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import requests

BSB_URL = "https://bible.helloao.org/api/BSB/complete.json"
TRANSLATION_ID = "BSB"

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "data" / "corpus"
RAW_PATH = CORPUS_DIR / "bsb_complete.json"
DB_PATH = CORPUS_DIR / "bible.db"
SOURCES_PATH = CORPUS_DIR / "SOURCES.md"


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
        """
    )


def load_into_sqlite(rows, db_path: Path) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        init_schema(conn)
        cur = conn.executemany(
            "INSERT OR REPLACE INTO verses "
            "(translation, book, chapter, verse, text) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM verses WHERE translation = ?",
            (TRANSLATION_ID,),
        ).fetchone()
        return count


def write_sources_md(meta: dict) -> None:
    """Record where each corpus file came from and under what license."""
    entry = (
        f"## {meta.get('shortName', TRANSLATION_ID)} — {meta.get('englishName', meta.get('name', ''))}\n"
        f"- Source: {BSB_URL}\n"
        f"- Publisher website: {meta.get('website', '')}\n"
        f"- License URL: {meta.get('licenseUrl', '')}\n"
        f"- License: Public domain\n"
        f"- SHA256 (upstream metadata): {meta.get('sha256', '')}\n"
    )
    header = "# Corpus sources\n\nEvery downloaded corpus file is recorded here with its origin and license.\n\n"
    if SOURCES_PATH.exists():
        existing = SOURCES_PATH.read_text()
        if f"## {meta.get('shortName', TRANSLATION_ID)}" in existing:
            return
        SOURCES_PATH.write_text(existing.rstrip() + "\n\n" + entry)
    else:
        SOURCES_PATH.write_text(header + entry)


def main() -> int:
    download(BSB_URL, RAW_PATH)
    raw = json.loads(RAW_PATH.read_text())
    expected = raw["translation"].get("totalNumberOfVerses")

    # Skip the DB load if we've already ingested the same number of verses.
    if DB_PATH.exists() and expected:
        with sqlite3.connect(DB_PATH) as conn:
            init_schema(conn)
            (have,) = conn.execute(
                "SELECT COUNT(*) FROM verses WHERE translation = ?",
                (TRANSLATION_ID,),
            ).fetchone()
        if have == expected:
            print(f"[skip] {DB_PATH.name} already has {have} BSB verses")
            write_sources_md(raw["translation"])
            return 0

    rows = list(iter_verses(raw))
    count = load_into_sqlite(rows, DB_PATH)
    write_sources_md(raw["translation"])
    print(f"[done] loaded {count} verses into {DB_PATH}")
    if expected and count != expected:
        print(
            f"[warn] verse count mismatch: got {count}, upstream reports {expected}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
