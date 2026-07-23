"""Tests for the scripture reference parser and verse lookup."""
from __future__ import annotations

import pytest

from src.corpus.references import (
    CorpusUnavailableError,
    Reference,
    get_range,
    get_verse,
    normalize_book,
    parse_references,
)


# ---------------------------------------------------------------------------
# Book name normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, canonical",
    [
        ("John", "John"),
        ("john", "John"),
        ("JOHN", "John"),
        ("Jn", "John"),
        ("Jhn", "John"),
        ("Ps", "Psalms"),
        ("Psalm", "Psalms"),
        ("Psalms", "Psalms"),
        ("psa", "Psalms"),
        ("1 Cor", "1 Corinthians"),
        ("1Cor", "1 Corinthians"),
        ("1  Cor", "1 Corinthians"),
        ("1 corinthians", "1 Corinthians"),
        ("Rev", "Revelation"),
        ("Revelations", "Revelation"),
        ("Song of Songs", "Song of Solomon"),
        ("SoS", "Song of Solomon"),
        ("Ps.", "Psalms"),
        ("Rev.", "Revelation"),
        ("1 Jn", "1 John"),
        ("3Jn", "3 John"),
    ],
)
def test_normalize_book_accepts_common_forms(raw, canonical):
    assert normalize_book(raw) == canonical


@pytest.mark.parametrize("raw", ["", "Hezekiah", "Foobar", "1 Nephi", "Book of Mormon"])
def test_normalize_book_returns_none_for_unknown(raw):
    assert normalize_book(raw) is None


# ---------------------------------------------------------------------------
# Reference parsing — happy path
# ---------------------------------------------------------------------------

def test_parse_single_verse():
    assert parse_references("John 3:16") == [Reference("John", 3, 16)]


def test_parse_verse_range():
    assert parse_references("1 Cor 13:4-7") == [
        Reference("1 Corinthians", 13, 4, 7)
    ]


def test_parse_whole_chapter():
    assert parse_references("Ps 23") == [Reference("Psalms", 23)]


def test_parse_end_of_bible():
    assert parse_references("Rev 22:21") == [Reference("Revelation", 22, 21)]


def test_parse_multiple_refs_in_one_string():
    text = "Compare John 3:16 with 1 Cor 13:4-7, and read Ps 23 alongside Rev 22:21."
    assert parse_references(text) == [
        Reference("John", 3, 16),
        Reference("1 Corinthians", 13, 4, 7),
        Reference("Psalms", 23),
        Reference("Revelation", 22, 21),
    ]


def test_parse_handles_dotted_abbreviations():
    assert parse_references("Rev. 22:21") == [Reference("Revelation", 22, 21)]
    assert parse_references("Ps. 23") == [Reference("Psalms", 23)]


def test_parse_handles_numbered_books_without_space():
    assert parse_references("1Cor 13:4") == [Reference("1 Corinthians", 13, 4)]
    assert parse_references("2Tim 3:16") == [Reference("2 Timothy", 3, 16)]


def test_parse_is_case_insensitive():
    assert parse_references("JOHN 3:16") == [Reference("John", 3, 16)]
    assert parse_references("john 3:16") == [Reference("John", 3, 16)]


def test_parse_tolerates_whitespace_around_colon_and_dash():
    assert parse_references("John 3 : 16") == [Reference("John", 3, 16)]
    assert parse_references("1 Cor 13:4 - 7") == [
        Reference("1 Corinthians", 13, 4, 7)
    ]


def test_parse_full_book_name_with_multiple_words():
    assert parse_references("Song of Solomon 2:1") == [
        Reference("Song of Solomon", 2, 1)
    ]


def test_parse_reference_at_sentence_boundaries():
    # The parser must not choke on trailing punctuation (period, comma).
    assert parse_references("...See John 3:16.") == [Reference("John", 3, 16)]
    assert parse_references("...See John 3:16, then move on.") == [
        Reference("John", 3, 16)
    ]


def test_parse_backward_range_drops_end():
    # "13:7-4" is nonsense; keep the start verse and drop the bad end.
    refs = parse_references("1 Cor 13:7-4")
    assert refs == [Reference("1 Corinthians", 13, 7)]


