"""Citation checker harness.

Reads an eval run file (schema in docs/SCHEMAS.md), iterates every
reference the model produced, and calls `classify_citation` on each.
Aggregates the results into a per-question and per-run summary.

**`classify_citation` is not implemented in this file.** The scoring
logic is the repo owner's; this module exists so that when it lands
there is nowhere left to bikeshed except the metric itself. See
`docs/CITATION_METRIC.md` for the metric specification.

CLI:
    python -m src.eval.citation_check <run_file> [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from src.corpus.references import (
    CorpusUnavailableError,
    Reference,
    get_range,
    reference_from_dict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "corpus" / "bible.db"

EXIT_NOT_IMPLEMENTED = 5
EXIT_CORPUS_MISSING = 6
EXIT_BAD_RUN_FILE = 7


# ---------------------------------------------------------------------------
# 5b — Verdict enum + CitationResult dataclass
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    """The five outcomes a single citation can receive.

    Kept as a `str` Enum so verdict values serialise directly to JSON as
    their string names, no custom encoder needed.
    """

    RESOLVED = "RESOLVED"
    """The reference exists in the canon, the text the model attributed
    to it matches the corpus, and the passage supports the surrounding
    claim. None of the failure conditions below apply."""

    UNRESOLVABLE = "UNRESOLVABLE"
    """The reference does not exist in the canon (e.g. book unknown,
    chapter beyond the book's length, verse beyond the chapter's length).
    A UNRESOLVABLE verdict says nothing about the model's *text* — only
    that the pointer itself is bad."""

    MISQUOTED = "MISQUOTED"
    """The reference exists in the canon but the quoted text the model
    attributed to it does not match the corpus text at that reference."""

    UNSUPPORTED = "UNSUPPORTED"
    """The reference exists and the quoted text matches the corpus, but
    the passage does not support the surrounding claim the model made.
    Correct citation of the wrong verse for the argument."""

    ERROR = "ERROR"
    """The check itself failed — an unexpected exception or a state the
    scorer refuses to classify. Never emitted for ordinary bad citations;
    that is what the four verdicts above cover."""


@dataclass(frozen=True)
class CitationResult:
    ref: Reference
    verdict: Verdict
    detail: str
    quoted_span: tuple[int, int] | None = None
    corpus_text: str | None = None


# ---------------------------------------------------------------------------
# 5a — the function the repo owner implements
# ---------------------------------------------------------------------------

def classify_citation(
    ref: Reference,
    answer: str,
    corpus_lookup: Callable[[Reference], list[tuple[int, int, str]]],
) -> CitationResult:
    """Classify one Reference extracted from a model answer.

    Arguments:
        ref: A `Reference` the extractor found in `answer`. Carries
            character offsets `ref.start` and `ref.end` into `answer`;
            `nearby_text(answer, ref)` returns the substring immediately
            after the reference for the scorer to inspect.
        answer: The raw model output the reference was extracted from.
            Passed verbatim; the scorer chooses how to locate quoted
            text within it.
        corpus_lookup: `corpus_lookup(ref)` returns a list of
            `(chapter, verse, text)` tuples covering the passage the
            reference points at, or an empty list if the passage does
            not exist in the corpus. All within-chapter and cross-chapter
            shapes are normalised to this uniform 3-tuple form.

    Returns:
        A `CitationResult` describing the verdict for this reference.

    Contract:
        The implementation MUST NOT raise on any input. On any internal
        failure it must return a `CitationResult` with
        `verdict=Verdict.ERROR` and a `detail` field explaining why.
        Callers rely on this to aggregate a whole run without having to
        wrap each call in try/except.

    See docs/CITATION_METRIC.md for the metric specification.
    """
    raise NotImplementedError(
        "classify_citation is implemented by the repo owner. "
        "See docs/CITATION_METRIC.md."
    )


# ---------------------------------------------------------------------------
# 5d — nearby text helper
# ---------------------------------------------------------------------------

def nearby_text(answer: str, ref: Reference, window: int = 300) -> str:
    """Return the raw substring of `answer` immediately following `ref.end`,
    clipped to the string bounds. No quote detection, no sentence
    splitting, no heuristics — extracting the quote is the metric's job."""
    if not isinstance(answer, str) or ref.end is None:
        return ""
    start = max(0, ref.end)
    stop = min(len(answer), start + window)
    return answer[start:stop]


