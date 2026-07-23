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
from dataclasses import dataclass, field
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
    "1 Samuel":        ["1 Samuel", "1Samuel", "1 Sam", "1Sam", "1 Sa", "1Sa",
                         "I Samuel", "I Sam", "I Sa"],
    "2 Samuel":        ["2 Samuel", "2Samuel", "2 Sam", "2Sam", "2 Sa", "2Sa",
                         "II Samuel", "II Sam", "II Sa"],
    "1 Kings":         ["1 Kings", "1Kings", "1 Kgs", "1Kgs", "1 Ki", "1Ki",
                         "I Kings", "I Kgs", "I Ki"],
    "2 Kings":         ["2 Kings", "2Kings", "2 Kgs", "2Kgs", "2 Ki", "2Ki",
                         "II Kings", "II Kgs", "II Ki"],
    "1 Chronicles":    ["1 Chronicles", "1Chronicles", "1 Chron", "1Chron",
                         "1 Chr", "1Chr", "1 Ch", "1Ch",
                         "I Chronicles", "I Chron", "I Chr"],
    "2 Chronicles":    ["2 Chronicles", "2Chronicles", "2 Chron", "2Chron",
                         "2 Chr", "2Chr", "2 Ch", "2Ch",
                         "II Chronicles", "II Chron", "II Chr"],
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
    "1 Corinthians":   ["1 Corinthians", "1Corinthians", "1 Cor", "1Cor",
                         "1 Co", "1Co",
                         "I Corinthians", "I Cor", "I Co"],
    "2 Corinthians":   ["2 Corinthians", "2Corinthians", "2 Cor", "2Cor",
                         "2 Co", "2Co",
                         "II Corinthians", "II Cor", "II Co"],
    "Galatians":       ["Galatians", "Gal"],
    "Ephesians":       ["Ephesians", "Eph"],
    "Philippians":     ["Philippians", "Phil", "Php"],
    "Colossians":      ["Colossians", "Col"],
    "1 Thessalonians": ["1 Thessalonians", "1Thessalonians", "1 Thess", "1Thess",
                         "1 Thes", "1Thes", "1 Th", "1Th",
                         "I Thessalonians", "I Thess", "I Thes", "I Th"],
    "2 Thessalonians": ["2 Thessalonians", "2Thessalonians", "2 Thess", "2Thess",
                         "2 Thes", "2Thes", "2 Th", "2Th",
                         "II Thessalonians", "II Thess", "II Thes", "II Th"],
    "1 Timothy":       ["1 Timothy", "1Timothy", "1 Tim", "1Tim", "1 Ti", "1Ti",
                         "I Timothy", "I Tim", "I Ti"],
    "2 Timothy":       ["2 Timothy", "2Timothy", "2 Tim", "2Tim", "2 Ti", "2Ti",
                         "II Timothy", "II Tim", "II Ti"],
    "Titus":           ["Titus", "Tit"],
    "Philemon":        ["Philemon", "Phlm", "Phm", "Philem"],
    "Hebrews":         ["Hebrews", "Heb"],
    "James":           ["James", "Jas", "Jm"],
    "1 Peter":         ["1 Peter", "1Peter", "1 Pet", "1Pet", "1 Pt", "1Pt",
                         "1 Pe", "1Pe",
                         "I Peter", "I Pet", "I Pt", "I Pe"],
    "2 Peter":         ["2 Peter", "2Peter", "2 Pet", "2Pet", "2 Pt", "2Pt",
                         "2 Pe", "2Pe",
                         "II Peter", "II Pet", "II Pt", "II Pe"],
    "1 John":          ["1 John", "1John", "1 Jn", "1Jn", "1 Jhn", "1Jhn",
                         "I John", "I Jn", "I Jhn"],
    "2 John":          ["2 John", "2John", "2 Jn", "2Jn", "2 Jhn", "2Jhn",
                         "II John", "II Jn", "II Jhn"],
    "3 John":          ["3 John", "3John", "3 Jn", "3Jn", "3 Jhn", "3Jhn",
                         "III John", "IIIJohn", "III Jn", "IIIJn"],
    "Jude":            ["Jude", "Jud"],
    "Revelation":      ["Revelation", "Revelations", "Rev", "Rv", "Apoc"],
}


def _normalize(s: str) -> str:
    """Lowercase, drop periods, collapse whitespace — used for lookup only."""
    return " ".join(s.replace(".", " ").lower().split())


# Books with exactly one chapter. For these, "Jude 5" in common usage
# means Jude verse 5 (i.e. chapter 1 verse 5), not chapter 5. See
# PARSER_AUDIT.md rows 1-5 for the ruling.
SINGLE_CHAPTER_BOOKS = frozenset({
    "Jude", "Obadiah", "Philemon", "2 John", "3 John",
})

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
#   Book Chapter                             e.g. "Ps 23"
#   Book Chapter:Verse                       e.g. "John 3:16"
#   Book Chapter:StartVerse-EndVerse         e.g. "1 Cor 13:4-7"
#   Book Chapter:StartVerse-EndCh:EndVerse   e.g. "Genesis 1:1-2:3"
# The `end2` group is only present for cross-chapter ranges; when it is
# set the `end` group is the *chapter* half of the endpoint, and `end2`
# is the verse.
_REF_RE = re.compile(
    rf"\b(?P<book>{_book_alternation})"
    r"\s+(?P<chapter>\d{1,3})"
    r"(?:\s*:\s*(?P<verse>\d{1,3})"
    r"(?:\s*[-–]\s*(?P<end>\d{1,3})(?:\s*:\s*(?P<end2>\d{1,3}))?)?"
    r")?"
    r"\b",
    re.IGNORECASE,
)

