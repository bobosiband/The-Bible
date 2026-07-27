"""Tests for src.eval.compare_runs.

Fully hermetic — the tests build small CheckerReport dicts by hand,
never touch the real corpus or Ollama. The comparator itself has no
external dependencies beyond stdlib + argparse.
"""
from __future__ import annotations

import json

import pytest

from src.eval import compare_runs as cr


def _mk_report(
    *,
    mode: str,
    questions_sha256: str = "q" * 64,
    corpus_sha256: str = "c" * 64,
    git_sha: str = "g" * 40,
    model: str = "qwen2.5:3b",
    options: dict | None = None,
    system_prompt_sha256: str = "s" * 64,
    per_question: list[dict] | None = None,
    totals: dict | None = None,
) -> dict:
    return {
        "meta": {
            "type": "run_meta",
            "mode": mode,
            "model": model,
            "options": options or {"temperature": 0.0, "top_p": 1.0, "seed": 1},
            "corpus_sha256": corpus_sha256,
            "git_sha": git_sha,
            "system_prompt_sha256": system_prompt_sha256,
            "questions_sha256": questions_sha256,
        },
        "per_question": per_question or [],
        "totals": totals or {v: 0 for v in cr.VERDICTS},
    }


def _mk_q(qid: str, verdicts: list[str]) -> dict:
    return {
        "question_id": qid,
        "question": f"question {qid}",
        "answer": "…",
        "error": None,
        "results": [
            {"ref": {"book": "John", "chapter": 3, "verse": 16},
             "verdict": v, "detail": "", "quoted_span": None,
             "corpus_text": None}
            for v in verdicts
        ],
        "counts": {v: verdicts.count(v) for v in set(verdicts)},
    }


# ---------------------------------------------------------------------------
# Provenance guard
# ---------------------------------------------------------------------------

def test_matching_provenance_returns_no_warnings():
    left = _mk_report(mode="baseline")
    right = _mk_report(mode="grounded")
    fatal, warnings = cr.check_provenance(left, right)
    assert fatal is None
    assert warnings == []


def test_mode_and_prompt_diff_is_expected_and_silent():
    """The point of comparing baseline vs grounded IS that mode and
    system_prompt_sha256 differ. Silence them."""
    left = _mk_report(mode="baseline", system_prompt_sha256="a" * 64)
    right = _mk_report(mode="grounded", system_prompt_sha256="b" * 64)
    fatal, warnings = cr.check_provenance(left, right)
    assert fatal is None
    assert warnings == []


def test_questions_sha256_mismatch_is_fatal():
    left = _mk_report(mode="baseline", questions_sha256="1" * 64)
    right = _mk_report(mode="grounded", questions_sha256="2" * 64)
    fatal, warnings = cr.check_provenance(left, right)
    assert fatal is not None
    assert "questions_sha256" in fatal
    assert warnings == []


def test_missing_questions_sha256_is_fatal():
    left = _mk_report(mode="baseline")
    del left["meta"]["questions_sha256"]
    right = _mk_report(mode="grounded")
    fatal, warnings = cr.check_provenance(left, right)
    assert fatal is not None
    assert "missing" in fatal


def test_model_mismatch_warns_but_not_fatal():
    left = _mk_report(mode="baseline", model="qwen2.5:3b")
    right = _mk_report(mode="grounded", model="qwen2.5:7b")
    fatal, warnings = cr.check_provenance(left, right)
    assert fatal is None
    assert any(w.key == "model" for w in warnings)


def test_corpus_git_options_mismatches_all_warn():
    left = _mk_report(
        mode="baseline",
        corpus_sha256="c" * 64, git_sha="a" * 40,
        options={"temperature": 0.0, "top_p": 1.0, "seed": 1},
    )
    right = _mk_report(
        mode="grounded",
        corpus_sha256="d" * 64, git_sha="b" * 40,
        options={"temperature": 0.5, "top_p": 1.0, "seed": 1},
    )
    _, warnings = cr.check_provenance(left, right)
    keys = {w.key for w in warnings}
    assert keys == {"corpus_sha256", "git_sha", "options"}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def test_render_includes_provenance_and_totals_tables():
    left = _mk_report(
        mode="baseline",
        per_question=[_mk_q("q001", ["MISQUOTED"])],
        totals={"MISQUOTED": 1, "RESOLVED": 0, "UNRESOLVABLE": 0,
                 "UNSUPPORTED": 0, "ERROR": 0},
    )
    right = _mk_report(
        mode="grounded",
        per_question=[_mk_q("q001", ["RESOLVED"])],
        totals={"RESOLVED": 1, "UNRESOLVABLE": 0, "MISQUOTED": 0,
                 "UNSUPPORTED": 0, "ERROR": 0},
    )
    md = cr.render(left, right)
    assert "## Provenance" in md
    assert "## Aggregate verdicts" in md
    assert "## Per-question verdicts" in md
    assert "q001" in md
    assert "MISQUOTED" in md
    assert "RESOLVED" in md


def test_render_shows_raw_counts_alongside_percentages():
    left = _mk_report(
        mode="baseline",
        totals={"RESOLVED": 3, "UNRESOLVABLE": 0, "MISQUOTED": 2,
                 "UNSUPPORTED": 0, "ERROR": 0},
    )
    right = _mk_report(mode="grounded")
    md = cr.render(left, right)
    # Raw count must appear; percentage must also appear beside it.
    assert "3 (60.0%)" in md
    assert "2 (40.0%)" in md
    # Never a bare "60.0%" without the count preceding it — grep proves it.
    for line in md.splitlines():
        if "%" in line and "|" in line:
            # Every % on a table row is preceded by a number and space.
            assert not line.strip().startswith("%")


