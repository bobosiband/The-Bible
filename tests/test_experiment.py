"""Tests for src.eval.experiment.

The experiment orchestrator wires the other tools together. Tests here
focus on the failure paths that MUST be graceful — empty questions,
NotImplementedError from the checker — and on the mode-newest-run
locator that survives -2 suffixes when a run is repeated.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.eval import experiment as ex


def _write_run(dir: Path, name: str, mode: str) -> Path:
    p = dir / name
    meta = {
        "type": "run_meta", "mode": mode, "model": "stub",
        "options": {}, "timeout_s": 1.0,
        "corpus_sha256": "c", "system_prompt_sha256": "s",
        "questions_sha256": "q",
    }
    p.write_text(json.dumps(meta) + "\n")
    return p


def test_newest_run_for_mode_picks_last_matching(tmp_path):
    _write_run(tmp_path, "20260101T000000Z.jsonl", "baseline")
    p2 = _write_run(tmp_path, "20260102T000000Z.jsonl", "baseline")
    _write_run(tmp_path, "20260102T000001Z.jsonl", "grounded")
    assert ex._newest_run_for_mode(tmp_path, "baseline") == p2


def test_newest_run_for_mode_ignores_wrong_mode(tmp_path):
    _write_run(tmp_path, "20260101T000000Z.jsonl", "grounded")
    assert ex._newest_run_for_mode(tmp_path, "baseline") is None


def test_newest_run_for_mode_ignores_non_meta_files(tmp_path):
    (tmp_path / "garbage.jsonl").write_text("not json\n")
    p = _write_run(tmp_path, "20260101T000000Z.jsonl", "grounded")
    assert ex._newest_run_for_mode(tmp_path, "grounded") == p


def test_experiment_refuses_missing_questions_file(tmp_path, capsys):
    rc = ex.main([
        "--questions", str(tmp_path / "nope.jsonl"),
        "--runs-dir", str(tmp_path / "runs"),
        "--reports-dir", str(tmp_path / "reports"),
    ])
    assert rc == 3
    err = capsys.readouterr().err
    assert "not found" in err
    assert "docs/SCHEMAS.md" in err


def test_experiment_refuses_invalid_questions(tmp_path, capsys):
    q = tmp_path / "q.jsonl"
    q.write_text('{"id":"q001"}\n')  # missing 'question'
    rc = ex.main([
        "--questions", str(q),
        "--runs-dir", str(tmp_path / "runs"),
        "--reports-dir", str(tmp_path / "reports"),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "validation error" in err


def test_experiment_refuses_empty_questions(tmp_path, capsys):
    q = tmp_path / "q.jsonl"
    q.write_text("")
    rc = ex.main([
        "--questions", str(q),
        "--runs-dir", str(tmp_path / "runs"),
        "--reports-dir", str(tmp_path / "reports"),
    ])
    assert rc == 4
    err = capsys.readouterr().err
    assert "no questions" in err
    assert "docs/SCHEMAS.md" in err
