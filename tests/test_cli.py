"""Tests for src.cli.

The CLI is a thin adapter over answer_question — the tests here focus
on rendering (are abstentions distinct from errors? does provenance
appear?) and on the "never writes to data/eval/runs/" contract.

Model calls are mocked via monkeypatch on the shared pipeline module;
the real Ollama client is never constructed in these tests.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src import cli
from src.pipeline import PipelineOutput
from src.retrieval import RetrievedPassage


def _make_ok(text: str, passages=None) -> PipelineOutput:
    return PipelineOutput(
        prompt="[system]\n…\n\n[user]\n…",
        answer=text,
        refs_in_answer=[{"book": "John", "chapter": 3, "verse": 16,
                          "end_verse": None, "end_chapter": None,
                          "start": 0, "end": 9}],
        retrieval=None,
        abstained=False,
        error=None,
        passages=passages or [],
    )


def _make_abstain() -> PipelineOutput:
    return PipelineOutput(
        prompt="…", answer=None, refs_in_answer=[],
        retrieval={"abstention_reason": "no passages retrieved for the query"},
        abstained=True, error=None, passages=[],
    )


def _make_error() -> PipelineOutput:
    return PipelineOutput(
        prompt="…", answer=None, refs_in_answer=[],
        retrieval=None, abstained=False,
        error="TimeoutError: simulated",
        passages=[],
    )


@pytest.fixture(autouse=True)
def _mock_ollama(monkeypatch):
    """Never touch Ollama in these tests — the CLI constructs a client
    at call time. Patch the constructor to a no-op stub so nothing goes
    over the wire."""
    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass
        def chat(self, *args, **kwargs):
            return {"message": {"content": "unused"}}
    monkeypatch.setattr(cli.ollama, "Client", _StubClient)


def test_render_ok_includes_answer_and_provenance(capsys, monkeypatch):
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _make_ok("Answer text."))
    monkeypatch.setattr(cli, "load_system_prompt", lambda p: ("stub", "a" * 64))
    monkeypatch.setattr(cli, "read_corpus_sha256", lambda p: "b" * 64)
    monkeypatch.setattr(cli, "_read_index_version", lambda p: "i" * 64)
    rc = cli.main(["--mode", "baseline", "What is love?"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[answer]" in out
    assert "Answer text." in out
    assert "[provenance]" in out
    assert "mode=baseline" in out


def test_render_abstain_uses_refused_label_not_error(capsys, monkeypatch):
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _make_abstain())
    monkeypatch.setattr(cli, "load_system_prompt", lambda p: ("stub", "a" * 64))
    monkeypatch.setattr(cli, "read_corpus_sha256", lambda p: "b" * 64)
    monkeypatch.setattr(cli, "_read_index_version", lambda p: "i" * 64)
    cli.main(["--mode", "grounded", "obscure query"])
    out = capsys.readouterr().out
    assert "[refused]" in out
    assert "no passages retrieved for the query" in out
    # Must NOT frame abstention as an error.
    assert "[error]" not in out


def test_render_error_uses_error_label(capsys, monkeypatch):
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _make_error())
    monkeypatch.setattr(cli, "load_system_prompt", lambda p: ("stub", "a" * 64))
    monkeypatch.setattr(cli, "read_corpus_sha256", lambda p: "b" * 64)
    monkeypatch.setattr(cli, "_read_index_version", lambda p: "i" * 64)
    cli.main(["--mode", "grounded", "q"])
    out = capsys.readouterr().out
    assert "[error]" in out
    assert "TimeoutError" in out


def test_grounded_shows_retrieved_passages(capsys, monkeypatch):
    passage = RetrievedPassage(
        reference="John 3:16", book="John", chapter=3,
        verse_start=16, verse_end=16, text="For God so loved the world…",
        score=-1e9, rank=1,
    )
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _make_ok("Yes.", passages=[passage]))
    monkeypatch.setattr(cli, "load_system_prompt", lambda p: ("stub", "a" * 64))
    monkeypatch.setattr(cli, "read_corpus_sha256", lambda p: "b" * 64)
    monkeypatch.setattr(cli, "_read_index_version", lambda p: "i" * 64)
    cli.main(["--mode", "grounded", "q"])
    out = capsys.readouterr().out
    assert "[retrieved passages]" in out
    assert "[John 3:16]" in out
    assert "For God so loved the world" in out


def test_cli_never_writes_to_eval_runs(tmp_path, monkeypatch, capsys):
    """Snapshot the runs directory before and after — nothing must change."""
    runs = Path(__file__).resolve().parents[1] / "data" / "eval" / "runs"
    before = sorted(os.listdir(runs)) if runs.exists() else []
    monkeypatch.setattr(cli, "answer_question", lambda *a, **k: _make_ok("ok"))
    monkeypatch.setattr(cli, "load_system_prompt", lambda p: ("stub", "a" * 64))
    monkeypatch.setattr(cli, "read_corpus_sha256", lambda p: "b" * 64)
    monkeypatch.setattr(cli, "_read_index_version", lambda p: "i" * 64)
    cli.main(["--mode", "baseline", "q"])
    after = sorted(os.listdir(runs)) if runs.exists() else []
    assert before == after
