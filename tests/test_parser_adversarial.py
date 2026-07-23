"""Adversarial parser audit — REPORT ONLY.

Every test in this file records what `src/corpus/references.py` currently
does for a specific edge case. Whether the current behaviour is correct is
decided in `PARSER_AUDIT.md`, not here. Do not "improve" the parser in
response to a failing test in this file — either the test is documenting
observed behaviour (assertion should already match), or a parser change
elsewhere has moved the behaviour and the audit needs to be re-run.

Grouped to match `PARSER_AUDIT.md`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.corpus.references import (
    Reference,
    get_range,
    get_verse,
    parse_references,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "corpus" / "bible.db"

def _strs(refs) -> list[str]:
    return [str(r) for r in refs]


# ---------------------------------------------------------------------------
# 1. Single-chapter books — highest-value case
# ---------------------------------------------------------------------------
# Jude/Obadiah/Philemon/2 John/3 John each have only one chapter. In common
# usage "Jude 5" means Jude verse 5 (i.e. chapter 1, verse 5). The parser
# currently treats the number as a chapter.

@pytest.mark.parametrize("text, expected", [
    ("Jude 5",      Reference("Jude", 1, 5)),
    ("Obadiah 3",   Reference("Obadiah", 1, 3)),
    ("Philemon 6",  Reference("Philemon", 1, 6)),
    ("2 John 4",    Reference("2 John", 1, 4)),
    ("3 John 2",    Reference("3 John", 1, 2)),
])
def test_single_chapter_book_treats_bare_number_as_verse(text, expected):
    """Stage 3 ruling on audit rows 1-5: for Jude, Obadiah, Philemon,
    2 John and 3 John a bare 'Book N' means Book 1:N — those five have
    only one chapter, so N cannot be a chapter."""
    assert parse_references(text) == [expected]


def test_single_chapter_books_with_explicit_colon_are_unchanged():
    """A user writing 'Jude 1:5' explicitly must still get chapter 1
    verse 5. The single-chapter rewrite only kicks in when there is no
    colon (no verse) in the input."""
    assert parse_references("Jude 1:5") == [Reference("Jude", 1, 5)]
    assert parse_references("3 John 1:2") == [Reference("3 John", 1, 2)]


# ---------------------------------------------------------------------------
# 2. Abbreviations with and without periods
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Gen 1:1",  Reference("Genesis", 1, 1)),
    ("Gen. 1:1", Reference("Genesis", 1, 1)),
    ("Gn 1:1",   Reference("Genesis", 1, 1)),
    ("Matt 5:3", Reference("Matthew", 5, 3)),
    ("Mt 5:3",   Reference("Matthew", 5, 3)),
    ("Mt. 5:3",  Reference("Matthew", 5, 3)),
])
def test_abbreviations_with_or_without_periods(text, expected):
    assert parse_references(text) == [expected]


# ---------------------------------------------------------------------------
# 3. Roman numerals and no-space forms
# ---------------------------------------------------------------------------

def test_roman_i_cor_resolves_to_1_corinthians():
    """Stage 3 ruling on audit row 6: I/II/III Roman prefixes are accepted."""
    assert parse_references("I Cor 13:4") == [
        Reference("1 Corinthians", 13, 4)
    ]


def test_roman_ii_tim_resolves_to_2_timothy():
    """Stage 3 ruling on audit row 7."""
    assert parse_references("II Tim 3:16") == [
        Reference("2 Timothy", 3, 16)
    ]


@pytest.mark.parametrize("text, canonical", [
    ("I Sam 3:10",   "1 Samuel"),
    ("II Sam 7:1",   "2 Samuel"),
    ("I Kgs 8:1",    "1 Kings"),
    ("II Kgs 2:11",  "2 Kings"),
    ("I Chr 29:11",  "1 Chronicles"),
    ("II Chr 7:14",  "2 Chronicles"),
    ("II Cor 5:17",  "2 Corinthians"),
    ("I Thess 5:16", "1 Thessalonians"),
    ("II Thess 3:3", "2 Thessalonians"),
    ("I Tim 6:12",   "1 Timothy"),
    ("I Pet 5:7",    "1 Peter"),
    ("II Pet 3:9",   "2 Peter"),
    ("I Jn 1:9",     "1 John"),
    ("II Jn 6",      "2 John"),
])
def test_roman_prefixes_across_all_numbered_books(text, canonical):
    """Sanity sweep: every numbered book of the Bible resolves via I/II
    (and 3 John via III, already covered separately by row 8)."""
    refs = parse_references(text)
    assert refs, f"{text!r} produced no refs"
    assert refs[0].book == canonical


def test_roman_iii_john_routes_to_3_john():
    """Stage 3 ruling on audit row 8: 'III John 2' must resolve to
    3 John, not to John. With rows 1-5 also applied, the bare '2' is
    interpreted as verse 2 of chapter 1 (single-chapter book)."""
    assert parse_references("III John 2") == [Reference("3 John", 1, 2)]


@pytest.mark.parametrize("text", ["1 Cor", "1Cor", "1 Jn", "1John"])
def test_bare_book_without_chapter_number_is_not_returned(text):
    """The parser requires a chapter number after the book — a lone book
    name (with or without leading digit / space) does not resolve."""
    assert parse_references(text) == []


# ---------------------------------------------------------------------------
# 4. Psalm forms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Psalm 23", "Psalms 23", "Ps 23", "Ps. 23"])
def test_psalm_forms_all_resolve_to_psalms_23(text):
    assert parse_references(text) == [Reference("Psalms", 23)]


# ---------------------------------------------------------------------------
# 5. Cross-chapter ranges
# ---------------------------------------------------------------------------

def test_cross_chapter_range_genesis_now_captures_end_chapter():
    """Stage 3 ruling on audit row 13: 'Genesis 1:1-2:3' becomes a proper
    cross-chapter Reference — from chapter 1 verse 1 through chapter 2
    verse 3 — with end_chapter=2 and end_verse=3."""
    (ref,) = parse_references("Genesis 1:1-2:3")
    assert ref.book == "Genesis"
    assert ref.chapter == 1
    assert ref.verse == 1
    assert ref.end_chapter == 2
    assert ref.end_verse == 3


def test_cross_chapter_range_psalms_now_captures_end_chapter():
    """Stage 3 ruling on audit row 14."""
    (ref,) = parse_references("Ps 22:1-23:6")
    assert (ref.chapter, ref.verse, ref.end_chapter, ref.end_verse) == (
        22, 1, 23, 6,
    )


def test_cross_chapter_reference_str_reproduces_input_shape():
    (ref,) = parse_references("Genesis 1:1-2:3")
    assert str(ref) == "Genesis 1:1-2:3"


def test_within_chapter_range_still_leaves_end_chapter_none():
    """Backwards compatibility: an ordinary within-chapter range keeps
    end_chapter=None so equality with pre-Stage-3 code still works."""
    (ref,) = parse_references("1 Cor 13:4-7")
    assert ref.end_chapter is None
    assert ref.end_verse == 7


@pytest.mark.corpus
def test_cross_chapter_get_range_returns_chapter_verse_text_tuples():
    """When end_chapter is set, get_range returns (chapter, verse, text)
    triples so callers can distinguish which chapter each verse came from."""
    rows = get_range("Genesis", 1, 30, 2, end_chapter=2)
    # Should include the tail of chapter 1 (verses 30, 31) and the head
    # of chapter 2 (verses 1, 2).
    chapters_seen = sorted({r[0] for r in rows})
    assert chapters_seen == [1, 2]
    # Row shape sanity: 3-tuple (chapter, verse, text).
    assert all(len(r) == 3 for r in rows)


# ---------------------------------------------------------------------------
# 6. Within-chapter ranges
# ---------------------------------------------------------------------------

def test_within_chapter_range_1_cor():
    assert parse_references("1 Cor 13:4-7") == [
        Reference("1 Corinthians", 13, 4, 7)
    ]


def test_within_chapter_range_matt():
    assert parse_references("Matt 5:3-12") == [
        Reference("Matthew", 5, 3, 12)
    ]


# ---------------------------------------------------------------------------
# 7. Comma lists
# ---------------------------------------------------------------------------

def test_comma_list_of_chapter_verse_currently_only_takes_first():
    """'Romans 3:23, 6:23' currently returns only [Romans 3:23]. The second
    chapter:verse after the comma is not treated as a continuation."""
    assert parse_references("Romans 3:23, 6:23") == [
        Reference("Romans", 3, 23)
    ]


def test_comma_list_of_verses_currently_only_takes_first():
    """'John 1:1, 14' currently returns only [John 1:1]. Comma-separated
    verses within the same chapter are not honoured."""
    assert parse_references("John 1:1, 14") == [Reference("John", 1, 1)]


# ---------------------------------------------------------------------------
# 8. Alternate names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Song of Solomon 2:1",
    "Song of Songs 2:1",
    "Canticles 2:1",
])
def test_song_alt_names(text):
    assert parse_references(text) == [Reference("Song of Solomon", 2, 1)]


def test_ecclesiastes_alone_is_not_returned():
    """Bare book name without a chapter number — same rule as 'Matthew' alone."""
    assert parse_references("Ecclesiastes") == []


def test_qoheleth_alt_name_not_currently_recognised():
    """'Qoheleth' is the transliterated Hebrew name of Ecclesiastes; not
    listed as an abbreviation in the current parser's book map."""
    assert parse_references("Qoheleth 1:1") == []


