"""Shared text and reference normalisation.

Deliberately narrow: one canonical way to prepare a string for comparison,
one canonical way to render a Reference to a string. Every downstream
consumer (parser internals, eval runner, citation checker) must import
from here — two normalisers *will* disagree eventually and the disagreement
will look like a model error.
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_text", "canonical_reference_string"]


# Curly quotes → straight; hyphen variants → hyphen-minus. Keeps comparisons
# between BSB corpus text (which uses curly punctuation) and model output
# (which may use either) apples-to-apples.
_QUOTE_MAP = str.maketrans({
    "‘": "'",  # left single quote
    "’": "'",  # right single quote
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "–": "-",  # en dash
    "—": "-",  # em dash
})

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Normalise a chunk of text for comparison.

    Steps (in order):
    1. Unicode NFC.
    2. Curly quotes → straight; en/em dash → hyphen-minus.
    3. Collapse any whitespace run to a single space.
    4. Strip leading and trailing whitespace.

    This does NOT lowercase — case is often semantically meaningful in
    Scripture ("Lord" vs "lord") and lowercasing would erase that
    distinction.
    """
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.translate(_QUOTE_MAP)
    s = _WHITESPACE_RUN.sub(" ", s)
    return s.strip()


def canonical_reference_string(
    book: str,
    chapter: int,
    verse: int | None = None,
    end_verse: int | None = None,
    end_chapter: int | None = None,
) -> str:
    """One canonical printed form for a reference tuple.

    Valid shapes:
    - Book Chapter                     e.g. "Psalms 23"
    - Book Chapter:Verse               e.g. "John 3:16"
    - Book Chapter:Verse-EndVerse      e.g. "1 Corinthians 13:4-7"
    - Book Ch:V-EndCh:EndVerse         e.g. "Genesis 1:1-2:3"

    Nonsense combinations raise ValueError. Reference is a frozen
    dataclass with all-optional trailing fields; it's easy to construct
    an inconsistent state (e.g. end_chapter set but end_verse missing,
    or end_verse set without a base verse). Failing loudly at the
    render path is the right place to catch that — silent normalisation
    hides the caller's bug and no valid Reference constructed by the
    parser can hit this branch.

    `Reference.__str__` calls through here so any downstream string
    comparison uses the same rendering.
    """
    # verse=None means "whole chapter". In that mode neither end_verse
    # nor end_chapter can meaningfully be set.
    if verse is None:
        if end_verse is not None or end_chapter is not None:
            raise ValueError(
                f"invalid reference: verse=None with "
                f"end_verse={end_verse!r}, end_chapter={end_chapter!r}"
            )
        return f"{book} {chapter}"
    # verse is set. end_chapter without end_verse is nonsense — a
    # cross-chapter range needs both endpoints.
    if end_chapter is not None and end_verse is None:
        raise ValueError(
            f"invalid reference: end_chapter={end_chapter!r} set but "
            f"end_verse is None"
        )
    if end_chapter is not None:
        return f"{book} {chapter}:{verse}-{end_chapter}:{end_verse}"
    if end_verse is None:
        return f"{book} {chapter}:{verse}"
    return f"{book} {chapter}:{verse}-{end_verse}"
