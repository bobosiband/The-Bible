"""Tests for src.web.server.

Uses FastAPI's TestClient — no real HTTP server, no Ollama, no
network. The model call is replaced by a callable-returning-iterator
stub injected via AppState; the retrieval function is replaced too
where needed. Every test asserts against the SSE frame shape the
brief locks down.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.pipeline import (
    DEFAULT_ABSTENTION_THRESHOLD,
    DEFAULT_CONTEXT,
    DEFAULT_K,
)
from src.retrieval import RetrievedPassage
from src.web import server as srv

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "data" / "eval" / "runs"


def _mk_state(*, model_stream, retrieve_fn=None) -> srv.AppState:
    return srv.AppState(
        model="stub-model",
        model_stream=model_stream,
        db_path=None,
        corpus_sha256="c" * 64,
        index_version="i" * 64,
        baseline_prompt="baseline system",
        baseline_prompt_sha="a" * 64,
        grounded_prompt="grounded system",
        grounded_prompt_sha="b" * 64,
        git_sha="g" * 40,
        git_dirty=False,
        abstention_threshold=DEFAULT_ABSTENTION_THRESHOLD,
        retrieval_k=DEFAULT_K,
        retrieval_context=DEFAULT_CONTEXT,
    )


def _parse_sse(body: bytes) -> list[tuple[str, dict]]:
    """Parse SSE bytes into (event, data-dict) tuples."""
    events: list[tuple[str, dict]] = []
    text = body.decode("utf-8")
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        event = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event is not None and data is not None:
            events.append((event, data))
    return events


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_index_returns_html(tmp_path, monkeypatch):
    """The index page is loaded from src/web/static/index.html when it
    exists. If the file is missing (before Part E's second commit), the
    server returns a 500 rather than serving garbage."""
    state = _mk_state(model_stream=lambda s, u: iter([]))
    app = srv.create_app(state)
    client = TestClient(app)
    r = client.get("/")
    # We don't have index.html in this test's tree yet during the
    # server-only commit — but once it lands the test asserts the
    # content-type is HTML. Cover both branches here.
    if srv.INDEX_HTML.exists():
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
    else:
        assert r.status_code == 500


# ---------------------------------------------------------------------------
# GET /meta
# ---------------------------------------------------------------------------

def test_meta_returns_documented_fields():
    state = _mk_state(model_stream=lambda s, u: iter([]))
    app = srv.create_app(state)
    client = TestClient(app)
    r = client.get("/meta")
    assert r.status_code == 200
    body = r.json()
    for key in ("model", "prompts", "corpus_sha256", "git_sha",
                 "abstention_threshold", "retrieval_k", "retrieval_context",
                 "index_version"):
        assert key in body, f"missing meta key: {key}"
    assert "baseline" in body["prompts"]
    assert "grounded" in body["prompts"]
    assert body["prompts"]["baseline"]["sha256"] == "a" * 64
    assert body["prompts"]["grounded"]["sha256"] == "b" * 64


# ---------------------------------------------------------------------------
# POST /ask — grounded, non-abstain
# ---------------------------------------------------------------------------

def test_ask_grounded_emits_passages_then_tokens_then_done(monkeypatch):
    passage = RetrievedPassage(
        reference="John 3:16", book="John", chapter=3,
        verse_start=16, verse_end=16,
        text="For God so loved the world…",
        score=-12.0, rank=1,
    )
    # Stub the pipeline's retrieve function so we never touch the corpus.
    monkeypatch.setattr(srv, "prepare_grounded", _grounded_stub([passage]))
    tokens_emitted = ["Hello ", "world", "."]
    state = _mk_state(model_stream=lambda s, u: iter(tokens_emitted))
    client = TestClient(srv.create_app(state))
    with client.stream("POST", "/ask",
                        json={"question": "q", "mode": "grounded"}) as r:
        body = b"".join(r.iter_bytes())
    events = _parse_sse(body)
    names = [e[0] for e in events]
    assert names[0] == "passages"
    assert "token" in names
    assert names[-1] == "done"
    passages_ev = events[0][1]
    assert len(passages_ev["passages"]) == 1
    assert passages_ev["passages"][0]["reference"] == "John 3:16"
    done_ev = events[-1][1]
    assert done_ev["answer"] == "Hello world."
    assert done_ev["provenance"]["mode"] == "grounded"


def _grounded_stub(passages):
    """Return a prepare_grounded replacement that yields the given passages
    (non-abstain path). Bypasses the real retrieve + threshold logic
    since we're testing the server, not the pipeline."""
    from src.pipeline import PreparedCall, GroundedPreparation, _retrieval_object, MODE_GROUNDED, build_grounded_user_message, _grounded_prompt_text
    def _fake(question, *, system_prompt, k, context, threshold, db_path,
               index_version, **_):
        user = build_grounded_user_message(question, passages)
        prompt = _grounded_prompt_text(system_prompt, user)
        retrieval = _retrieval_object(
            mode=MODE_GROUNDED, passages=passages,
            k=k, context=context, threshold=threshold,
            abstained=False, abstention_reason=None,
            index_version=index_version,
        )
        return GroundedPreparation(
            call=PreparedCall(
                system_prompt=system_prompt, user_message=user,
                prompt_text=prompt, retrieval=retrieval,
                passages=list(passages),
            ),
            output=None,
        )
    return _fake


# ---------------------------------------------------------------------------
# POST /ask — grounded abstention (model NOT called)
# ---------------------------------------------------------------------------

def test_ask_grounded_abstention_emits_no_tokens_and_never_calls_model(monkeypatch):
    """The pipeline decides to abstain; the server must emit `passages`
    with abstention info and `done` with answer=null. The model_stream
    must NEVER be called."""
    from src.pipeline import GroundedPreparation, PipelineOutput, _retrieval_object, MODE_GROUNDED
    def _abstain(question, *, system_prompt, k, context, threshold, db_path,
                  index_version, **_):
        retrieval = _retrieval_object(
            mode=MODE_GROUNDED, passages=[],
            k=k, context=context, threshold=threshold,
            abstained=True,
            abstention_reason="no passages retrieved for the query",
            index_version=index_version,
        )
        return GroundedPreparation(
            call=None,
            output=PipelineOutput(
                prompt="prompt", answer=None, refs_in_answer=[],
                retrieval=retrieval, abstained=True, error=None,
                passages=[],
            ),
        )
    monkeypatch.setattr(srv, "prepare_grounded", _abstain)
    stream_call_count = {"n": 0}
    def _model_stream(s, u):
        stream_call_count["n"] += 1
        yield "should not be sent"
    state = _mk_state(model_stream=_model_stream)
    client = TestClient(srv.create_app(state))
    with client.stream("POST", "/ask",
                        json={"question": "obscure", "mode": "grounded"}) as r:
        body = b"".join(r.iter_bytes())
    events = _parse_sse(body)
    names = [e[0] for e in events]
    assert names == ["passages", "done"]  # no `token`
    passages_ev = events[0][1]
    assert passages_ev["abstained"] is True
    assert "no passages" in passages_ev["abstention_reason"]
    done_ev = events[1][1]
    assert done_ev["answer"] is None
    assert done_ev["abstained"] is True
    assert stream_call_count["n"] == 0


# ---------------------------------------------------------------------------
# POST /ask — baseline (no passages, tokens present)
# ---------------------------------------------------------------------------

def test_ask_baseline_emits_empty_passages_then_tokens():
    state = _mk_state(model_stream=lambda s, u: iter(["one ", "two"]))
    client = TestClient(srv.create_app(state))
    with client.stream("POST", "/ask",
                        json={"question": "q", "mode": "baseline"}) as r:
        body = b"".join(r.iter_bytes())
    events = _parse_sse(body)
    names = [e[0] for e in events]
    assert names[0] == "passages"
    assert events[0][1] == {"passages": [], "mode": "baseline"}
    assert names[-1] == "done"
    assert events[-1][1]["answer"] == "one two"
    assert events[-1][1]["provenance"]["mode"] == "baseline"
    assert events[-1][1]["provenance"]["retrieval_params"] is None


# ---------------------------------------------------------------------------
# POST /ask — malformed request → 4xx, not 500
# ---------------------------------------------------------------------------

def test_ask_missing_question_returns_4xx():
    state = _mk_state(model_stream=lambda s, u: iter([]))
    client = TestClient(srv.create_app(state))
    r = client.post("/ask", json={"mode": "baseline"})
    assert 400 <= r.status_code < 500


def test_ask_invalid_mode_returns_4xx():
    state = _mk_state(model_stream=lambda s, u: iter([]))
    client = TestClient(srv.create_app(state))
    r = client.post("/ask", json={"question": "q", "mode": "turbo"})
    assert 400 <= r.status_code < 500


def test_ask_empty_question_returns_4xx():
    state = _mk_state(model_stream=lambda s, u: iter([]))
    client = TestClient(srv.create_app(state))
    r = client.post("/ask", json={"question": "", "mode": "baseline"})
    assert 400 <= r.status_code < 500


# ---------------------------------------------------------------------------
# Model errors surface as a single `error` event, not a hung stream
# ---------------------------------------------------------------------------

def test_ask_model_error_emits_error_event_and_closes(monkeypatch):
    def _boom(system, user):
        yield "one "
        raise TimeoutError("model timeout")
    state = _mk_state(model_stream=_boom)
    client = TestClient(srv.create_app(state))
    with client.stream("POST", "/ask",
                        json={"question": "q", "mode": "baseline"}) as r:
        body = b"".join(r.iter_bytes())
    events = _parse_sse(body)
    names = [e[0] for e in events]
    assert names[0] == "passages"
    assert "token" in names
    assert names[-1] == "error"
    err = events[-1][1]
    assert err["stage"] == "model"
    assert "TimeoutError" in err["error"]


def test_ask_retrieval_error_emits_error_event(monkeypatch):
    def _boom(question, **kwargs):
        raise RuntimeError("index broken")
    # Patch the pipeline's retrieve entry point.
    from src.pipeline import GroundedPreparation, PipelineOutput
    def _prep_that_errors(question, *, system_prompt, **_):
        # The real prepare_grounded catches retrieval exceptions and
        # returns them via output.error — do the same here.
        return GroundedPreparation(
            call=None,
            output=PipelineOutput(
                prompt="p", answer=None, refs_in_answer=[],
                retrieval=None, abstained=False,
                error="RuntimeError: index broken",
                passages=[],
            ),
        )
    monkeypatch.setattr(srv, "prepare_grounded", _prep_that_errors)
    state = _mk_state(model_stream=lambda s, u: iter([]))
    client = TestClient(srv.create_app(state))
    with client.stream("POST", "/ask",
                        json={"question": "q", "mode": "grounded"}) as r:
        body = b"".join(r.iter_bytes())
    events = _parse_sse(body)
    names = [e[0] for e in events]
    assert names == ["error"]
    assert events[0][1]["stage"] == "retrieval"


# ---------------------------------------------------------------------------
# The eval-runs directory must be untouched by any /ask request
# ---------------------------------------------------------------------------

def test_ask_never_writes_to_eval_runs():
    before = sorted(os.listdir(RUNS_DIR)) if RUNS_DIR.exists() else []
    state = _mk_state(model_stream=lambda s, u: iter(["ok"]))
    client = TestClient(srv.create_app(state))
    for _ in range(3):
        with client.stream("POST", "/ask",
                            json={"question": "q", "mode": "baseline"}) as r:
            list(r.iter_bytes())
    after = sorted(os.listdir(RUNS_DIR)) if RUNS_DIR.exists() else []
    assert before == after


# ---------------------------------------------------------------------------
# sse_frame helper
# ---------------------------------------------------------------------------

def test_sse_frame_format():
    frame = srv.sse_frame("token", {"text": "hi"})
    assert frame == b'event: token\ndata: {"text": "hi"}\n\n'


def test_sse_frame_unicode_preserved():
    frame = srv.sse_frame("token", {"text": "café"})
    assert b"caf\xc3\xa9" in frame  # UTF-8, no \uNNNN escape
