"""Parse Scripture references from free text and look verses up.

Public API:
    parse_references(text) -> list[Reference]
    get_verse(book, chapter, verse, translation="BSB", db_path=None) -> str | None
    get_range(book, chapter, start, end=None, translation="BSB", db_path=None)
        -> list[tuple[int, str]]
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "corpus" / "bible.db"
DEFAULT_TRANSLATION = "BSB"

# Canonical book name (as stored in the DB) → accepted forms.
# Forms are matched case-insensitively; periods in the input are treated
# as whitespace before matching, so "Ps." and "Ps" both work.
BOOKS: dict[str, list[str]] = {
    # --- Old Testament ---
    "Genesis":         ["Genesis", "Gen", "Gn"],
    "Exodus":          ["Exodus", "Exod", "Exo", "Ex"],
    "Leviticus":       ["Leviticus", "Lev", "Lv"],
    "Numbers":         ["Numbers", "Num", "Nm", "Nu"],
    "Deuteronomy":     ["Deuteronomy", "Deut", "Dt"],
    "Joshua":          ["Joshua", "Josh", "Jos"],
    "Judges":          ["Judges", "Judg", "Jdg", "Jgs"],
    "Ruth":            ["Ruth", "Rth", "Ru"],
    "1 Samuel":        ["1 Samuel", "1Samuel", "1 Sam", "1Sam", "1 Sa", "1Sa"],
    "2 Samuel":        ["2 Samuel", "2Samuel", "2 Sam", "2Sam", "2 Sa", "2Sa"],
    "1 Kings":         ["1 Kings", "1Kings", "1 Kgs", "1Kgs", "1 Ki", "1Ki"],
    "2 Kings":         ["2 Kings", "2Kings", "2 Kgs", "2Kgs", "2 Ki", "2Ki"],
    "1 Chronicles":    ["1 Chronicles", "1Chronicles", "1 Chron", "1Chron", "1 Chr", "1Chr", "1 Ch", "1Ch"],
    "2 Chronicles":    ["2 Chronicles", "2Chronicles", "2 Chron", "2Chron", "2 Chr", "2Chr", "2 Ch", "2Ch"],
    "Ezra":            ["Ezra", "Ezr"],
    "Nehemiah":        ["Nehemiah", "Neh", "Ne"],
    "Esther":          ["Esther", "Esth", "Est"],
    "Job":             ["Job", "Jb"],
    "Psalms":          ["Psalms", "Psalm", "Psa", "Pss", "Ps"],
    "Proverbs":        ["Proverbs", "Prov", "Prv", "Pr"],
    "Ecclesiastes":    ["Ecclesiastes", "Eccles", "Eccl", "Ecc", "Qoh"],
    "Song of Solomon": ["Song of Solomon", "Song of Songs", "Song", "SoS", "Canticles", "Cant"],
    "Isaiah":          ["Isaiah", "Isa", "Is"],
    "Jeremiah":        ["Jeremiah", "Jer", "Jr"],
    "Lamentations":    ["Lamentations", "Lam", "La"],
    "Ezekiel":         ["Ezekiel", "Ezek", "Ezk", "Eze"],
    "Daniel":          ["Daniel", "Dan", "Dn"],
    "Hosea":           ["Hosea", "Hos", "Ho"],
    "Joel":            ["Joel", "Joe", "Jl"],
    "Amos":            ["Amos", "Amo", "Am"],
    "Obadiah":         ["Obadiah", "Obad", "Oba", "Ob"],
    "Jonah":           ["Jonah", "Jon", "Jnh"],
    "Micah":           ["Micah", "Mic", "Mi"],
    "Nahum":           ["Nahum", "Nah", "Na"],
    "Habakkuk":        ["Habakkuk", "Hab", "Hbk"],
    "Zephaniah":       ["Zephaniah", "Zeph", "Zep"],
    "Haggai":          ["Haggai", "Hag", "Hg"],
    "Zechariah":       ["Zechariah", "Zech", "Zec"],
    "Malachi":         ["Malachi", "Mal", "Ml"],
    # --- New Testament ---
    "Matthew":         ["Matthew", "Matt", "Mt"],
    "Mark":            ["Mark", "Mrk", "Mk"],
    "Luke":            ["Luke", "Luk", "Lk"],
    "John":            ["John", "Jhn", "Jn"],
    "Acts":            ["Acts", "Act", "Ac"],
    "Romans":          ["Romans", "Rom", "Rm", "Ro"],
    "1 Corinthians":   ["1 Corinthians", "1Corinthians", "1 Cor", "1Cor", "1 Co", "1Co"],
    "2 Corinthians":   ["2 Corinthians", "2Corinthians", "2 Cor", "2Cor", "2 Co", "2Co"],
    "Galatians":       ["Galatians", "Gal"],
    "Ephesians":       ["Ephesians", "Eph"],
    "Philippians":     ["Philippians", "Phil", "Php"],
    "Colossians":      ["Colossians", "Col"],
    "1 Thessalonians": ["1 Thessalonians", "1Thessalonians", "1 Thess", "1Thess", "1 Thes", "1Thes", "1 Th", "1Th"],
    "2 Thessalonians": ["2 Thessalonians", "2Thessalonians", "2 Thess", "2Thess", "2 Thes", "2Thes", "2 Th", "2Th"],
    "1 Timothy":       ["1 Timothy", "1Timothy", "1 Tim", "1Tim", "1 Ti", "1Ti"],
    "2 Timothy":       ["2 Timothy", "2Timothy", "2 Tim", "2Tim", "2 Ti", "2Ti"],
    "Titus":           ["Titus", "Tit"],
    "Philemon":        ["Philemon", "Phlm", "Phm", "Philem"],
    "Hebrews":         ["Hebrews", "Heb"],
    "James":           ["James", "Jas", "Jm"],
    "1 Peter":         ["1 Peter", "1Peter", "1 Pet", "1Pet", "1 Pt", "1Pt", "1 Pe", "1Pe"],
    "2 Peter":         ["2 Peter", "2Peter", "2 Pet", "2Pet", "2 Pt", "2Pt", "2 Pe", "2Pe"],
    "1 John":          ["1 John", "1John", "1 Jn", "1Jn", "1 Jhn", "1Jhn"],
    "2 John":          ["2 John", "2John", "2 Jn", "2Jn", "2 Jhn", "2Jhn"],
    "3 John":          ["3 John", "3John", "3 Jn", "3Jn", "3 Jhn", "3Jhn"],
    "Jude":            ["Jude", "Jud"],
    "Revelation":      ["Revelation", "Revelations", "Rev", "Rv", "Apoc"],
}


def _normalize(s: str) -> str:
    """Lowercase, drop periods, collapse whitespace — used for lookup only."""
    return " ".join(s.replace(".", " ").lower().split())


# Reverse map: every accepted form (normalized) → canonical name.
_LOOKUP: dict[str, str] = {}
for canonical, forms in BOOKS.items():
    for form in forms:
        _LOOKUP[_normalize(form)] = canonical

# Build the regex once. Sort longest-first so "Philippians" beats "Phil",
# "1 Corinthians" beats "1 Cor", etc. Each form's internal whitespace is
# rewritten to `\s*` so "1 Cor", "1Cor", and "1  Cor" all match.
_book_alternation = "|".join(
    re.escape(form).replace(r"\ ", r"\s*")
    for form in sorted(_LOOKUP, key=len, reverse=True)
)

# Reference syntax:
#   Book Chapter                        e.g. "Ps 23"
#   Book Chapter:Verse                  e.g. "John 3:16"
#   Book Chapter:StartVerse-EndVerse    e.g. "1 Cor 13:4-7"
_REF_RE = re.compile(
    rf"\b(?P<book>{_book_alternation})"
    r"\s+(?P<chapter>\d{1,3})"
    r"(?:\s*:\s*(?P<verse>\d{1,3})(?:\s*[-–]\s*(?P<end>\d{1,3}))?)?"
    r"\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Reference:
    book: str
    chapter: int
    verse: int | None = None
    end_verse: int | None = None

    def __str__(self) -> str:
        if self.verse is None:
            return f"{self.book} {self.chapter}"
        if self.end_verse is None:
            return f"{self.book} {self.chapter}:{self.verse}"
        return f"{self.book} {self.chapter}:{self.verse}-{self.end_verse}"


def parse_references(text: str) -> list[Reference]:
    """Extract all Scripture references from a block of free text."""
    if not text:
        return []
    # Periods can appear as abbreviation markers ("Ps." "Rev.") or as
    # sentence terminators next to a reference ("...John 3:16."). Turning
    # them into spaces normalizes both cases without shifting semantics.
    cleaned = text.replace(".", " ")
    results: list[Reference] = []
    for m in _REF_RE.finditer(cleaned):
        book_key = _normalize(m.group("book"))
        canonical = _LOOKUP.get(book_key)
        if canonical is None:
            continue
        chapter = int(m.group("chapter"))
        verse = int(m.group("verse")) if m.group("verse") else None
        end = int(m.group("end")) if m.group("end") else None
        # Guard against nonsensical ranges like "13:7-4".
        if verse is not None and end is not None and end < verse:
            end = None
        results.append(Reference(canonical, chapter, verse, end))
    return results


def normalize_book(name: str) -> str | None:
    """Return the canonical book name for any accepted form, or None."""
    return _LOOKUP.get(_normalize(name))


def _connect(db_path: Path | str | None) -> sqlite3.Connection | None:
    """Open the corpus DB. Returns None if the file doesn't exist yet —
    callers treat that as 'no data' rather than raising, so tests and
    dry-runs don't have to guard the call site."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def get_verse(
    book: str,
    chapter: int,
    verse: int,
    translation: str = DEFAULT_TRANSLATION,
    db_path: Path | str | None = None,
) -> str | None:
    """Return a single verse's text, or None if not present."""
    canonical = normalize_book(book)
    if canonical is None:
        return None
    conn = _connect(db_path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT text FROM verses "
            "WHERE translation = ? AND book = ? AND chapter = ? AND verse = ?",
            (translation, canonical, chapter, verse),
        ).fetchone()
    finally:
        conn.close()
    return row["text"] if row else None


def get_range(
    book: str,
    chapter: int,
    start: int | None = None,
    end: int | None = None,
    translation: str = DEFAULT_TRANSLATION,
    db_path: Path | str | None = None,
) -> list[tuple[int, str]]:
    """Return an inclusive range of verses as (verse_number, text) tuples.

    - start=None returns the whole chapter.
    - end=None means "just the single verse `start`".
    - Nonexistent refs return an empty list rather than raising.
    """
    canonical = normalize_book(book)
    if canonical is None:
        return []
    conn = _connect(db_path)
    if conn is None:
        return []
    try:
        if start is None:
            rows = conn.execute(
                "SELECT verse, text FROM verses "
                "WHERE translation = ? AND book = ? AND chapter = ? "
                "ORDER BY verse",
                (translation, canonical, chapter),
            ).fetchall()
        else:
            stop = end if end is not None else start
            if stop < start:
                return []
            rows = conn.execute(
                "SELECT verse, text FROM verses "
                "WHERE translation = ? AND book = ? AND chapter = ? "
                "AND verse BETWEEN ? AND ? "
                "ORDER BY verse",
                (translation, canonical, chapter, start, stop),
            ).fetchall()
    finally:
        conn.close()
    return [(r["verse"], r["text"]) for r in rows]