# ---------------------------------------------------------------------------
# 9. Misspelling — report only
# ---------------------------------------------------------------------------

def test_common_misspelling_revelations():
    """'Revelations' (plural, common lay misspelling) is currently accepted."""
    assert parse_references("Revelations 22:21") == [
        Reference("Revelation", 22, 21)
    ]


# ---------------------------------------------------------------------------
# 10. Must NOT resolve — needs lookup, not just parse
# ---------------------------------------------------------------------------

def test_nonexistent_verse_parses_but_lookup_fails():
    """'John 3:99' parses OK — the parser doesn't know verse counts.
    The DB lookup is the layer that must return None."""
    assert parse_references("John 3:99") == [Reference("John", 3, 99)]


@pytest.mark.corpus
def test_nonexistent_lookups_return_none_or_empty():
    assert get_verse("John", 3, 99) is None
    assert get_verse("Psalms", 151, 1) is None
    assert get_verse("Genesis", 51, 1) is None
    assert get_verse("Matthew", 29, 1) is None


@pytest.mark.parametrize("text", [
    "Psalm 151:1", "Genesis 51:1", "Matthew 29:1",
])
def test_out_of_range_still_parses(text):
    """These parse — the parser doesn't validate chapter/verse against
    real book counts. Downstream must call get_verse to know if the
    reference is resolvable."""
    refs = parse_references(text)
    assert len(refs) == 1


