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

@pytest.mark.parametrize("text, current", [
    ("Jude 5",      Reference("Jude", 5)),
    ("Obadiah 3",   Reference("Obadiah", 3)),
    ("Philemon 6",  Reference("Philemon", 6)),
    ("2 John 4",    Reference("2 John", 4)),
    ("3 John 2",    Reference("3 John", 2)),
])
def test_single_chapter_book_currently_treats_number_as_chapter(text, current):
    assert parse_references(text) == [current]


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

def test_roman_i_cor_currently_not_recognised():
    assert parse_references("I Cor 13:4") == []


def test_roman_ii_tim_currently_not_recognised():
    assert parse_references("II Tim 3:16") == []


def test_roman_iii_john_currently_matches_john_and_drops_iii():
    """Documents the misparse: 'III John 2' currently returns [John 2],
    treating the leading III as noise and matching the bare 'John'."""
    assert parse_references("III John 2") == [Reference("John", 2)]


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

def test_cross_chapter_range_genesis_currently_truncates():
    """'Genesis 1:1-2:3' currently parses as chapter 1, verses 1-2 —
    the ':3' after the second colon is dropped."""
    assert parse_references("Genesis 1:1-2:3") == [
        Reference("Genesis", 1, 1, 2)
    ]


def test_cross_chapter_range_psalms_currently_truncates():
    assert parse_references("Ps 22:1-23:6") == [
        Reference("Psalms", 22, 1, 23)
    ]


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
def test_single_chapter_book_lookup_currently_impossible():
    """A follow-on from the highest-value case: because the parser reads
    'Jude 26' as chapter 26 rather than verse 26 of chapter 1, get_range
    returns [] instead of surfacing a possible verse. Jude only has 25
    verses so v26 doesn't exist either way, but the point stands: the
    parser routes the request to the wrong lookup shape."""
    (ref,) = parse_references("Jude 26")
    assert (ref.chapter, ref.verse) == (26, None)
    # As chapter 26: empty. As verse 26: not tested by parser output.
    assert get_range("Jude", 26) == []


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


def test_integer_input_currently_raises_attributeerror():
    """DOCUMENTED FAILURE: passing an int reaches `.replace('.', ' ')` on
    a non-string and raises AttributeError. The spec says 'None of these
    may raise an unhandled exception' — this one does. Open decision."""
    with pytest.raises(AttributeError):
        parse_references(12345)


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
