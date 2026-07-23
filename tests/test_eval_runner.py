"""Unit tests for the eval runner's provenance capture, collision handling,
and per-question error recovery.

These tests don't touch Ollama — the model call is mocked out. Anything
that would require a real Ollama server is covered by manual runs, not
by pytest.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.eval import run_eval as ev


# ---------------------------------------------------------------------------
# read_corpus_sha256
# ---------------------------------------------------------------------------

def _make_corpus_db(path: Path, sha: str, translation: str = "BSB") -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE corpus_meta (
                translation     TEXT PRIMARY KEY,
                source_url      TEXT,
                retrieved_at    TEXT,
                sha256_local    TEXT,
                sha256_upstream TEXT,
                book_count      INTEGER,
                chapter_count   INTEGER,
                verse_count     INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO corpus_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (translation, "url", "2026-07-24", sha, "up", 66, 1189, 31086),
        )


def test_read_corpus_sha256_returns_the_stored_hash(tmp_path):
    db = tmp_path / "bible.db"
    _make_corpus_db(db, sha="deadbeef" * 8)
    assert ev.read_corpus_sha256(db) == "deadbeef" * 8


def test_read_corpus_sha256_returns_none_when_db_missing(tmp_path):
    assert ev.read_corpus_sha256(tmp_path / "nope.db") is None


def test_read_corpus_sha256_returns_none_when_meta_table_missing(tmp_path):
    db = tmp_path / "bible.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
    assert ev.read_corpus_sha256(db) is None


# ---------------------------------------------------------------------------
# next_available_path
# ---------------------------------------------------------------------------

def test_next_available_path_returns_original_when_free(tmp_path):
    target = tmp_path / "run.jsonl"
    assert ev.next_available_path(target) == target


def test_next_available_path_suffixes_on_collision(tmp_path):
    target = tmp_path / "run.jsonl"
    target.touch()
    assert ev.next_available_path(target) == tmp_path / "run-2.jsonl"


def test_next_available_path_walks_forward_past_existing_suffixes(tmp_path):
    (tmp_path / "run.jsonl").touch()
    (tmp_path / "run-2.jsonl").touch()
    (tmp_path / "run-3.jsonl").touch()
    assert ev.next_available_path(tmp_path / "run.jsonl") == tmp_path / "run-4.jsonl"


# ---------------------------------------------------------------------------
# read_git_state — assumes tests run inside the Shepherd repo
# ---------------------------------------------------------------------------

def test_read_git_state_reports_current_head():
    sha, dirty = ev.read_git_state()
    assert sha is not None
    assert len(sha) == 40
    assert set(sha) <= set("0123456789abcdef")
    assert isinstance(dirty, bool)


def test_read_git_state_returns_none_outside_a_repo(tmp_path):
    sha, dirty = ev.read_git_state(tmp_path)
    assert sha is None
    assert dirty is False


# ---------------------------------------------------------------------------
# build_run_meta
# ---------------------------------------------------------------------------

def test_build_run_meta_captures_all_provenance_fields():
    meta = ev.build_run_meta(
        model="qwen2.5:3b",
        options={"temperature": 0.0, "top_p": 1.0, "seed": 1},
        timeout_s=90.0,
        corpus_sha256="abc" * 20,
        git_sha="a" * 40,
        git_dirty=True,
    )
    assert meta["type"] == "run_meta"
    assert meta["model"] == "qwen2.5:3b"
    assert meta["options"] == {"temperature": 0.0, "top_p": 1.0, "seed": 1}
    assert meta["timeout_s"] == 90.0
    assert meta["corpus_sha256"] == "abc" * 20
    assert meta["git_sha"] == "a" * 40
    assert meta["git_dirty"] is True
    assert "run_started_at" in meta and meta["run_started_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# run() — end-to-end with a stub client
# ---------------------------------------------------------------------------

class _StubResponse:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _StubClient:
    """Records every chat() call and returns a canned answer or raises."""
    def __init__(self, *, answer: str = "answer text", raise_on_ids: set[str] = None):
        self.calls: list[dict] = []
        self._answer = answer
        self._raise_on_ids = raise_on_ids or set()

    def chat(self, *, model, messages, options):
        self.calls.append({"model": model, "messages": messages, "options": options})
        # Match the raise-list against the user prompt (last message content).
        user_msg = messages[-1]["content"]
        if any(bad in user_msg for bad in self._raise_on_ids):
            raise TimeoutError("simulated timeout")
        return _StubResponse(self._answer)


@pytest.fixture
def wired_run(tmp_path, monkeypatch):
    """Build a self-contained run environment: fixture questions file,
    fixture DB with a corpus_meta row, and a stub Ollama client."""
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        json.dumps({"id": "q1", "question": "What is love?"}) + "\n"
        + json.dumps({"id": "q2", "question": "Explain BAD_QUERY please"}) + "\n"
        + json.dumps({"id": "q3", "question": "One more question"}) + "\n"
    )
    runs_dir = tmp_path / "runs"
    db = tmp_path / "bible.db"
    _make_corpus_db(db, sha="c0ffee" * 10)

    stub = _StubClient(raise_on_ids={"BAD_QUERY"})
    monkeypatch.setattr(ev.ollama, "Client", lambda **_: stub)
    return questions, runs_dir, db, stub


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l]


def test_run_writes_meta_header_and_per_question_records(wired_run):
    questions, runs_dir, db, _ = wired_run
    out = ev.run(
        model="stub-model",
        questions_path=questions,
        runs_dir=runs_dir,
        options={"temperature": 0.0, "top_p": 1.0, "seed": 1},
        timeout_s=5.0,
        db_path=db,
    )
    records = _read_jsonl(out)
    assert records[0]["type"] == "run_meta"
    assert records[0]["model"] == "stub-model"
    assert records[0]["corpus_sha256"] == "c0ffee" * 10
    assert records[0]["git_sha"] is not None      # we're in the shepherd repo
    assert records[0]["timeout_s"] == 5.0
    assert records[0]["options"]["temperature"] == 0.0

    answers = [r for r in records if r["type"] == "answer"]
    assert [r["id"] for r in answers] == ["q1", "q2", "q3"]


def test_run_records_per_question_error_and_continues(wired_run):
    """When one question raises, the run must NOT abort — it must record
    the error and move on to the remaining questions."""
    questions, runs_dir, db, _ = wired_run
    out = ev.run(
        model="stub-model",
        questions_path=questions,
        runs_dir=runs_dir,
        options={"temperature": 0.0},
        timeout_s=5.0,
        db_path=db,
    )
    answers = [r for r in _read_jsonl(out) if r["type"] == "answer"]
    q2 = next(a for a in answers if a["id"] == "q2")
    assert "error" in q2
    assert "TimeoutError" in q2["error"]
    # The record for q3 must still exist.
    q3 = next(a for a in answers if a["id"] == "q3")
    assert q3.get("answer") == "answer text"
    assert "elapsed_ms" in q2 and "elapsed_ms" in q3


def test_run_never_overwrites_existing_run_file(wired_run, monkeypatch):
    """Two runs in the same UTC second must produce two distinct files."""
    questions, runs_dir, db, _ = wired_run
    out1 = ev.run(
        model="m", questions_path=questions, runs_dir=runs_dir,
        options={}, timeout_s=5.0, db_path=db,
    )
    # Freeze the timestamp so the second run would collide on filename.
    import datetime as dt
    fixed_now = dt.datetime.now(dt.timezone.utc)
    class _FixedDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.replace(tzinfo=tz) if tz else fixed_now
    monkeypatch.setattr(ev.dt, "datetime", _FixedDT)

    out2 = ev.run(
        model="m", questions_path=questions, runs_dir=runs_dir,
        options={}, timeout_s=5.0, db_path=db,
    )
    out3 = ev.run(
        model="m", questions_path=questions, runs_dir=runs_dir,
        options={}, timeout_s=5.0, db_path=db,
    )
    assert out1 != out2 != out3
    assert out2.name.endswith("-2.jsonl") or out2 != out1
    assert out3 != out2
    # All three files still exist — no overwrites.
    assert out1.exists() and out2.exists() and out3.exists()