@pytest.mark.corpus
def test_single_chapter_book_out_of_range_verse_parses_but_lookup_fails():
    """After Stage 3 rows 1-5, 'Jude 26' parses as Jude 1:26 — chapter 1
    verse 26. Jude has 25 verses, so the *lookup* returns None. That's
    the correct routing: parse cleanly, then fail at the DB layer with
    the specific 'verse not found' signal (not a chapter-26 miss)."""
    (ref,) = parse_references("Jude 26")
    assert (ref.chapter, ref.verse) == (1, 26)
    assert get_verse("Jude", 1, 26) is None


@pytest.mark.corpus
def test_audit_rows_19_20_jude_26_and_obadiah_22_parse_then_fail_cleanly():
    """Stage 3 ruling on audit rows 19-20 — follows from rows 1-5.

    Both books are single-chapter and both lookups reference a verse past
    the end of the book. The parser must produce a well-formed Reference
    (chapter 1, verse N), and the DB layer must be what signals 'not
    present'. The two-layer signal is important for the citation checker:
    'parse failure' means the model wrote gibberish; 'lookup failure' means
    the model wrote a plausible-looking but nonexistent citation."""
    (jude,) = parse_references("Jude 26")
    (obad,) = parse_references("Obadiah 22")
    assert jude == Reference("Jude", 1, 26)
    assert obad == Reference("Obadiah", 1, 22)
    # Real book lengths: Jude has 25 verses; Obadiah has 21.
    assert get_verse("Jude", 1, 26) is None
    assert get_verse("Obadiah", 1, 22) is None
    # And the still-present valid verses do resolve.
    assert get_verse("Jude", 1, 25) is not None
    assert get_verse("Obadiah", 1, 21) is not None


# ---------------------------------------------------------------------------
# 11. Malformed inputs — must not raise unhandled exceptions (per spec)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("",              []),
    ("John",          []),
    ("John 3:",       [Reference("John", 3)]),   # trailing colon dropped
    ("3:16",          []),
    ("John :16",      []),
    ("John 3:16:20",  [Reference("John", 3, 16)]),  # extra ':20' ignored
    ("JohnJohn 3:16", []),
])
def test_malformed_string_inputs_dont_raise(text, expected):
    assert parse_references(text) == expected


def test_none_input_returns_empty_without_raising():
    assert parse_references(None) == []