# Comma-continuation after a base ref:
#   ", chapter:verse[-endchapter:endverse | -endverse]"
#   ", verse[-endverse]"   (bare number → inherits chapter)
# See PARSER_AUDIT.md rows 15-16 for the ruling.
_CONT_RE = re.compile(
    r"\s*,\s*(?P<c1>\d{1,3})"
    r"(?:\s*:\s*(?P<v1>\d{1,3}))?"
    r"(?:\s*[-–]\s*(?P<end>\d{1,3})(?:\s*:\s*(?P<end2>\d{1,3}))?)?"
)


@dataclass(frozen=True)
class Reference:
    book: str
    chapter: int
    verse: int | None = None
    end_verse: int | None = None
    # Cross-chapter ranges (Stage 3, audit rows 13-14): when set, the range
    # runs from (chapter, verse) through (end_chapter, end_verse) inclusive.
    end_chapter: int | None = None
    # Character offsets into the ORIGINAL input string of parse_references.
    # `compare=False` keeps equality by (book, chapter, verse, end_verse,
    # end_chapter) so two references to the same passage from different
    # positions in a text still compare equal.
    start: int | None = field(default=None, compare=False)
    end: int | None = field(default=None, compare=False)

    def __str__(self) -> str:
        if self.verse is None:
            return f"{self.book} {self.chapter}"
        if self.end_chapter is not None:
            return (
                f"{self.book} {self.chapter}:{self.verse}"
                f"-{self.end_chapter}:{self.end_verse}"
            )
        if self.end_verse is None:
            return f"{self.book} {self.chapter}:{self.verse}"
        return f"{self.book} {self.chapter}:{self.verse}-{self.end_verse}"


def parse_references(text: str, *, dedupe: bool = False) -> list[Reference]:
    """Extract all Scripture references from a block of free text.

    Returned references carry `start` and `end` character offsets into the
    ORIGINAL `text`. The offsets round-trip: `text[ref.start:ref.end]`
    reproduces the matched substring modulo `.` ↔ ` ` normalisation
    (see `_normalize`).

    `dedupe=True` drops later occurrences of the same reference (equality
    ignores spans, so "John 3:16 ... John 3:16" collapses to one entry),
    preserving first-occurrence order. Citation counting needs both
    behaviours: raw for "how many citations did the model make", deduped
    for "how many distinct passages did the model rely on".
    """
    # Never raise on a non-string input; a citation checker sweeping a
    # variety of upstream fields shouldn't have to guard every call.
    if not isinstance(text, str) or not text:
        return []
    # Periods can appear as abbreviation markers ("Ps." "Rev.") or as
    # sentence terminators next to a reference ("...John 3:16."). Turning
    # them into spaces normalizes both cases without shifting semantics.
    # Crucially, `.replace(".", " ")` preserves string length so the
    # regex match offsets are valid indices into the original text.
    cleaned = text.replace(".", " ")
    results: list[Reference] = []
    seen: set[Reference] = set()

    def _emit(ref: Reference) -> None:
        if dedupe:
            if ref in seen:
                return
            seen.add(ref)
        results.append(ref)

    pos = 0
    while pos < len(cleaned):
        m = _REF_RE.search(cleaned, pos)
        if not m:
            break
        book_key = _normalize(m.group("book"))
        canonical = _LOOKUP.get(book_key)
        if canonical is None:
            pos = m.end()
            continue
        chapter = int(m.group("chapter"))
        had_explicit_verse = m.group("verse") is not None
        verse = int(m.group("verse")) if had_explicit_verse else None
        end1 = int(m.group("end")) if m.group("end") else None
        end2 = int(m.group("end2")) if m.group("end2") else None
        if end2 is not None:
            # Cross-chapter range: "Book c1:v1-c2:v2" — end1 is c2, end2 is v2.
            end_chapter, end_verse = end1, end2
        else:
            end_chapter, end_verse = None, end1
        # Guard against nonsensical within-chapter ranges like "13:7-4".
        if (end_chapter is None and verse is not None
                and end_verse is not None and end_verse < verse):
            end_verse = None
        # Single-chapter books: "Jude 5" without a colon means Jude 1:5.
        # Only applies when the writer wrote no verse info at all.
        if (canonical in SINGLE_CHAPTER_BOOKS
                and verse is None and end_verse is None and end_chapter is None):
            chapter, verse = 1, chapter
        _emit(Reference(
            canonical, chapter, verse, end_verse,
            end_chapter=end_chapter,
            start=m.start(), end=m.end(),
        ))
        last_end = m.end()

        # Comma continuations. Only apply after a ref that carried an
        # explicit verse — "Ps 23, 24" is too ambiguous to guess at.
        # Continuations inherit the book. If the continuation has its
        # own chapter (comma-c:v) it uses that; if it's bare (comma-v)
        # it inherits the previous ref's chapter.
        cont_chapter = chapter
        if had_explicit_verse:
            while True:
                cm = _CONT_RE.match(cleaned, last_end)
                if not cm:
                    break
                c1 = int(cm.group("c1"))
                v1 = int(cm.group("v1")) if cm.group("v1") else None
                cend1 = int(cm.group("end")) if cm.group("end") else None
                cend2 = int(cm.group("end2")) if cm.group("end2") else None
                if v1 is not None:
                    # "c:v" form → new chapter+verse
                    cur_chapter, cur_verse = c1, v1
                else:
                    # bare "v" form → inherit chapter, use c1 as verse
                    cur_chapter, cur_verse = cont_chapter, c1
                if cend2 is not None:
                    cur_end_chapter, cur_end_verse = cend1, cend2
                else:
                    cur_end_chapter, cur_end_verse = None, cend1
                if (cur_end_chapter is None and cur_end_verse is not None
                        and cur_end_verse < cur_verse):
                    cur_end_verse = None
                _emit(Reference(
                    canonical, cur_chapter, cur_verse, cur_end_verse,
                    end_chapter=cur_end_chapter,
                    start=cm.start(), end=cm.end(),
                ))
                cont_chapter = cur_chapter
                last_end = cm.end()

        pos = last_end
    return results


