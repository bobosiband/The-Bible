"""Tests for src.corpus.normalize.

Exhaustive coverage of `canonical_reference_string`'s valid and invalid
input combinations — no output may contain the literal token "None".
"""
from __future__ import annotations

import pytest

from src.corpus.normalize import canonical_reference_string, normalize_text


# ---------------------------------------------------------------------------
# canonical_reference_string — valid combinations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verse, end_verse, end_chapter, expected", [
    # (verse, end_verse, end_chapter) → rendered string
    (None, None, None, "Genesis 1"),                # whole chapter
    (1,    None, None, "Genesis 1:1"),              # single verse
    (1,    3,    None, "Genesis 1:1-3"),            # within-chapter range
    (1,    3,    2,    "Genesis 1:1-2:3"),          # cross-chapter range
])
def test_valid_combinations_render(verse, end_verse, end_chapter, expected):
    assert canonical_reference_string(
        "Genesis", 1, verse=verse, end_verse=end_verse, end_chapter=end_chapter,
    ) == expected


# ---------------------------------------------------------------------------
# canonical_reference_string — nonsense combinations must raise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verse, end_verse, end_chapter, reason", [
    (None, 5,    None, "end_verse without base verse"),
    (None, None, 2,    "end_chapter without base verse"),
    (None, 5,    2,    "cross-chapter endpoints but no base verse"),
    (1,    None, 2,    "end_chapter set but end_verse missing"),
])
def test_nonsense_combinations_raise_value_error(verse, end_verse, end_chapter, reason):
    with pytest.raises(ValueError) as exc:
        canonical_reference_string(
            "Genesis", 1, verse=verse, end_verse=end_verse, end_chapter=end_chapter,
        )
    assert "invalid reference" in str(exc.value)


@pytest.mark.parametrize("verse, end_verse, end_chapter", [
    (None, None, None),
    (1,    None, None),
    (1,    3,    None),
    (1,    3,    2),
])
def test_no_none_token_in_valid_output(verse, end_verse, end_chapter):
    out = canonical_reference_string("Genesis", 1,
                                     verse=verse, end_verse=end_verse,
                                     end_chapter=end_chapter)
    assert "None" not in out


# ---------------------------------------------------------------------------
# normalize_text — sanity checks (already relied on by future citation checker)
# ---------------------------------------------------------------------------

def test_normalize_text_straightens_curly_quotes():
    assert normalize_text("“hello,” he said") == '"hello," he said'


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  a  \n  b\tc  ") == "a b c"


def test_normalize_text_preserves_case():
    assert normalize_text("Lord") == "Lord"
    assert normalize_text("lord") == "lord"


def test_normalize_text_returns_empty_for_non_str():
    assert normalize_text(None) == ""
    assert normalize_text(123) == ""
