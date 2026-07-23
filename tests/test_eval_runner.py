"""Unit tests for the eval runner's provenance capture, collision handling,
per-question error recovery, empty-questions refusal, and frozen schema.

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
                verse_count     INTEGER,
                loader_version  TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO corpus_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (translation, "url", "2026-07-24", sha, "up", 66, 1189, 31086, "loader-v1"),
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
# System prompt loading
# ---------------------------------------------------------------------------

def test_load_system_prompt_returns_text_and_stable_hash(tmp_path):
    p = tmp_path / "system.txt"
    p.write_text("hello world\n")
    text, sha = ev.load_system_prompt(p)
    assert text == "hello world"
    # Stable across trailing-whitespace-invariant edits: hash is over raw bytes.
    import hashlib
    assert sha == hashlib.sha256(b"hello world\n").hexdigest()


def test_load_default_system_prompt_exists():
    """The versioned prompts/system.txt must always be present."""
    text, sha = ev.load_system_prompt()
    assert text and len(sha) == 64


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
        system_prompt_sha256="d" * 64,
    )
    assert meta["type"] == "run_meta"
    assert meta["model"] == "qwen2.5:3b"
    assert meta["options"] == {"temperature": 0.0, "top_p": 1.0, "seed": 1}
    assert meta["timeout_s"] == 90.0
    assert meta["corpus_sha256"] == "abc" * 20
    assert meta["git_sha"] == "a" * 40
    assert meta["git_dirty"] is True
    assert meta["system_prompt_sha256"] == "d" * 64
    assert meta["run_started_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# run() — end-to-end with a stub client
# ---------------------------------------------------------------------------

class _StubResponse:
    def __init__(self, content: str):
        self.message = type("M", (), {"content": content})()


class _StubClient:
    def __init__(self, *, answer: str = "answer text", raise_on_ids: set[str] = None):
        self.calls: list[dict] = []
        self._answer = answer
        self._raise_on_ids = raise_on_ids or set()

    def chat(self, *, model, messages, options):
        self.calls.append({"model": model, "messages": messages, "options": options})
        user_msg = messages[-1]["content"]
        if any(bad in user_msg for bad in self._raise_on_ids):
            raise TimeoutError("simulated timeout")
        return _StubResponse(self._answer)


@pytest.fixture
def wired_run(tmp_path, monkeypatch):
    questions = tmp_path / "questions.jsonl"
    questions.write_text(
        json.dumps({"id": "q1", "question": "What is love?"}) + "\n"
        + json.dumps({"id": "q2", "question": "Explain BAD_QUERY please"}) + "\n"
        + json.dumps({"id": "q3", "question": "Cite John 3:16 and 1 Cor 13:4-7"}) + "\n"
    )
    runs_dir = tmp_path / "runs"
    db = tmp_path / "bible.db"
    _make_corpus_db(db, sha="c0ffee" * 10)
    system_prompt = tmp_path / "system.txt"
    system_prompt.write_text("test system prompt\n")

    stub = _StubClient(
        answer="Love is patient. See 1 Cor 13:4-7 and John 3:16.",
        raise_on_ids={"BAD_QUERY"},
    )
    monkeypatch.setattr(ev.ollama, "Client", lambda **_: stub)
    return questions, runs_dir, db, system_prompt, stub


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l]


def test_run_writes_meta_header_with_frozen_schema(wired_run):
    questions, runs_dir, db, sysprompt, _ = wired_run
    out = ev.run(
        model="stub-model", questions_path=questions, runs_dir=runs_dir,
        options={"temperature": 0.0, "top_p": 1.0, "seed": 1},
        timeout_s=5.0, db_path=db, system_prompt_path=sysprompt,
    )
    records = _read_jsonl(out)
    meta = records[0]
    assert meta["type"] == "run_meta"
    for field in [
        "run_started_at", "model", "options", "timeout_s",
        "git_sha", "git_dirty", "corpus_sha256", "system_prompt_sha256",
    ]:
        assert field in meta


def test_run_emits_frozen_eval_run_entry_shape(wired_run):
    """Every answer record must carry every frozen field, in the shape
    documented in docs/SCHEMAS.md."""
    questions, runs_dir, db, sysprompt, _ = wired_run
    out = ev.run(
        model="stub-model", questions_path=questions, runs_dir=runs_dir,
        options={"temperature": 0.0}, timeout_s=5.0,
        db_path=db, system_prompt_path=sysprompt,
    )
    answers = [r for r in _read_jsonl(out) if r["type"] == "answer"]
    required = {
        "type", "question_id", "question", "prompt", "answer",
        "refs_in_answer", "model", "model_tag", "options",
        "system_prompt_sha256", "timestamp", "git_commit_sha", "git_dirty",
        "corpus_sha256", "latency_ms", "error", "retrieval",
    }
    for a in answers:
        missing = required - a.keys()
        assert not missing, f"missing fields in answer record: {missing}"
    # retrieval reserved as null this stage.
    assert all(a["retrieval"] is None for a in answers)


def test_refs_in_answer_are_extracted_verbatim_no_filtering(wired_run):
    """refs_in_answer is pure extraction: whatever parse_references
    finds in the raw answer, serialised. No dedup, no ranking, no filter."""
    questions, runs_dir, db, sysprompt, _ = wired_run
    out = ev.run(
        model="m", questions_path=questions, runs_dir=runs_dir,
        options={}, timeout_s=5.0, db_path=db, system_prompt_path=sysprompt,
    )
    answers = [r for r in _read_jsonl(out) if r["type"] == "answer"]
    q3 = next(a for a in answers if a["question_id"] == "q3")
    # Answer is "Love is patient. See 1 Cor 13:4-7 and John 3:16."
    ref_strings = {
        (r["book"], r["chapter"], r["verse"], r["end_verse"])
        for r in q3["refs_in_answer"]
    }
    assert ("1 Corinthians", 13, 4, 7) in ref_strings
    assert ("John", 3, 16, None) in ref_strings
    # Every ref carries spans for the citation checker.
    assert all("start" in r and "end" in r for r in q3["refs_in_answer"])


def test_run_records_per_question_error_and_continues(wired_run):
    questions, runs_dir, db, sysprompt, _ = wired_run
    out = ev.run(
        model="stub-model", questions_path=questions, runs_dir=runs_dir,
        options={"temperature": 0.0}, timeout_s=5.0,
        db_path=db, system_prompt_path=sysprompt,
    )
    answers = [r for r in _read_jsonl(out) if r["type"] == "answer"]
    q2 = next(a for a in answers if a["question_id"] == "q2")
    assert q2["error"] and "TimeoutError" in q2["error"]
    assert q2["answer"] is None
    assert q2["refs_in_answer"] == []
    q3 = next(a for a in answers if a["question_id"] == "q3")
    assert q3["error"] is None
    assert q3["answer"] is not None


def test_run_refuses_empty_questions_file(tmp_path, monkeypatch):
    """Stage 3 Task 5: empty questions.jsonl must refuse rather than
    silently succeed. Placeholder questions would poison the baseline."""
    questions = tmp_path / "questions.jsonl"
    questions.write_text("")
    sysprompt = tmp_path / "system.txt"
    sysprompt.write_text("stub\n")
    monkeypatch.setattr(ev.ollama, "Client", lambda **_: _StubClient())
    with pytest.raises(ev.EmptyQuestionsError):
        ev.run(
            model="m", questions_path=questions, runs_dir=tmp_path / "runs",
            options={}, timeout_s=5.0, db_path=tmp_path / "nope.db",
            system_prompt_path=sysprompt,
        )


def test_main_returns_nonzero_on_empty_questions(tmp_path, monkeypatch):
    """The CLI must also propagate the refusal as a non-zero exit code."""
    questions = tmp_path / "questions.jsonl"
    questions.write_text("")
    sysprompt = tmp_path / "system.txt"
    sysprompt.write_text("stub\n")
    monkeypatch.setattr(ev.ollama, "Client", lambda **_: _StubClient())
    rc = ev.main([
        "--model", "m",
        "--questions", str(questions),
        "--runs-dir", str(tmp_path / "runs"),
        "--db-path", str(tmp_path / "nope.db"),
        "--system-prompt", str(sysprompt),
    ])
    assert rc == 4


def test_run_never_overwrites_existing_run_file(wired_run, monkeypatch):
    questions, runs_dir, db, sysprompt, _ = wired_run
    out1 = ev.run(
        model="m", questions_path=questions, runs_dir=runs_dir,
        options={}, timeout_s=5.0, db_path=db, system_prompt_path=sysprompt,
    )
    import datetime as dt
    fixed_now = dt.datetime.now(dt.timezone.utc)
    class _FixedDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.replace(tzinfo=tz) if tz else fixed_now
    monkeypatch.setattr(ev.dt, "datetime", _FixedDT)

    out2 = ev.run(
        model="m", questions_path=questions, runs_dir=runs_dir,
        options={}, timeout_s=5.0, db_path=db, system_prompt_path=sysprompt,
    )
    out3 = ev.run(
        model="m", questions_path=questions, runs_dir=runs_dir,
        options={}, timeout_s=5.0, db_path=db, system_prompt_path=sysprompt,
    )
    assert out1 != out2 != out3
    assert out2 != out1
    assert out3 != out2
    assert out1.exists() and out2.exists() and out3.exists()