def test_non_str_input_returns_empty_list():
    """Stage 3 ruling on audit row 21: any non-str input yields [] rather
    than raising. Never surface an internal type error to the caller."""
    assert parse_references(12345) == []
    assert parse_references(3.14) == []
    assert parse_references({"John": 3}) == []
    assert parse_references(object()) == []


# ---------------------------------------------------------------------------
# 12. Extraction — precision / recall on a mixed paragraph
# ---------------------------------------------------------------------------

_EXTRACTION_TEXT = (
    "Read John 3:16 and Romans 8:28 and Psalm 23 today. "
    "Also 1 in 3:1 odds. Acts 2 chapter three."
)
_EXTRACTION_TRUTH = {"John 3:16", "Romans 8:28", "Psalms 23"}


def test_extraction_paragraph_returned_refs():
    """Records exactly what the parser extracts from a mixed paragraph.
    Precision/recall analysis lives in PARSER_AUDIT.md."""
    got = _strs(parse_references(_EXTRACTION_TEXT))
    # Current behaviour: also returns a false-positive 'Acts 2' because
    # the parser matches book+chapter greedily without checking that the
    # surrounding words ("chapter three") contradict it.
    assert got == ["John 3:16", "Romans 8:28", "Psalms 23", "Acts 2"]


def test_extraction_precision_recall():
    got = set(_strs(parse_references(_EXTRACTION_TEXT)))
    true_positives = got & _EXTRACTION_TRUTH
    false_positives = got - _EXTRACTION_TRUTH
    false_negatives = _EXTRACTION_TRUTH - got
    # Recall must be perfect on the three intended references.
    assert false_negatives == set(), (
        f"missed intended refs: {false_negatives}"
    )
    # Precision below 1.0 — one false positive documented above.
    assert false_positives == {"Acts 2"}, (
        f"unexpected false positives: {false_positives}"
    )
    precision = len(true_positives) / len(got)
    recall = len(true_positives) / len(_EXTRACTION_TRUTH)
    assert (round(precision, 3), round(recall, 3)) == (0.75, 1.0)


# ---------------------------------------------------------------------------
# Stage 3, Task 3c — reference spans round-trip against the original string
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "John 3:16",
    "Read John 3:16 and Romans 8:28 and Psalm 23 today.",
    "Compare 1 Cor 13:4-7 with Rev. 22:21.",
    "See Ps. 23 and Rev 22:21 for context.",
    "Multiple: Genesis 1:1, Matt 5:3, Song of Solomon 2:1 — all famous.",
])
def test_reference_spans_roundtrip_to_original_text(text):
    """text[ref.start:ref.end] must reproduce the matched substring after
    normalising periods to spaces (the same transformation the parser applies
    internally). Since `.replace('.', ' ')` preserves string length, the
    normalised substring at the same offsets is what we compare against."""
    normalised = text.replace(".", " ")
    for ref in parse_references(text):
        assert ref.start is not None and ref.end is not None
        original_slice = text[ref.start:ref.end]
        normalised_slice = normalised[ref.start:ref.end]
        assert original_slice.replace(".", " ") == normalised_slice
        # The slice must begin at the book name — no leading whitespace.
        assert not original_slice.startswith(" ")


def test_reference_equality_ignores_spans():
    """Two references to the same passage from different positions in text
    must compare equal — spans are metadata, not identity."""
    refs = parse_references("John 3:16 and later John 3:16 again")
    assert refs[0] == refs[1]
    assert refs[0].start != refs[1].start


def test_cross_chapter_end_chapter_field_defaults_to_none():
    """Field exists and defaults to None on ordinary refs."""
    (ref,) = parse_references("John 3:16")
    assert ref.end_chapter is None


# ---------------------------------------------------------------------------
# Stage 3, Task 3b — dedupe parameter
# ---------------------------------------------------------------------------

def test_parse_references_returns_duplicates_by_default():
    refs = parse_references("John 3:16 and John 3:16 again")
    assert len(refs) == 2
    assert refs[0] == refs[1]


def test_parse_references_dedupe_true_keeps_first_occurrence_only():
    refs = parse_references("John 3:16 and John 3:16 again", dedupe=True)
    assert len(refs) == 1
    # First-occurrence position — the span still points at the first match.
    assert refs[0].start == 0


def test_dedupe_preserves_original_order():
    refs = parse_references(
        "Ps 23, then John 3:16, then Ps 23 again, then Rom 8:28.",
        dedupe=True,
    )
    assert [str(r) for r in refs] == ["Psalms 23", "John 3:16", "Romans 8:28"]
