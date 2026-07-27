"""Generate the 13 checker fixtures from real BSB corpus text.

Every fixture is built by pulling verse text from the local corpus
(`data/corpus/bible.db`) via the same `get_verse` / `get_range`
helpers `citation_check` uses at grading time. This keeps the fixture
authoring surface honest — a MISQUOTED case is a real, byte-for-byte
alteration of an actual BSB verse, not a paraphrase the fixture author
remembered.

The generator is idempotent and deterministic: running it twice writes
the same bytes. Commit both the generator AND the fixture files; the
harness reads the JSON, not the generator output.

Run:
    .venv/bin/python -m tests.checker_fixtures._generate

Requires the corpus to be present. Fails loudly (rather than emitting
half-fake fixtures) if any expected verse is missing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.corpus.references import (
    get_range,
    get_verse,
    parse_references,
    reference_to_dict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / "data" / "corpus" / "bible.db"

# Stub provenance — matches the convention in tests/fixtures/run_sample.jsonl.
STUB_META = {
    "type": "run_meta",
    "run_started_at": "2026-07-27T00:00:00+00:00",
    "model": "stub-model",
    "options": {"temperature": 0.0, "top_p": 1.0, "seed": 1},
    "timeout_s": 120.0,
    "git_sha": "0" * 40,
    "git_dirty": False,
    "corpus_sha256": "d" * 64,
    "system_prompt_sha256": "c" * 64,
    "questions_sha256": "q" * 64,
    "mode": "grounded",
    "retrieval_params": {"k": 5, "context": 0, "threshold": -5.0,
                          "index_version": "i" * 64},
}

LABELS_PENDING = "LABELS_PENDING"


@dataclass
class Fixture:
    number: int
    slug: str
    description: str
    answer: str
    retrieval: dict | None = None
    error: str | None = None


def _passage(book: str, chapter: int, verse_start: int, verse_end: int,
              text: str, rank: int = 1) -> dict:
    """Build a retrieved-passage dict matching the RetrievedPassage schema."""
    if verse_start == verse_end:
        reference = f"{book} {chapter}:{verse_start}"
    else:
        reference = f"{book} {chapter}:{verse_start}-{verse_end}"
    return {
        "reference": reference,
        "book": book,
        "chapter": chapter,
        "verse_start": verse_start,
        "verse_end": verse_end,
        "end_chapter": None,
        "text": text,
        "score": -1e9,
        "rank": rank,
    }


def _make_retrieval(passages: list[dict], *, abstained: bool = False,
                     abstention_reason: str | None = None) -> dict:
    return {
        "mode": "grounded",
        "parameters": {"k": 5, "context": 0, "threshold": -5.0,
                        "index_version": "i" * 64},
        "passages": passages,
        "abstained": abstained,
        "abstention_reason": abstention_reason,
    }


def _fetch_verse(book: str, chapter: int, verse: int) -> str:
    text = get_verse(book, chapter, verse, db_path=DB_PATH)
    if text is None:
        raise SystemExit(
            f"corpus missing {book} {chapter}:{verse} — cannot generate "
            f"fixture. Run `python -m src.ingest.bsb` first."
        )
    return text


def _fetch_range(book: str, chapter: int, start: int, end: int) -> str:
    rows = get_range(book, chapter, start=start, end=end, db_path=DB_PATH)
    if not rows or len(rows) != (end - start + 1):
        raise SystemExit(
            f"corpus missing {book} {chapter}:{start}-{end}"
        )
    return " ".join(text for (_v, text) in rows)


def _fetch_cross(book: str, ch1: int, v1: int, ch2: int, v2: int) -> str:
    rows = get_range(book, ch1, start=v1, end=v2,
                      end_chapter=ch2, db_path=DB_PATH)
    if not rows:
        raise SystemExit(f"corpus missing {book} {ch1}:{v1}-{ch2}:{v2}")
    return " ".join(text for (_c, _v, text) in rows)


def build_fixtures() -> list[Fixture]:
    j316 = _fetch_verse("John", 3, 16)
    rom828 = _fetch_verse("Romans", 8, 28)
    ps23_1 = _fetch_verse("Psalms", 23, 1)
    ps23 = _fetch_range("Psalms", 23, 1, 6)
    cor13 = _fetch_range("1 Corinthians", 13, 4, 7)
    gen11_23 = _fetch_cross("Genesis", 1, 1, 2, 3)
    jude5 = _fetch_verse("Jude", 1, 5)

    fixtures: list[Fixture] = []

    # 1 — verbatim quote of the retrieved passage
    fixtures.append(Fixture(
        number=1, slug="verbatim_quote",
        description="Answer quotes the retrieved verse byte-for-byte.",
        answer=f"John 3:16 says: \"{j316}\"",
        retrieval=_make_retrieval([_passage("John", 3, 16, 16, j316)]),
    ))

    # 2 — quote with ONE word altered (deterministic swap: "loved" → "adored")
    if "loved" not in j316:
        raise SystemExit("John 3:16 does not contain 'loved' — fixture 2 needs a new perturbation")
    altered = j316.replace("loved", "adored", 1)
    fixtures.append(Fixture(
        number=2, slug="altered_word_quote",
        description="Reference correct, quote alters one word ('loved' → 'adored').",
        answer=f"John 3:16 tells us: \"{altered}\"",
        retrieval=_make_retrieval([_passage("John", 3, 16, 16, j316)]),
    ))

    # 3 — real reference NOT in retrieved passages (answer cites Romans 8:28
    #     but the retrieval bundle only holds John 3:16)
    fixtures.append(Fixture(
        number=3, slug="real_ref_not_retrieved",
        description=("Answer cites Romans 8:28 verbatim, but retrieval "
                      "supplied only John 3:16 — the citation is off-context."),
        answer=f"Romans 8:28 assures us: \"{rom828}\"",
        retrieval=_make_retrieval([_passage("John", 3, 16, 16, j316)]),
    ))

    # 4 — plausible fabricated reference (Matthew has 28 chapters)
    fixtures.append(Fixture(
        number=4, slug="fabricated_reference",
        description=("Answer cites Matthew 29:3 with invented text. The "
                      "reference does not exist in the canon."),
        answer=("Matthew 29:3 says: \"And the Lord walked upon the mountain "
                "at dawn to greet the wanderers.\""),
        retrieval=_make_retrieval([_passage("John", 3, 16, 16, j316)]),
    ))

    # 5 — real reference with invented text
    fixtures.append(Fixture(
        number=5, slug="real_ref_invented_text",
        description=("John 3:16 exists in the canon but the quoted text is "
                      "invented — this is the classic misquote case."),
        answer=("John 3:16 says: \"Whosoever visits the temple thrice shall "
                "gain wisdom and long life.\""),
        retrieval=_make_retrieval([_passage("John", 3, 16, 16, j316)]),
    ))

    # 6 — abstention record (grounded, answer=null, refs_in_answer=[])
    fixtures.append(Fixture(
        number=6, slug="abstention",
        description=("Grounded abstention — retrieval returned no passages "
                      "above the threshold and the model was not called."),
        answer="",
        retrieval=_make_retrieval(
            [], abstained=True,
            abstention_reason="no passages retrieved for the query",
        ),
    ))

    # 7 — error record (error!=null, answer=null)
    fixtures.append(Fixture(
        number=7, slug="error_answer",
        description="Model call failed; error field populated, no answer text.",
        answer="",
        retrieval=None,
        error="TimeoutError: simulated read timeout after 120s",
    ))

    # 8 — zero-reference answer (prose only)
    fixtures.append(Fixture(
        number=8, slug="zero_references",
        description=("Answer is prose that contains no parseable Scripture "
                      "reference — refs_in_answer is empty."),
        answer=("The passage speaks of divine love as a gift, offered without "
                "condition to all who believe."),
        retrieval=_make_retrieval([_passage("John", 3, 16, 16, j316)]),
    ))

    # 9 — range citation, verbatim
    fixtures.append(Fixture(
        number=9, slug="range_citation",
        description="Answer cites a verse range verbatim.",
        answer=f"1 Corinthians 13:4-7 describes love: \"{cor13}\"",
        retrieval=_make_retrieval(
            [_passage("1 Corinthians", 13, 4, 7, cor13)]
        ),
    ))

    # 10 — single-chapter book citation ("Jude 5" → Jude 1:5)
    fixtures.append(Fixture(
        number=10, slug="single_chapter_book",
        description=("Jude 5 (parses as Jude 1:5) — exercises the single-"
                      "chapter book rule end-to-end through the parser."),
        answer=f"Jude 5 warns us: \"{jude5}\"",
        retrieval=_make_retrieval([_passage("Jude", 1, 5, 5, jude5)]),
    ))

    # 11 — cross-chapter range
    fixtures.append(Fixture(
        number=11, slug="cross_chapter_range",
        description="Answer cites a cross-chapter range: Genesis 1:1-2:3.",
        answer=f"Genesis 1:1-2:3 records the creation account: \"{gen11_23}\"",
        retrieval=_make_retrieval(
            # A single cross-chapter passage is represented with
            # end_chapter set — copy the pattern the runner emits.
            [{"reference": "Genesis 1:1-2:3", "book": "Genesis",
              "chapter": 1, "verse_start": 1, "verse_end": 3,
              "end_chapter": 2, "text": gen11_23, "score": -1e9, "rank": 1}]
        ),
    ))

    # 12 — multiple references in one answer (John 3:16 verbatim + Ps 23:1
    #     with altered wording). The label decision is per-ref.
    altered_ps = ps23_1.replace("shepherd", "sheperd", 1) if "shepherd" in ps23_1 else ps23_1 + " ."
    fixtures.append(Fixture(
        number=12, slug="mixed_multi_ref",
        description=("Two references in one answer — John 3:16 verbatim; "
                      "Psalms 23:1 with a subtle spelling change. Verdict "
                      "per reference is deliberately different."),
        answer=(
            f"Two anchors of the faith: John 3:16 says \"{j316}\" — and "
            f"Psalms 23:1 says \"{altered_ps}\"."
        ),
        retrieval=_make_retrieval([
            _passage("John", 3, 16, 16, j316, rank=1),
            _passage("Psalms", 23, 1, 1, ps23_1, rank=2),
        ]),
    ))

    # 13 — reference embedded in prose (mid-sentence, not sentence-initial)
    fixtures.append(Fixture(
        number=13, slug="embedded_reference",
        description=("Reference is embedded mid-sentence rather than opening "
                      "the sentence — checker must handle nearby-text "
                      "extraction from either position."),
        answer=(
            f"When Paul writes in Romans 8:28 that \"{rom828}\", he is "
            f"comforting believers under pressure."
        ),
        retrieval=_make_retrieval([_passage("Romans", 8, 28, 28, rom828)]),
    ))
    return fixtures


def _entry_from_fixture(fx: Fixture) -> dict:
    """Wrap a Fixture into a valid EvalRunEntry answer record."""
    # refs_in_answer is populated by running the SAME extractor the
    # runner uses; this makes the fixture exercise the real parse path.
    refs = [reference_to_dict(r) for r in parse_references(fx.answer)]
    if fx.error is not None:
        # Error records: null answer, empty refs — matches the runner.
        answer_field: str | None = None
        refs = []
    elif not fx.answer:
        # Empty answer = abstention path.
        answer_field = None
    else:
        answer_field = fx.answer
    return {
        "type": "answer",
        "question_id": f"fx{fx.number:03d}",
        "question": fx.description,
        "prompt": f"[system]\nstub\n\n[user]\n{fx.description}",
        "answer": answer_field,
        "refs_in_answer": refs,
        "model": STUB_META["model"],
        "model_tag": STUB_META["model"],
        "options": STUB_META["options"],
        "system_prompt_sha256": STUB_META["system_prompt_sha256"],
        "timestamp": "2026-07-27T00:00:01+00:00",
        "git_commit_sha": STUB_META["git_sha"],
        "git_dirty": STUB_META["git_dirty"],
        "corpus_sha256": STUB_META["corpus_sha256"],
        "latency_ms": 100,
        "error": fx.error,
        "retrieval": fx.retrieval,
    }


def _fixture_json(fx: Fixture) -> str:
    payload = {
        "entry": _entry_from_fixture(fx),
        "expected": {
            "verdicts": LABELS_PENDING,
            "quoted_spans": LABELS_PENDING,
            "notes": LABELS_PENDING,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main() -> int:
    fixtures = build_fixtures()
    for fx in fixtures:
        path = FIXTURES_DIR / f"fixture_{fx.number:02d}_{fx.slug}.json"
        path.write_text(_fixture_json(fx))
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    print(f"[done] wrote {len(fixtures)} fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
