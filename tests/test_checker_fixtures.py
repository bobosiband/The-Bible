"""Checker fixture harness.

For each `tests/checker_fixtures/fixture_*.json`:
  1. SKIP if `expected.verdicts == "LABELS_PENDING"` (labels not yet
     provided by the repo owner).
  2. SKIP if `classify_citation` is still `raise NotImplementedError`
     (scorer not yet implemented).
  3. Otherwise call `classify_citation` on every reference in
     `entry.refs_in_answer` and assert the resulting verdicts equal
     `expected.verdicts` in order.

Never passes vacuously — a fixture whose labels aren't ready SKIPS
with an explicit reason rather than quietly succeeding.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.corpus.references import reference_from_dict
from src.eval.citation_check import (
    Verdict,
    classify_citation,
    make_corpus_lookup,
)

FIXTURES_DIR = Path(__file__).parent / "checker_fixtures"
LABELS_PENDING = "LABELS_PENDING"

# ---- Fixture discovery ---------------------------------------------------

def _fixture_paths():
    return sorted(FIXTURES_DIR.glob("fixture_*.json"))


def _fixture_ids():
    return [p.stem for p in _fixture_paths()]


@pytest.fixture(params=_fixture_paths(), ids=_fixture_ids())
def fixture(request):
    path = request.param
    return json.loads(path.read_text())


# ---- Fixture-structure sanity checks (always run) ------------------------

def test_at_least_one_fixture_exists():
    paths = _fixture_paths()
    assert paths, (
        f"no fixture files in {FIXTURES_DIR}. Run "
        f"`python -m tests.checker_fixtures._generate`."
    )


def test_every_fixture_expected_block_is_labels_pending(fixture):
    """Until the repo owner labels these, every `expected.*` MUST be
    the placeholder. A filled-in value that isn't part of a real
    label pass is a bug — the schema is explicit about this."""
    expected = fixture["expected"]
    for key, value in expected.items():
        assert value == LABELS_PENDING, (
            f"expected.{key} is not LABELS_PENDING — this fixture has "
            f"been pre-labelled. Either finish labelling and update "
            f"the harness, or reset to LABELS_PENDING."
        )


def test_every_fixture_has_a_valid_entry_shape(fixture):
    """Cheap belt-and-braces — the fixture entries pass through the
    same reference deserialiser the checker uses, so wrong shapes
    would blow up here rather than deep inside classify_citation."""
    entry = fixture["entry"]
    assert entry["type"] == "answer"
    assert isinstance(entry["refs_in_answer"], list)
    for ref_dict in entry["refs_in_answer"]:
        # Round-trip through the shared deserialiser.
        ref = reference_from_dict(ref_dict)
        assert ref.book
        assert isinstance(ref.chapter, int)


# ---- The actual scoring assertion (SKIPs while labels/impl pending) ------

def _classify_available() -> bool:
    """Whether classify_citation is implemented. Cheap probe — call
    with a throwaway ref and no-op lookup, catch NotImplementedError.
    """
    from src.corpus.references import Reference
    try:
        classify_citation(Reference("John", 3, 16), "", lambda r: [])
    except NotImplementedError:
        return False
    except Exception:
        # Any other exception counts as "implemented" — the harness
        # will get a genuine per-fixture assertion or error, not a skip.
        return True
    return True


def test_fixture_verdicts_match_labels(fixture, tmp_path):
    expected_verdicts = fixture["expected"]["verdicts"]
    if expected_verdicts == LABELS_PENDING:
        pytest.skip(
            "labels pending — see tests/checker_fixtures/README.md; the "
            "repo owner writes the expected.verdicts list."
        )
    if not _classify_available():
        pytest.skip(
            "classify_citation still raises NotImplementedError — "
            "see docs/CITATION_METRIC.md."
        )
    # Must be a list of verdict strings in ref order.
    assert isinstance(expected_verdicts, list), (
        f"expected.verdicts must be a list, got {type(expected_verdicts).__name__}"
    )
    entry = fixture["entry"]
    refs = [reference_from_dict(d) for d in entry["refs_in_answer"]]
    assert len(expected_verdicts) == len(refs), (
        f"expected.verdicts has {len(expected_verdicts)} entries but the "
        f"fixture has {len(refs)} references — one label per reference."
    )
    # Corpus lookup mirrors what the CLI uses; the fixture harness runs
    # against the same corpus the citation checker does. If the corpus
    # is absent, this test would only be REACHED after the owner has
    # written labels, so we let the resulting CorpusUnavailableError
    # surface as a real failure rather than papering over it.
    corpus_lookup = make_corpus_lookup()
    answer_text = entry.get("answer") or ""
    got = [
        classify_citation(ref, answer_text, corpus_lookup).verdict
        for ref in refs
    ]
    expected = [Verdict(v) for v in expected_verdicts]
    assert got == expected, (
        f"fixture {entry['question_id']}: verdicts mismatch\n"
        f"  expected: {[v.value for v in expected]}\n"
        f"  got:      {[v.value for v in got]}"
    )
