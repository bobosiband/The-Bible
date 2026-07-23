"""Tests for src.eval.citation_check.

Load + validate the golden fixture, prove the harness calls
classify_citation once per non-error reference (using a stub classifier),
and prove the CLI exits cleanly with the real (NotImplementedError)
classifier and with a missing DB.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.corpus.references import (
    CorpusUnavailableError,
    Reference,
    reference_to_dict,
)
from src.eval import citation_check as cc
from src.eval.citation_check import (
    CitationResult,
    RunFileError,
    Verdict,
    load_run_file,
    main,
    nearby_text,
    run_check,
)

FIXTURE = Path(__file__).parent / "fixtures" / "run_sample.jsonl"


# ---------------------------------------------------------------------------
# Fixture loads and validates
# ---------------------------------------------------------------------------

def test_fixture_loads_meta_and_six_answers():
    meta, answers = load_run_file(FIXTURE)
    assert meta["type"] == "run_meta"
    assert meta["model"] == "stub-model"
    assert len(answers) == 6
    assert [a["question_id"] for a in answers] == [
        "q001", "q002", "q003", "q004", "q005", "q006",
    ]


def test_run_file_missing_raises_run_file_error(tmp_path):
    with pytest.raises(RunFileError):
        load_run_file(tmp_path / "nope.jsonl")


def test_run_file_without_run_meta_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(json.dumps({"type": "answer", "question_id": "x"}) + "\n")
    with pytest.raises(RunFileError) as exc:
        load_run_file(p)
    assert "before run_meta" in str(exc.value)


def test_run_file_with_unknown_type_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        json.dumps({"type": "run_meta"}) + "\n"
        + json.dumps({"type": "weird"}) + "\n"
    )
    with pytest.raises(RunFileError) as exc:
        load_run_file(p)
    assert "unknown record type" in str(exc.value)


def test_run_file_with_two_run_meta_raises(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        json.dumps({"type": "run_meta"}) + "\n"
        + json.dumps({"type": "run_meta"}) + "\n"
    )
    with pytest.raises(RunFileError) as exc:
        load_run_file(p)
    assert "second run_meta" in str(exc.value)


# ---------------------------------------------------------------------------
# Harness calls classify_citation once per non-error reference (stub)
# ---------------------------------------------------------------------------

class _RecordingClassifier:
    def __init__(self):
        self.calls: list[Reference] = []

    def __call__(self, ref, answer, corpus_lookup):
        self.calls.append(ref)
        return CitationResult(
            ref=ref, verdict=Verdict.RESOLVED, detail="stub",
            quoted_span=None, corpus_text=None,
        )


def _null_lookup(ref):
    return []


def test_harness_calls_classifier_once_per_non_error_ref():
    """The fixture has 5 non-error answers each with 1 ref (5 refs total)
    and one error entry (0 refs). Stub must be called exactly 5 times."""
    stub = _RecordingClassifier()
    report = run_check(FIXTURE, classifier=stub, corpus_lookup=_null_lookup)
    assert len(stub.calls) == 5
    # Every classified ref accounted for in totals as RESOLVED (stub).
    assert report.totals == {"RESOLVED": 5}
    # The error question appears in per_question but contributes 0 counts.
    q006 = next(q for q in report.per_question if q["question_id"] == "q006")
    assert q006["error"] is not None
    assert q006["results"] == []
    assert q006["counts"] == {}


def test_harness_per_question_counts_are_populated():
    stub = _RecordingClassifier()
    report = run_check(FIXTURE, classifier=stub, corpus_lookup=_null_lookup)
    q001 = next(q for q in report.per_question if q["question_id"] == "q001")
    assert q001["counts"] == {"RESOLVED": 1}


def test_harness_classifier_receives_the_reference_and_full_answer():
    """Contract: `answer` passed to the classifier is verbatim, `ref` is
    deserialised from the dict form and equal to the original."""
    calls = []
    def spy(ref, answer, lookup):
        calls.append((ref, answer))
        return CitationResult(ref, Verdict.RESOLVED, "", None, None)

    run_check(FIXTURE, classifier=spy, corpus_lookup=_null_lookup)
    # q003 = "Real ref, mangled quote"
    ref, answer = next(c for c in calls if c[1].startswith("John 3:16 clearly"))
    assert ref == Reference("John", 3, 16)
    assert "Whosoever visits" in answer


# ---------------------------------------------------------------------------
# nearby_text
# ---------------------------------------------------------------------------

def test_nearby_text_returns_window_after_reference():
    answer = "John 3:16 says something specific right here."
    ref = Reference("John", 3, 16, start=0, end=9)
    assert nearby_text(answer, ref, window=100) == " says something specific right here."


def test_nearby_text_clips_at_string_end():
    answer = "John 3:16 short"
    ref = Reference("John", 3, 16, start=0, end=9)
    assert nearby_text(answer, ref, window=1000) == " short"


def test_nearby_text_empty_when_ref_end_none():
    ref = Reference("John", 3, 16)  # no start/end
    assert nearby_text("some answer", ref) == ""


def test_nearby_text_empty_for_non_str_answer():
    ref = Reference("John", 3, 16, start=0, end=9)
    assert nearby_text(None, ref) == ""


# ---------------------------------------------------------------------------
# CLI: real classifier raises NotImplementedError → clean abort
# ---------------------------------------------------------------------------

def test_cli_exits_cleanly_with_not_implemented(capsys):
    """The real classify_citation is used here (no stub injected via
    the CLI). Expected: exit code 5, useful stderr message, no traceback."""
    rc = main([str(FIXTURE)])
    assert rc == cc.EXIT_NOT_IMPLEMENTED
    err = capsys.readouterr().err
    assert "classify_citation is not implemented" in err
    assert "docs/CITATION_METRIC.md" in err
    assert "Traceback" not in err


def test_cli_subprocess_returns_expected_exit_code(tmp_path):
    """Belt-and-braces: run as a subprocess to prove exit propagation."""
    result = subprocess.run(
        [sys.executable, "-m", "src.eval.citation_check", str(FIXTURE)],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == cc.EXIT_NOT_IMPLEMENTED
    assert "classify_citation is not implemented" in result.stderr


# ---------------------------------------------------------------------------
# Missing DB → abort BEFORE any scoring happens
# ---------------------------------------------------------------------------

def test_cli_aborts_when_db_missing_before_scoring(tmp_path, capsys):
    """A missing DB must never be scored as fabrication. Use a stub that
    calls the corpus lookup — the lookup raises CorpusUnavailableError,
    which the CLI must surface cleanly."""
    calls = []

    def scorer_that_uses_lookup(ref, answer, lookup):
        # Force the lookup so the missing-DB path fires.
        rows = lookup(ref)   # will raise CorpusUnavailableError
        calls.append(rows)
        return CitationResult(ref, Verdict.RESOLVED, "", None, None)

    # Wire the real corpus lookup pointed at a non-existent DB.
    lookup = cc.make_corpus_lookup(db_path=tmp_path / "nope.db")
    with pytest.raises(CorpusUnavailableError):
        run_check(FIXTURE, classifier=scorer_that_uses_lookup, corpus_lookup=lookup)
    # Never scored anything.
    assert calls == []


def test_cli_main_returns_corpus_missing_exit_code(tmp_path, capsys, monkeypatch):
    """Using the CLI entrypoint: point at a missing DB, inject a scorer
    that forces the lookup, expect exit code 6 and the abort message."""
    def scorer(ref, answer, lookup):
        lookup(ref)   # will raise CorpusUnavailableError
        return CitationResult(ref, Verdict.RESOLVED, "", None, None)

    monkeypatch.setattr(cc, "classify_citation", scorer)
    rc = main([str(FIXTURE), "--db-path", str(tmp_path / "nope.db")])
    assert rc == cc.EXIT_CORPUS_MISSING
    err = capsys.readouterr().err
    assert "corpus DB missing" in err
    assert "Refusing to score citations" in err


# ---------------------------------------------------------------------------
# Report can be written to disk
# ---------------------------------------------------------------------------

def test_cli_writes_json_report_when_out_given(tmp_path, monkeypatch):
    """With a stub classifier injected, the CLI produces a JSON report
    with the expected keys and totals."""
    stub = _RecordingClassifier()
    monkeypatch.setattr(cc, "classify_citation", stub)
    out = tmp_path / "report.json"
    rc = main([str(FIXTURE), "--out", str(out), "--db-path", str(tmp_path / "nope.db")])
    assert rc == 0
    report = json.loads(out.read_text())
    assert "meta" in report and "per_question" in report and "totals" in report
    assert report["totals"] == {"RESOLVED": 5}
