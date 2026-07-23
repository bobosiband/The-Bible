"""Corpus text fidelity tests.

Two flavours:
- Fixture-level: assert `_verse_text` handles concrete tricky content arrays
  (footnote mid-verse, inline lineBreak, chapter-level headings and Hebrew
  subtitles). These are pure unit tests and always run.
- Whole-corpus: sweep every row in `data/corpus/bible.db` and assert
  invariants (no empty text, no stringified dicts, no leading/trailing
  whitespace, no double spaces). Skipped if the DB isn't present.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.ingest.bsb import _verse_text, iter_verses

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "corpus" / "bible.db"


# ---------------------------------------------------------------------------
# Unit-level: verse-content flattening
# ---------------------------------------------------------------------------

def test_footnote_marker_between_text_segments_joins_with_single_space():
    """Gen 1:3-shape: two text segments separated by a footnote marker."""
    content = [
        "And God said, “Let there be light,”",
        {"noteId": 0},
        "and there was light.",
    ]
    out = _verse_text(content)
    assert out == (
        "And God said, “Let there be light,” "
        "and there was light."
    )
    assert "noteId" not in out
    assert "[object" not in out
    assert "{'" not in out


def test_inline_linebreak_between_segments_joins_cleanly():
    """Gen 1:5-shape: a lineBreak dict between text segments must be dropped
    and the segments joined with a single space."""
    content = [
        "God called the light “day,” and the darkness He called “night.”",
        {"lineBreak": True},
        "And there was evening, and there was morning—the first day.",
        {"noteId": 1},
    ]
    out = _verse_text(content)
    assert out.startswith("God called the light")
    assert out.endswith("the first day.")
    assert "lineBreak" not in out
    assert "noteId" not in out
    # Exactly one space at the segment join.
    assert "night.” And there" in out


def test_closing_curly_quote_after_footnote_attaches_without_space():
    """John 3:3-shape: closing curly quote in its own segment must not have
    a stray space in front of it. This was a real bug in the initial loader."""
    content = [
        "Jesus replied, “Truly, truly, I tell you, no one can see "
        "the kingdom of God unless he is born again.",
        {"noteId": 13},
        "”",
    ]
    out = _verse_text(content)
    assert out.endswith("born again.”")
    assert " ”" not in out


def test_object_text_segments_are_preserved():
    """Poetry / red-letter verses (Matt 5:3-shape) store lines as
    {text, poem, wordsOfJesus?} objects. All `text` fields must be included."""
    content = [
        {"text": "“Blessed are the poor in spirit,", "poem": 1},
        {"text": "for theirs is the kingdom of heaven.", "poem": 2},
    ]
    out = _verse_text(content)
    assert "Blessed are the poor in spirit" in out
    assert "for theirs is the kingdom of heaven." in out


def test_leading_and_trailing_whitespace_in_segments_is_normalised():
    content = ["  hello,  ", "  world  "]
    out = _verse_text(content)
    assert out == "hello, world"
    assert not out.startswith(" ")
    assert not out.endswith(" ")


def test_empty_content_returns_empty_string():
    assert _verse_text([]) == ""
    assert _verse_text([{"noteId": 3}, {"lineBreak": True}]) == ""


def test_headings_and_hebrew_subtitles_are_not_treated_as_verses():
    """Ps 49-shape: chapter content mixes headings, hebrew_subtitle,
    line_break, and verse nodes at the top level. iter_verses must only
    yield the verses — never a heading or subtitle as if it were verse 1."""
    raw = {
        "translation": {"id": "TEST"},
        "books": [{
            "id": "PSA", "name": "Psalms", "commonName": "Psalms",
            "chapters": [{
                "chapter": {
                    "number": 49,
                    "content": [
                        {"type": "heading", "content": ["The Evanescence of Wealth"]},
                        {"type": "hebrew_subtitle",
                         "content": ["For the choirmaster. A Psalm of the sons of Korah."]},
                        {"type": "line_break"},
                        {"type": "verse", "number": 1, "content": [
                            {"text": "Hear this, all you peoples;", "poem": 1},
                            {"text": "listen, all inhabitants of the world,", "poem": 2},
                        ]},
                        {"type": "verse", "number": 2, "content": [
                            {"text": "both low and high,", "poem": 1},
                            {"text": "rich and poor alike.", "poem": 2},
                        ]},
                    ],
                }
            }]
        }]
    }
    rows = list(iter_verses(raw))
    assert len(rows) == 2
    _, _, _, v1_num, v1_text = rows[0]
    assert v1_num == 1
    assert v1_text.startswith("Hear this")
    assert "choirmaster" not in v1_text
    assert "Evanescence" not in v1_text


# ---------------------------------------------------------------------------
# Whole-corpus sweep against the real bible.db
# ---------------------------------------------------------------------------

corpus_only = pytest.mark.skipif(
    not DB_PATH.exists(),
    reason="requires data/corpus/bible.db (run: python -m src.ingest.bsb)",
)


@pytest.fixture(scope="module")
def corpus_conn():
    if not DB_PATH.exists():
        pytest.skip("bible.db not present")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@corpus_only
def test_corpus_has_expected_totals(corpus_conn):
    row = corpus_conn.execute(
        "SELECT book_count, chapter_count, verse_count "
        "FROM corpus_meta WHERE translation = 'BSB'"
    ).fetchone()
    assert row is not None, "corpus_meta row missing — re-run ingest"
    assert (row["book_count"], row["chapter_count"], row["verse_count"]) == (
        66, 1189, 31086,
    )


@corpus_only
def test_no_verse_is_empty_or_whitespace_only(corpus_conn):
    offenders = corpus_conn.execute(
        "SELECT book, chapter, verse FROM verses "
        "WHERE trim(text) = ''"
    ).fetchall()
    assert offenders == [], f"{len(offenders)} empty verses: {offenders[:5]}"


@corpus_only
@pytest.mark.parametrize("needle", [
    "[object",     # naive JSON.stringify of an object
    "noteId",      # footnote-marker dict serialized as text
    "lineBreak",   # linebreak dict serialized as text
    "{'",          # Python dict repr leak
    "{\"",         # JSON dict leak
])
def test_no_stringified_json_leaked_into_text(corpus_conn, needle):
    offenders = corpus_conn.execute(
        "SELECT book, chapter, verse FROM verses WHERE text LIKE ?",
        (f"%{needle}%",),
    ).fetchall()
    assert offenders == [], (
        f"{len(offenders)} verses contain {needle!r}: {offenders[:5]}"
    )


@corpus_only
def test_no_verse_starts_or_ends_with_whitespace(corpus_conn):
    offenders = corpus_conn.execute(
        "SELECT book, chapter, verse FROM verses "
        "WHERE text != trim(text)"
    ).fetchall()
    assert offenders == [], f"{len(offenders)} verses with edge whitespace"


@corpus_only
def test_no_verse_contains_double_spaces(corpus_conn):
    offenders = corpus_conn.execute(
        "SELECT book, chapter, verse FROM verses WHERE text LIKE '%  %'"
    ).fetchall()
    assert offenders == [], f"{len(offenders)} verses have runs of >=2 spaces"


@corpus_only
def test_no_stray_space_before_closing_curly_quote(corpus_conn):
    """Space-before-closing-quote is the specific bug that flagged this
    audit; guard against regression."""
    offenders = corpus_conn.execute(
        "SELECT book, chapter, verse FROM verses "
        "WHERE text LIKE '% ”%' OR text LIKE '% ’%'"
    ).fetchall()
    assert offenders == [], (
        f"{len(offenders)} verses have space before closing curly quote: "
        f"{offenders[:5]}"
    )


# ---------------------------------------------------------------------------
# Specific verse spot-checks — sanity of well-known passages
# ---------------------------------------------------------------------------

@corpus_only
def test_psalm_117_has_two_verses(corpus_conn):
    verses = corpus_conn.execute(
        "SELECT verse FROM verses WHERE book='Psalms' AND chapter=117 "
        "ORDER BY verse"
    ).fetchall()
    assert [v["verse"] for v in verses] == [1, 2]


@corpus_only
def test_psalm_119_has_176_verses(corpus_conn):
    (n,) = corpus_conn.execute(
        "SELECT COUNT(*) FROM verses WHERE book='Psalms' AND chapter=119"
    ).fetchone()
    assert n == 176


@corpus_only
def test_genesis_1_5_contains_both_halves(corpus_conn):
    (text,) = corpus_conn.execute(
        "SELECT text FROM verses WHERE book='Genesis' AND chapter=1 AND verse=5"
    ).fetchone()
    assert "God called the light" in text
    assert "the first day" in text
    assert "lineBreak" not in text


@corpus_only
def test_john_3_16_is_the_expected_wording(corpus_conn):
    (text,) = corpus_conn.execute(
        "SELECT text FROM verses WHERE book='John' AND chapter=3 AND verse=16"
    ).fetchone()
    assert text.startswith("For God so loved the world")
    assert text.endswith("eternal life.")


@corpus_only
def test_john_3_3_closing_quote_attaches(corpus_conn):
    """Regression test for the space-before-closing-quote bug."""
    (text,) = corpus_conn.execute(
        "SELECT text FROM verses WHERE book='John' AND chapter=3 AND verse=3"
    ).fetchone()
    assert text.endswith("born again.”")