# ---------------------------------------------------------------------------
# 5c — run file I/O and validation
# ---------------------------------------------------------------------------

_ALLOWED_TYPES = {"run_meta", "answer"}


class RunFileError(ValueError):
    """Raised on any structural problem with a run file. Distinct from
    `CorpusUnavailableError` and `NotImplementedError` so the CLI can
    map each to its own exit code and message."""


def load_run_file(path: Path) -> tuple[dict, list[dict]]:
    """Parse a run file, validate its shape, return (meta, answers).

    Raises RunFileError on: missing file, malformed JSON, no run_meta on
    the first non-blank line, more than one run_meta, unknown `type`.
    """
    if not path.exists():
        raise RunFileError(f"run file does not exist: {path}")
    meta: dict | None = None
    answers: list[dict] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise RunFileError(f"{path}:{lineno}: invalid JSON — {e}")
        rec_type = rec.get("type")
        if rec_type not in _ALLOWED_TYPES:
            raise RunFileError(
                f"{path}:{lineno}: unknown record type {rec_type!r} "
                f"(expected one of {sorted(_ALLOWED_TYPES)})"
            )
        if rec_type == "run_meta":
            if meta is not None:
                raise RunFileError(
                    f"{path}:{lineno}: second run_meta record; only one allowed"
                )
            meta = rec
        else:
            if meta is None:
                raise RunFileError(
                    f"{path}:{lineno}: answer record before run_meta"
                )
            answers.append(rec)
    if meta is None:
        raise RunFileError(f"{path}: no run_meta record found")
    return meta, answers


# ---------------------------------------------------------------------------
# 5c — corpus lookup adapter (the single normalisation point for N4)
# ---------------------------------------------------------------------------

def make_corpus_lookup(
    db_path: Path = DEFAULT_DB,
    translation: str = "BSB",
) -> Callable[[Reference], list[tuple[int, int, str]]]:
    """Return a `corpus_lookup(ref)` callable that yields (chapter, verse,
    text) tuples for any Reference shape.

    This is the ONE place we paper over the Stage 3 N4 open decision
    (get_range returning 2-tuples for within-chapter and 3-tuples for
    cross-chapter). The classifier sees a single shape.
    """

    def lookup(ref: Reference) -> list[tuple[int, int, str]]:
        if ref.end_chapter is not None:
            rows = get_range(
                ref.book, ref.chapter,
                start=ref.verse, end=ref.end_verse,
                end_chapter=ref.end_chapter,
                translation=translation, db_path=db_path,
            )
            # get_range returns 3-tuples in cross-chapter mode.
            return list(rows)  # already (chapter, verse, text)
        # Within-chapter (or whole-chapter): normalise 2-tuples up to
        # (chapter, verse, text) using ref.chapter.
        rows = get_range(
            ref.book, ref.chapter,
            start=ref.verse, end=ref.end_verse,
            translation=translation, db_path=db_path,
        )
        return [(ref.chapter, v, t) for (v, t) in rows]

    return lookup


# ---------------------------------------------------------------------------
# 5c — orchestration
# ---------------------------------------------------------------------------

@dataclass
class RunReport:
    meta: dict
    per_question: list[dict] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "meta": self.meta,
            "per_question": self.per_question,
            "totals": self.totals,
        }