def test_render_flags_per_question_differences():
    left = _mk_report(
        mode="baseline",
        per_question=[
            _mk_q("q001", ["MISQUOTED"]),
            _mk_q("q002", ["RESOLVED"]),
        ],
    )
    right = _mk_report(
        mode="grounded",
        per_question=[
            _mk_q("q001", ["RESOLVED"]),
            _mk_q("q002", ["RESOLVED"]),
        ],
    )
    md = cr.render(left, right)
    lines = [ln for ln in md.splitlines() if ln.startswith("| q0")]
    # q001 changed, q002 did not.
    q001_row = next(ln for ln in lines if "q001" in ln)
    q002_row = next(ln for ln in lines if "q002" in ln)
    assert "yes" in q001_row
    assert "no" in q002_row


def test_render_is_deterministic_over_repeated_calls():
    left = _mk_report(mode="baseline", per_question=[_mk_q("q002", ["MISQUOTED"]),
                                                       _mk_q("q001", ["RESOLVED"])])
    right = _mk_report(mode="grounded", per_question=[_mk_q("q001", ["RESOLVED"]),
                                                        _mk_q("q002", ["RESOLVED"])])
    assert cr.render(left, right) == cr.render(left, right)


def test_render_sorts_per_question_by_id_for_stable_output():
    left = _mk_report(
        mode="baseline",
        per_question=[_mk_q("q003", ["RESOLVED"]), _mk_q("q001", ["RESOLVED"])],
    )
    right = _mk_report(
        mode="grounded",
        per_question=[_mk_q("q002", ["RESOLVED"])],
    )
    md = cr.render(left, right)
    q001_pos = md.index("| q001 |")
    q002_pos = md.index("| q002 |")
    q003_pos = md.index("| q003 |")
    assert q001_pos < q002_pos < q003_pos


def test_render_includes_no_judgement_disclaimer():
    md = cr.render(_mk_report(mode="baseline"), _mk_report(mode="grounded"))
    assert "not a judgement" in md
    # Sanity: no unqualified verdict-quality words that would imply the
    # comparator is grading the results.
    for word in ("better", "worse", "winner", "improvement"):
        assert word.lower() not in md.lower()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write_report(path, report):
    path.write_text(json.dumps(report))


def test_cli_prints_markdown_and_writes_file(tmp_path, capsys):
    left = _mk_report(mode="baseline")
    right = _mk_report(mode="grounded")
    lp, rp = tmp_path / "b.json", tmp_path / "g.json"
    _write_report(lp, left)
    _write_report(rp, right)
    out = tmp_path / "out.md"
    rc = cr.main([str(lp), str(rp), "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "# Shepherd checker comparison" in captured.out
    assert captured.out.strip() in out.read_text() + captured.out.strip()  # file == stdout modulo trailing whitespace
    assert out.read_text() in captured.out


def test_cli_exits_two_on_questions_hash_mismatch(tmp_path, capsys):
    lp, rp = tmp_path / "b.json", tmp_path / "g.json"
    _write_report(lp, _mk_report(mode="baseline", questions_sha256="1" * 64))
    _write_report(rp, _mk_report(mode="grounded", questions_sha256="2" * 64))
    rc = cr.main([str(lp), str(rp)])
    assert rc == cr.EXIT_QUESTIONS_MISMATCH
    err = capsys.readouterr().err
    assert "different question sets" in err


def test_cli_exits_three_on_missing_questions_hash(tmp_path, capsys):
    lp, rp = tmp_path / "b.json", tmp_path / "g.json"
    left = _mk_report(mode="baseline")
    del left["meta"]["questions_sha256"]
    _write_report(lp, left)
    _write_report(rp, _mk_report(mode="grounded"))
    rc = cr.main([str(lp), str(rp)])
    assert rc == cr.EXIT_MISSING_QUESTIONS_HASH
    assert "missing" in capsys.readouterr().err


def test_cli_exits_four_on_missing_file(tmp_path, capsys):
    rc = cr.main([str(tmp_path / "nope1.json"), str(tmp_path / "nope2.json")])
    assert rc == cr.EXIT_BAD_FILE
    assert "not found" in capsys.readouterr().err


def test_cli_warns_on_provenance_diff_but_exits_zero_without_flag(tmp_path, capsys):
    lp, rp = tmp_path / "b.json", tmp_path / "g.json"
    _write_report(lp, _mk_report(mode="baseline", model="qwen2.5:3b"))
    _write_report(rp, _mk_report(mode="grounded", model="qwen2.5:7b"))
    rc = cr.main([str(lp), str(rp)])
    assert rc == 0
    assert "model" in capsys.readouterr().err


def test_cli_fails_on_provenance_mismatch_with_flag(tmp_path, capsys):
    lp, rp = tmp_path / "b.json", tmp_path / "g.json"
    _write_report(lp, _mk_report(mode="baseline", model="qwen2.5:3b"))
    _write_report(rp, _mk_report(mode="grounded", model="qwen2.5:7b"))
    rc = cr.main([str(lp), str(rp), "--fail-on-provenance-mismatch"])
    assert rc == cr.EXIT_PROVENANCE_FAIL


def test_cli_output_is_byte_identical_across_invocations(tmp_path, capsys):
    lp, rp = tmp_path / "b.json", tmp_path / "g.json"
    _write_report(lp, _mk_report(mode="baseline", per_question=[_mk_q("q001", ["RESOLVED"])]))
    _write_report(rp, _mk_report(mode="grounded", per_question=[_mk_q("q001", ["MISQUOTED"])]))

    cr.main([str(lp), str(rp)])
    first = capsys.readouterr().out
    cr.main([str(lp), str(rp)])
    second = capsys.readouterr().out
    assert first == second
