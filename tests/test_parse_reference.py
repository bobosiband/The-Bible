"""Tests for the strict single-reference parser `parse_reference`.

Round-trip is the central property: for every reference the extractor
`parse_references` currently emits, `parse_reference(str(ref)) == ref`.
"""
from __future__ import annotations

import pytest

from src.corpus.references import (
    BOOKS,
    Reference,
    ReferenceParseError,
    parse_reference,
    parse_references,
    reference_from_dict,
    reference_to_dict,
)


# ---------------------------------------------------------------------------
# Round-trip: every canonical book name, chapter 1 verse 1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("book", list(BOOKS))
def test_round_trip_every_canonical_book_name(book):
    """The 66-book invariant carried over to the strict parser."""
    original = Reference(book, 1, 1)
    parsed = parse_reference(str(original))
    assert parsed == original


# ---------------------------------------------------------------------------
# Round-trip: every reference shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref", [
    # Whole chapter
    Reference("Psalms", 23),
    Reference("Genesis", 1),
    Reference("Revelation", 22),
    # Single verse
    Reference("John", 3, 16),
    Reference("1 Corinthians", 13, 4),
    # Within-chapter range
    Reference("1 Corinthians", 13, 4, 7),
    Reference("Matthew", 5, 3, 12),
    # Cross-chapter range
    Reference("Genesis", 1, 1, 3, end_chapter=2),
    Reference("Psalms", 22, 1, 6, end_chapter=23),
    # Single-chapter book (post-Stage 3 rewrite is 1:N)
    Reference("Jude", 1, 5),
    Reference("Obadiah", 1, 3),
    Reference("3 John", 1, 2),
])
def test_round_trip_range_and_special_forms(ref):
    parsed = parse_reference(str(ref))
    assert parsed == ref


# ---------------------------------------------------------------------------
# Round-trip: everything the extractor produces
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "John 3:16",
    "Ps 23",
    "1 Cor 13:4-7",
    "Genesis 1:1-2:3",
    "Rev 22:21",
    "Song of Solomon 2:1",
    "Matt 5:3-12",
    "Jude 5",
    "III John 2",
    "2 Timothy 3:16",
])
def test_round_trip_via_extractor(text):
    """For every input the extractor accepts, `parse_reference` accepts
    the extractor's `str(ref)` output and returns the same Reference."""
    (ref,) = parse_references(text)
    reparsed = parse_reference(str(ref))
    assert reparsed == ref


# ---------------------------------------------------------------------------
# Leading and trailing whitespace tolerated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("padded", [
    "  John 3:16",
    "John 3:16  ",
    "\tJohn 3:16\n",
    "  1 Cor 13:4-7  ",
])
def test_leading_and_trailing_whitespace_tolerated(padded):
    assert parse_reference(padded) == Reference("John" if "John" in padded else "1 Corinthians",
                                                3 if "John" in padded else 13,
                                                16 if "John" in padded else 4,
                                                None if "John" in padded else 7)


# ---------------------------------------------------------------------------
# Errors — malformed / multiple / trailing / non-str
# ---------------------------------------------------------------------------

def test_empty_input_raises():
    with pytest.raises(ReferenceParseError):
        parse_reference("")


def test_whitespace_only_input_raises():
    with pytest.raises(ReferenceParseError):
        parse_reference("   \t\n  ")


@pytest.mark.parametrize("bad", [None, 12345, 3.14, [], {}, object()])
def test_non_str_input_raises(bad):
    with pytest.raises(ReferenceParseError):
        parse_reference(bad)


def test_unknown_book_raises():
    """Book validation happens at the parse layer; no corpus needed."""
    with pytest.raises(ReferenceParseError):
        parse_reference("Hezekiah 3:16")


def test_book_only_raises_no_reference_found():
    """Bare book name without a chapter number — same rule as
    parse_references's DEFER decision but here it's an error, not silence."""
    with pytest.raises(ReferenceParseError):
        parse_reference("John")


def test_trailing_content_after_reference_raises():
    with pytest.raises(ReferenceParseError) as exc:
        parse_reference("John 3:16 extra text")
    assert "extra content" in str(exc.value) or "more than one" in str(exc.value)


def test_multiple_references_raises():
    with pytest.raises(ReferenceParseError) as exc:
        parse_reference("John 3:16 and John 3:17")
    assert "more than one" in str(exc.value)


def test_comma_continuation_rejected_by_strict_parser():
    """`parse_references` accepts 'Romans 3:23, 6:23' as a comma-list.
    The strict parser rejects it — 'exactly one complete reference'."""
    with pytest.raises(ReferenceParseError):
        parse_reference("Romans 3:23, 6:23")


# ---------------------------------------------------------------------------
# NON-validation: valid parse, invalid at lookup
# ---------------------------------------------------------------------------

def test_out_of_range_verse_parses_fine():
    """Genesis 51:1: parser doesn't know Genesis has 50 chapters. Lookup
    will fail; parse must not. That's the parse-vs-fabrication distinction
    the citation checker depends on."""
    ref = parse_reference("Genesis 51:1")
    assert ref == Reference("Genesis", 51, 1)


def test_out_of_range_chapter_parses_fine():
    ref = parse_reference("Psalm 151:1")
    assert ref == Reference("Psalms", 151, 1)


# ---------------------------------------------------------------------------
# Shared serde — writer and reader agree
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref", [
    Reference("John", 3, 16),
    Reference("Psalms", 23),
    Reference("1 Corinthians", 13, 4, 7),
    Reference("Genesis", 1, 1, 3, end_chapter=2),
    Reference("Jude", 1, 5),
])
def test_reference_serde_round_trip(ref):
    """to_dict → from_dict yields an equal Reference (equality ignores
    spans; the dict does preserve them so they survive the round-trip too)."""
    d = reference_to_dict(ref)
    reconstructed = reference_from_dict(d)
    assert reconstructed == ref


def test_reference_serde_preserves_spans():
    """Spans matter to the citation checker even though they're excluded
    from equality — nearby_text needs them."""
    ref = Reference("John", 3, 16, start=42, end=51)
    d = reference_to_dict(ref)
    r2 = reference_from_dict(d)
    assert r2.start == 42
    assert r2.end == 51


def test_reference_from_dict_tolerates_missing_optional_fields():
    """Old run files may not carry end_chapter/start/end. Deserialisation
    fills them with None rather than erroring."""
    r = reference_from_dict({"book": "John", "chapter": 3, "verse": 16})
    assert r == Reference("John", 3, 16)


def test_reference_from_dict_rejects_missing_required_fields():
    with pytest.raises(ValueError):
        reference_from_dict({"chapter": 3, "verse": 16})
    with pytest.raises(ValueError):
        reference_from_dict({"book": "John", "verse": 16})


def test_reference_from_dict_rejects_non_dict():
    with pytest.raises(TypeError):
        reference_from_dict(["John", 3, 16])
