"""Lexical (FTS5) retrieval over the ingested corpus.

Public surface:
    retrieve(query, k=5, context=0)  -> list[RetrievedPassage]
    RetrievedPassage                  — dataclass with the passages' shape
    IndexOutOfSyncError               — the FTS5 index is stale
    RetrievalIndexMissingError        — no FTS5 index has been built

The index is built by an explicit step (`python -m src.retrieval.build_index`).
Never built silently at query time — a silent rebuild would hide the fact
that the index was stale and mask exactly the bug the version guard exists
to catch.
"""
from src.retrieval.query import (
    IndexOutOfSyncError,
    RetrievalIndexMissingError,
    RetrievedPassage,
    retrieve,
)

__all__ = [
    "IndexOutOfSyncError",
    "RetrievalIndexMissingError",
    "RetrievedPassage",
    "retrieve",
]
