"""Query the FTS5 lexical index.

Two paths, chosen by the query itself:

- **Direct reference lookup.** If `parse_references(query)` finds any
  reference the user wrote explicitly (e.g. "What does Romans 8:28 say"),
  each such reference is resolved against the verses table and returned
  in the order the user wrote them. Keyword scoring is never consulted
  when the user was explicit — otherwise "Romans 8:28" could be beaten
  by a keyword-heavier passage, which is exactly the failure mode this
  path exists to prevent.

- **Keyword search.** Only reached if the query contains no parseable
  reference. Runs FTS5 `MATCH` with BM25 ranking; tie-break is
  deterministic (canonical book order, then chapter, then verse) so
  repeated queries return identical results.

The index must be built explicitly (`python -m src.retrieval.build_index`).
Never built at query time — a silent rebuild would defeat the corpus-hash
guard that makes this module refuse to query a stale index.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.corpus.normalize import canonical_reference_string
from src.corpus.references import (
    BOOKS,
    CorpusUnavailableError,
    Reference,
    get_range,
    parse_references,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "corpus" / "bible.db"
DEFAULT_TRANSLATION = "BSB"

# Canonical book order for deterministic tie-breaking. Two verses with
# an identical BM25 score sort by (book_order, chapter, verse) so the
# same query on the same corpus always returns the same order.
_BOOK_ORDER = {name: i for i, name in enumerate(BOOKS)}

# Sentinel score for direct-lookup passages. BM25 scores are negative
# (more negative = better) and the grounded pipeline abstains when the
# top passage's score is ABOVE a negative threshold — so the sentinel
# must be far more negative than any realistic BM25 score. A user's
# explicit reference must never be gated by keyword-scoring heuristics.
# `float('-inf')` would express this most clearly but is not JSON-safe;
# -1e9 is small enough to sit under any real BM25 value (which never
# exceed roughly -100 in practice) while remaining serialisable.
_DIRECT_LOOKUP_SCORE = -1e9


class IndexOutOfSyncError(RuntimeError):
    """The FTS5 index was built against different corpus bytes than the
    corpus currently on disk. Queries refuse to run against a stale
    index because scoring against text the model may not see would
    produce plausible, wrong results."""


class RetrievalIndexMissingError(RuntimeError):
    """The FTS5 index or its meta table is not present. Distinct from
    IndexOutOfSyncError so the caller can distinguish 'never built' from
    'built but stale' — both need a rebuild but the diagnosis differs."""


@dataclass(frozen=True)
class RetrievedPassage:
    """One passage returned by `retrieve()`.

    `reference` is the canonical printed form and is the authoritative
    pointer; the numeric fields describe the same passage in structured
    form for callers that want to fetch surrounding context themselves.
    For cross-chapter passages `end_chapter` is set; otherwise it is None
    and the passage lives entirely inside `chapter`.
    """
    reference: str
    book: str
    chapter: int
    verse_start: int
    verse_end: int
    text: str
    score: float
    rank: int
    end_chapter: int | None = None


_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


def _sanitize_query(text: str) -> str:
    """Turn free-text into a safe FTS5 MATCH expression.

    Every word is wrapped in double quotes (with any embedded quote
    doubled) so FTS5 operator characters in the user's input can't
    change the query semantics. Tokens are joined with whitespace, which
    is FTS5's implicit AND — every token must appear in the matching
    verse (after porter stemming). OR-semantics was tried first and let
    high-frequency terms swamp distinctive ones: "shepherd" was pulling
    verses with more shepherd-count than Psalm 23:1. Implicit AND is
    strict but predictable, and BM25 still ranks the matches by rarity.
    An empty token list produces an empty string, and the caller
    short-circuits to `[]` rather than sending an empty MATCH.
    """
    tokens = _TOKEN_RE.findall(text or "")
    quoted = [f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens if t]
    return " ".join(quoted)


def _verify_index_synced(conn: sqlite3.Connection, translation: str) -> None:
    """Confirm the FTS5 table exists AND its recorded corpus SHA matches
    the current corpus_meta SHA. Raises the appropriate custom error."""
    has_fts = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type IN ('table','view') AND name='verses_fts'"
    ).fetchone()
    has_meta_table = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='retrieval_index_meta'"
    ).fetchone()
    if not has_fts or not has_meta_table:
        raise RetrievalIndexMissingError(
            "FTS5 index is not built. "
            "Run `python -m src.retrieval.build_index`."
        )
    row = conn.execute(
        "SELECT corpus_sha256 FROM retrieval_index_meta WHERE translation = ?",
        (translation,),
    ).fetchone()
    if not row:
        raise RetrievalIndexMissingError(
            f"no retrieval_index_meta row for translation={translation!r}. "
            f"Rebuild the index."
        )
    index_corpus_sha = row[0]
    cur = conn.execute(
        "SELECT sha256_local FROM corpus_meta WHERE translation = ?",
        (translation,),
    ).fetchone()
    if not cur or not cur[0]:
        raise IndexOutOfSyncError(
            f"corpus_meta has no sha256_local for translation={translation!r} "
            f"but the FTS5 index was built against {index_corpus_sha[:12]}…. "
            f"Re-run ingest, then rebuild the index."
        )
    if cur[0] != index_corpus_sha:
        raise IndexOutOfSyncError(
            f"FTS5 index was built against corpus_sha256={index_corpus_sha[:12]}…, "
            f"current corpus_sha256={cur[0][:12]}…. "
            f"Rebuild: `python -m src.retrieval.build_index`."
        )


def _direct_lookup_within_chapter(
    ref: Reference,
    translation: str,
    db_path: Path,
    context: int,
    rank: int,
) -> RetrievedPassage | None:
    """Resolve a within-chapter Reference, applying a symmetric context
    window clipped to the chapter's own start and end. Returns None if
    the base reference has no rows in the corpus."""
    rows = get_range(
        ref.book, ref.chapter,
        start=ref.verse, end=ref.end_verse,
        translation=translation, db_path=db_path,
    )
    if not rows:
        return None
    orig_verses = [v for (v, _t) in rows]
    if ref.verse is None:
        # Whole-chapter reference. Context expansion doesn't apply — the
        # whole chapter is already retrieved and there is nothing outside
        # it to include without crossing the chapter boundary.
        verse_start, verse_end = orig_verses[0], orig_verses[-1]
        text = " ".join(t for (_v, t) in rows)
        return RetrievedPassage(
            reference=str(ref),
            book=ref.book, chapter=ref.chapter,
            verse_start=verse_start, verse_end=verse_end,
            text=text, score=_DIRECT_LOOKUP_SCORE, rank=rank,
        )
    # Verse-specific ref. Expand symmetrically by `context`; get_range
    # only returns verses that actually exist, so the returned slice is
    # already clipped to the chapter's real end.
    if context > 0:
        exp_start = max(1, orig_verses[0] - context)
        exp_end = orig_verses[-1] + context
        rows = get_range(
            ref.book, ref.chapter,
            start=exp_start, end=exp_end,
            translation=translation, db_path=db_path,
        )
    verse_nums = [v for (v, _t) in rows]
    verse_start, verse_end = verse_nums[0], verse_nums[-1]
    text = " ".join(t for (_v, t) in rows)
    ref_str = canonical_reference_string(
        ref.book, ref.chapter,
        verse=verse_start,
        end_verse=verse_end if verse_end != verse_start else None,
    )
    return RetrievedPassage(
        reference=ref_str,
        book=ref.book, chapter=ref.chapter,
        verse_start=verse_start, verse_end=verse_end,
        text=text, score=_DIRECT_LOOKUP_SCORE, rank=rank,
    )


def _direct_lookup_cross_chapter(
    ref: Reference,
    translation: str,
    db_path: Path,
    rank: int,
) -> RetrievedPassage | None:
    """Resolve a cross-chapter Reference. Context expansion is not
    supported here — the brief bans crossing chapter boundaries, and
    the user has already picked an explicit multi-chapter span."""
    rows = get_range(
        ref.book, ref.chapter,
        start=ref.verse, end=ref.end_verse,
        end_chapter=ref.end_chapter,
        translation=translation, db_path=db_path,
    )
    if not rows:
        return None
    text = " ".join(t for (_c, _v, t) in rows)
    return RetrievedPassage(
        reference=str(ref),
        book=ref.book, chapter=ref.chapter,
        verse_start=ref.verse, verse_end=ref.end_verse,
        end_chapter=ref.end_chapter,
        text=text, score=_DIRECT_LOOKUP_SCORE, rank=rank,
    )


def _direct_lookup(
    ref: Reference,
    translation: str,
    db_path: Path,
    context: int,
    rank: int,
) -> RetrievedPassage | None:
    if ref.end_chapter is not None:
        return _direct_lookup_cross_chapter(ref, translation, db_path, rank)
    return _direct_lookup_within_chapter(
        ref, translation, db_path, context, rank,
    )


def _keyword_search(
    query: str,
    translation: str,
    conn: sqlite3.Connection,
    k: int,
    context: int,
    db_path: Path,
) -> list[RetrievedPassage]:
    match_expr = _sanitize_query(query)
    if not match_expr:
        return []
    # Over-fetch a bit so the deterministic tiebreak on identical BM25
    # scores has room to work; a strict LIMIT k inside SQLite would
    # otherwise cut off before we could re-sort.
    over_fetch = max(k * 4, 20)
    rows = conn.execute(
        """
        SELECT book, chapter, verse, text, bm25(verses_fts) AS score
        FROM verses_fts
        WHERE verses_fts MATCH ? AND translation = ?
        LIMIT ?
        """,
        (match_expr, translation, over_fetch),
    ).fetchall()
    # Deterministic tiebreak: BM25 first (more negative = better),
    # then canonical book order, then chapter, then verse.
    rows.sort(key=lambda r: (
        r[4],
        _BOOK_ORDER.get(r[0], len(_BOOK_ORDER)),
        r[1], r[2],
    ))
    rows = rows[:k]
    passages: list[RetrievedPassage] = []
    for rank, (book, chapter, verse, text, score) in enumerate(rows, start=1):
        if context > 0:
            exp_start = max(1, verse - context)
            exp_end = verse + context
            crows = get_range(
                book, chapter, start=exp_start, end=exp_end,
                translation=translation, db_path=db_path,
            )
            if crows:
                verse_start = crows[0][0]
                verse_end = crows[-1][0]
                text = " ".join(t for (_v, t) in crows)
            else:
                verse_start, verse_end = verse, verse
        else:
            verse_start, verse_end = verse, verse
        ref_str = canonical_reference_string(
            book, chapter,
            verse=verse_start,
            end_verse=verse_end if verse_end != verse_start else None,
        )
        passages.append(RetrievedPassage(
            reference=ref_str,
            book=book, chapter=chapter,
            verse_start=verse_start, verse_end=verse_end,
            text=text, score=float(score), rank=rank,
        ))
    return passages


def retrieve(
    query: str,
    k: int = 5,
    context: int = 0,
    db_path: Path = DEFAULT_DB,
    translation: str = DEFAULT_TRANSLATION,
) -> list[RetrievedPassage]:
    """Retrieve up to `k` passages relevant to `query`.

    - Empty/whitespace/non-string query → empty list.
    - Query containing at least one parseable reference → direct lookup
      of each reference in original order; those that resolve are
      returned (rank 1..N). Keyword search is never consulted.
    - Otherwise → BM25 keyword search over the FTS5 index. `context` is
      a symmetric verse window applied to each hit, clipped so it never
      crosses a chapter boundary.

    Raises:
        CorpusUnavailableError: the DB file is missing.
        RetrievalIndexMissingError: the FTS5 index was never built.
        IndexOutOfSyncError: the index was built against different
            corpus bytes than the corpus_meta now records.
    """
    if not isinstance(query, str) or not query.strip():
        return []
    if not Path(db_path).exists():
        raise CorpusUnavailableError(
            f"corpus DB not found at {db_path}. "
            f"Run `python -m src.ingest.bsb` to build it."
        )
    with sqlite3.connect(db_path) as conn:
        _verify_index_synced(conn, translation)
        # Direct-lookup path.
        parsed = parse_references(query)
        if parsed:
            passages: list[RetrievedPassage] = []
            rank = 1
            for ref in parsed:
                p = _direct_lookup(ref, translation, db_path, context, rank)
                if p is not None:
                    passages.append(p)
                    rank += 1
            # If the user was explicit, honour that even when nothing
            # resolves — an empty result is the honest answer to "give
            # me Hezekiah 3:16." Falling through to keyword search would
            # produce plausible-looking but semantically wrong results.
            return passages
        # Keyword path.
        return _keyword_search(query, translation, conn, k, context, db_path)