# ---------------------------------------------------------------------------
# Reference parsing — nothing to find
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "",
        "this string has no references at all",
        "John chapter three verse sixteen",  # spelled-out numbers, not digits
        "3:16",                               # no book
        "1 Corinthians",                     # no chapter
        "the number 3:16 stands alone",      # no valid book precedes
    ],
)
def test_parse_returns_empty_when_no_refs(text):
    assert parse_references(text) == []


def test_parse_ignores_unknown_book_names():
    # "Hezekiah" is not a canonical or abbreviated book of the Bible.
    assert parse_references("Hezekiah 1:1 is not a thing") == []


def test_parse_does_not_match_book_names_inside_other_words():
    # "Jobst" is a surname; the parser must not extract "Job 5" from it.
    assert parse_references("Jobst 5:00 met me for coffee") == []


def test_parse_handles_none_and_empty_gracefully():
    assert parse_references("") == []


# ---------------------------------------------------------------------------
# Lookups — get_verse
# ---------------------------------------------------------------------------

def test_get_verse_returns_text(fixture_db):
    assert get_verse("John", 3, 16, db_path=fixture_db).startswith(
        "For God so loved"
    )


def test_get_verse_accepts_abbreviated_book(fixture_db):
    assert get_verse("Jn", 3, 16, db_path=fixture_db) == get_verse(
        "John", 3, 16, db_path=fixture_db
    )
    assert get_verse("1 Cor", 13, 4, db_path=fixture_db).startswith(
        "Love is patient"
    )


def test_get_verse_returns_none_for_missing_verse(fixture_db):
    # John 3 exists in the fixture but only verses 16 and 17 are seeded.
    assert get_verse("John", 3, 99, db_path=fixture_db) is None


def test_get_verse_returns_none_for_unknown_book(fixture_db):
    assert get_verse("Hezekiah", 1, 1, db_path=fixture_db) is None


def test_get_verse_raises_when_db_missing(empty_db_path):
    """Stage 3 contract: a missing DB is a setup problem, not a lookup miss."""
    with pytest.raises(CorpusUnavailableError):
        get_verse("John", 3, 16, db_path=empty_db_path)


def test_get_verse_raises_when_verses_table_missing(broken_db_path):
    with pytest.raises(CorpusUnavailableError):
        get_verse("John", 3, 16, db_path=broken_db_path)


# ---------------------------------------------------------------------------
# Lookups — get_range
# ---------------------------------------------------------------------------

def test_get_range_returns_inclusive_verses(fixture_db):
    verses = get_range("1 Corinthians", 13, 4, 7, db_path=fixture_db)
    assert [v[0] for v in verses] == [4, 5, 6, 7]
    assert verses[0][1].startswith("Love is patient")


def test_get_range_with_only_start_returns_single_verse(fixture_db):
    verses = get_range("John", 3, 16, db_path=fixture_db)
    assert len(verses) == 1
    assert verses[0][0] == 16


def test_get_range_without_start_returns_whole_chapter(fixture_db):
    verses = get_range("Psalms", 23, db_path=fixture_db)
    assert [v[0] for v in verses] == [1, 2, 3, 4, 5, 6]


def test_get_range_backward_returns_empty(fixture_db):
    assert get_range("1 Corinthians", 13, 7, 4, db_path=fixture_db) == []


def test_get_range_for_unknown_book_returns_empty(fixture_db):
    assert get_range("Hezekiah", 1, db_path=fixture_db) == []


def test_get_range_for_missing_chapter_returns_empty(fixture_db):
    assert get_range("John", 99, db_path=fixture_db) == []


def test_get_range_raises_when_db_missing(empty_db_path):
    """Stage 3 contract: a missing DB is a setup problem, not a lookup miss."""
    with pytest.raises(CorpusUnavailableError):
        get_range("John", 3, 16, db_path=empty_db_path)


def test_get_range_raises_when_verses_table_missing(broken_db_path):
    with pytest.raises(CorpusUnavailableError):
        get_range("John", 3, 16, db_path=broken_db_path)