def normalize_book(name: str) -> str | None:
    """Return the canonical book name for any accepted form, or None."""
    return _LOOKUP.get(_normalize(name))


class CorpusUnavailableError(RuntimeError):
    """The corpus DB is missing or unusable — distinct from 'verse not found'.

    A citation checker asking about John 3:16 on a machine with no ingested
    corpus needs to know 'I can't tell' (raise this) rather than 'not
    present' (return None), because those two outcomes drive completely
    different behaviour: one is a setup error, the other is a real finding.
    """


def _connect(db_path: Path | str | None) -> sqlite3.Connection:
    """Open the corpus DB. Raises CorpusUnavailableError if the file is
    absent or has no `verses` table. Callers deliberately do NOT catch
    this — the whole point of raising is to prevent a missing DB from
    masquerading as a legitimate 'verse not found' result."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB
    if not path.exists():
        raise CorpusUnavailableError(
            f"corpus DB not found at {path}. "
            f"Run `python -m src.ingest.bsb` to build it."
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        has_verses = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='verses'"
        ).fetchone()
    except sqlite3.DatabaseError as e:
        conn.close()
        raise CorpusUnavailableError(f"{path} is not a readable SQLite DB: {e}")
    if not has_verses:
        conn.close()
        raise CorpusUnavailableError(
            f"{path} exists but has no `verses` table. Re-run the ingest."
        )
    return conn


def get_verse(
    book: str,
    chapter: int,
    verse: int,
    translation: str = DEFAULT_TRANSLATION,
    db_path: Path | str | None = None,
) -> str | None:
    """Return a single verse's text, or None if not present.

    Raises CorpusUnavailableError if the DB is missing — that state is
    deliberately not conflated with a failed lookup.
    """
    canonical = normalize_book(book)
    if canonical is None:
        return None
    conn = _connect(db_path)
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
    end_chapter: int | None = None,
) -> list[tuple[int, str]] | list[tuple[int, int, str]]:
    """Return an inclusive range of verses.

    Return shape:
    - When `end_chapter` is None: `list[tuple[verse_number, text]]` (backward
      compatible with pre-Stage-3 callers).
    - When `end_chapter` is set: `list[tuple[chapter, verse_number, text]]`
      so callers can distinguish which chapter each verse came from.

    - start=None returns the whole chapter (only valid when end_chapter is
      also None).
    - end=None means "just the single verse `start`" for within-chapter,
      or "through end_chapter:last_verse" for cross-chapter — callers pass
      an explicit end verse for the latter case.
    - Nonexistent refs return an empty list rather than raising.
    - A missing DB raises CorpusUnavailableError (see class docstring).
    """
    canonical = normalize_book(book)
    if canonical is None:
        return []
    conn = _connect(db_path)
    try:
        if end_chapter is not None:
            # Cross-chapter: fetch every verse from (chapter, start) through
            # (end_chapter, end). We use a single query with a lexicographic
            # inequality on (chapter, verse) to keep it simple and ordered.
            if start is None or end is None:
                return []
            rows = conn.execute(
                "SELECT chapter, verse, text FROM verses "
                "WHERE translation = ? AND book = ? "
                "AND (chapter, verse) BETWEEN (?, ?) AND (?, ?) "
                "ORDER BY chapter, verse",
                (translation, canonical, chapter, start, end_chapter, end),
            ).fetchall()
            return [(r["chapter"], r["verse"], r["text"]) for r in rows]
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