def _result_to_dict(r: CitationResult) -> dict:
    return {
        "ref": {
            "book": r.ref.book, "chapter": r.ref.chapter,
            "verse": r.ref.verse, "end_verse": r.ref.end_verse,
            "end_chapter": r.ref.end_chapter,
            "start": r.ref.start, "end": r.ref.end,
        },
        "verdict": r.verdict.value,
        "detail": r.detail,
        "quoted_span": list(r.quoted_span) if r.quoted_span else None,
        "corpus_text": r.corpus_text,
    }


def run_check(
    run_path: Path,
    classifier: Callable[
        [Reference, str, Callable[[Reference], list[tuple[int, int, str]]]],
        CitationResult,
    ] | None = None,
    corpus_lookup: Callable[[Reference], list[tuple[int, int, str]]] | None = None,
    db_path: Path = DEFAULT_DB,
) -> RunReport:
    """Iterate every reference in `run_path` and classify it. Returns a
    RunReport. Raises `RunFileError` on structural problems and
    `CorpusUnavailableError` if the corpus is missing when a lookup is
    attempted.

    The `classifier` default resolves to `classify_citation` at *call*
    time so tests can monkeypatch the module attribute and see their
    replacement used here.
    """
    if classifier is None:
        classifier = classify_citation
    meta, answers = load_run_file(run_path)
    if corpus_lookup is None:
        corpus_lookup = make_corpus_lookup(db_path=db_path)

    report = RunReport(meta=meta)
    totals: Counter[str] = Counter()

    for a in answers:
        q_entry: dict = {
            "question_id": a.get("question_id"),
            "question": a.get("question"),
            "answer": a.get("answer"),
            "error": a.get("error"),
            "results": [],
            "counts": {},
        }
        if a.get("error") is not None:
            # Nothing to classify — record and move on.
            report.per_question.append(q_entry)
            continue

        answer_text = a.get("answer") or ""
        q_counts: Counter[str] = Counter()
        for ref_dict in a.get("refs_in_answer", []) or []:
            ref = reference_from_dict(ref_dict)
            result = classifier(ref, answer_text, corpus_lookup)
            q_entry["results"].append(_result_to_dict(result))
            q_counts[result.verdict.value] += 1
            totals[result.verdict.value] += 1
        q_entry["counts"] = dict(q_counts)
        report.per_question.append(q_entry)

    report.totals = dict(totals)
    return report


def format_summary(report: RunReport) -> str:
    lines = []
    lines.append(f"model: {report.meta.get('model')}")
    lines.append(f"corpus_sha256: {report.meta.get('corpus_sha256')}")
    lines.append(f"git_sha: {report.meta.get('git_sha')} "
                 f"(dirty={report.meta.get('git_dirty')})")
    total_refs = sum(report.totals.values())
    lines.append(f"total references classified: {total_refs}")
    for v in Verdict:
        n = report.totals.get(v.value, 0)
        pct = f" ({100*n/total_refs:.1f}%)" if total_refs else ""
        lines.append(f"  {v.value:>12}: {n}{pct}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("run_file", type=Path, help="Path to an eval run .jsonl")
    p.add_argument("--out", type=Path, default=None,
                   help="Write JSON report to this path (also printed to stdout summary).")
    p.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    args = p.parse_args(argv)

    try:
        report = run_check(args.run_file, db_path=args.db_path)
    except RunFileError as e:
        print(f"[abort] bad run file: {e}", file=sys.stderr)
        return EXIT_BAD_RUN_FILE
    except CorpusUnavailableError as e:
        print(
            f"[abort] corpus DB missing at {args.db_path}: {e}\n"
            f"Refusing to score citations against a missing corpus.",
            file=sys.stderr,
        )
        return EXIT_CORPUS_MISSING
    except NotImplementedError as e:
        print(
            f"[abort] classify_citation is not implemented yet — "
            f"see docs/CITATION_METRIC.md (src/eval/citation_check.py). {e}",
            file=sys.stderr,
        )
        return EXIT_NOT_IMPLEMENTED

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report.to_json(), indent=2))
        print(f"[wrote] {args.out}")
    print(format_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
