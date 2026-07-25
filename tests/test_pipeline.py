"""Tests for src.pipeline.answer_question.

Covers both modes, abstention paths, error handling, and unknown-mode
rejection. The model call is always mocked — no Ollama needed."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline import (
    DEFAULT_ABSTENTION_THRESHOLD,
    MODE_BASELINE,
    MODE_GROUNDED,
    PipelineOutput,
    UnknownModeError,
    _decide_abstention,
    answer_question,
    build_grounded_user_message,
)
from src.retrieval import RetrievedPassage


class _RecordingModel:
    """Capture every (system, user) pair the pipeline sends."""

    def __init__(self, answer: str = "answer text"):
        self.calls: list[tuple[str, str]] = []
        self._answer = answer

    def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._answer


class _ErrorModel:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        raise self._exc


def _passage(
    reference: str, book: str, chapter: int, verse: int,
    text: str, score: float, rank: int,
) -> RetrievedPassage:
    return RetrievedPassage(
        reference=reference, book=book, chapter=chapter,
        verse_start=verse, verse_end=verse,
        text=text, score=score, rank=rank,
    )


# ---------------------------------------------------------------------------
# Baseline mode
# ---------------------------------------------------------------------------

def test_baseline_calls_model_once_and_returns_answer():
    model = _RecordingModel(answer="Love is patient. See 1 Cor 13:4-7.")
    out = answer_question(
        "What is love?",
        mode=MODE_BASELINE,
        system_prompt="You are Shepherd.",
        call_model=model,
    )
    assert len(model.calls) == 1
    system, user = model.calls[0]
    assert system == "You are Shepherd."
    assert user == "What is love?"
    assert out.answer == "Love is patient. See 1 Cor 13:4-7."
    assert out.retrieval is None
    assert out.abstained is False
    assert out.error is None


def test_baseline_extracts_refs_from_answer():
    model = _RecordingModel(answer="See John 3:16 and 1 Corinthians 13:4-7.")
    out = answer_question(
        "cite something",
        mode=MODE_BASELINE,
        system_prompt="stub",
        call_model=model,
    )
    refs = out.refs_in_answer
    assert len(refs) == 2
    assert refs[0]["book"] == "John" and refs[0]["chapter"] == 3
    assert refs[1]["book"] == "1 Corinthians"


def test_baseline_records_model_exception_as_error():
    model = _ErrorModel(TimeoutError("simulated timeout"))
    out = answer_question(
        "q", mode=MODE_BASELINE, system_prompt="stub", call_model=model,
    )
    assert out.answer is None
    assert out.refs_in_answer == []
    assert out.retrieval is None
    assert out.abstained is False
    assert out.error == "TimeoutError: simulated timeout"


def test_baseline_prompt_text_records_system_and_user_verbatim():
    model = _RecordingModel(answer="x")
    out = answer_question(
        "the question",
        mode=MODE_BASELINE,
        system_prompt="the system",
        call_model=model,
    )
    assert "[system]\nthe system" in out.prompt
    assert "[user]\nthe question" in out.prompt


# ---------------------------------------------------------------------------
# Grounded mode — normal path
# ---------------------------------------------------------------------------

def test_grounded_calls_retrieve_and_model_with_passages():
    model = _RecordingModel(answer="grounded answer using John 3:16.")
    passages = [
        _passage("John 3:16", "John", 3, 16,
                 "For God so loved the world...", -12.0, 1),
    ]
    def stub_retrieve(query, **kwargs):
        assert query == "who is Jesus"
        return passages
    out = answer_question(
        "who is Jesus",
        mode=MODE_GROUNDED,
        system_prompt="Ground your answer.",
        call_model=model,
        retrieve_fn=stub_retrieve,
    )
    assert len(model.calls) == 1
    system, user = model.calls[0]
    assert system == "Ground your answer."
    assert "[John 3:16]" in user
    assert "For God so loved the world..." in user
    assert "Question: who is Jesus" in user
    assert out.abstained is False
    assert out.answer == "grounded answer using John 3:16."
    assert out.retrieval is not None
    assert out.retrieval["mode"] == "grounded"
    assert out.retrieval["abstained"] is False
    assert out.retrieval["abstention_reason"] is None
    assert len(out.retrieval["passages"]) == 1
    assert out.retrieval["passages"][0]["reference"] == "John 3:16"


def test_grounded_populates_parameters_in_retrieval():
    model = _RecordingModel(answer="a")
    passages = [_passage("John 3:16", "John", 3, 16, "x", -10.0, 1)]
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model,
        k=7, context=2, threshold=-4.0,
        retrieve_fn=lambda q, **_: passages,
        index_version="deadbeef" * 8,
    )
    params = out.retrieval["parameters"]
    assert params == {"k": 7, "context": 2, "threshold": -4.0,
                       "index_version": "deadbeef" * 8}


def test_grounded_extracts_refs_from_answer():
    model = _RecordingModel(answer="From John 3:16 we see love.")
    passages = [_passage("John 3:16", "John", 3, 16, "x", -12.0, 1)]
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model, retrieve_fn=lambda q, **_: passages,
    )
    assert len(out.refs_in_answer) == 1
    assert out.refs_in_answer[0]["book"] == "John"


# ---------------------------------------------------------------------------
# Grounded abstention
# ---------------------------------------------------------------------------

def test_grounded_abstains_on_empty_passages_and_never_calls_model():
    model = _RecordingModel(answer="should not be called")
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model, retrieve_fn=lambda q, **_: [],
    )
    assert model.calls == []
    assert out.abstained is True
    assert out.answer is None
    assert out.refs_in_answer == []
    assert out.retrieval["abstained"] is True
    assert "no passages retrieved" in out.retrieval["abstention_reason"]
    assert out.retrieval["passages"] == []
    assert out.error is None


def test_grounded_abstains_on_weak_top_score():
    """Top score -1.0 is well above the default threshold (-5.0), so
    the pipeline abstains without calling the model."""
    model = _RecordingModel(answer="should not be called")
    passages = [_passage("Genesis 1:1", "Genesis", 1, 1, "In the beginning...",
                          -1.0, 1)]
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model, retrieve_fn=lambda q, **_: passages,
    )
    assert model.calls == []
    assert out.abstained is True
    assert out.retrieval["abstention_reason"].startswith(
        "top passage score"
    )


def test_grounded_does_not_abstain_when_score_stronger_than_threshold():
    model = _RecordingModel(answer="ok")
    passages = [_passage("Psalms 23:1", "Psalms", 23, 1, "The LORD is my shepherd",
                          -20.0, 1)]
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model, retrieve_fn=lambda q, **_: passages,
        threshold=-5.0,
    )
    assert model.calls, "grounded call should have run"
    assert out.abstained is False


def test_grounded_direct_lookup_sentinel_passes_threshold():
    """Direct-lookup passages carry a large-negative sentinel score so
    they sit below any BM25 abstention threshold. An explicit user
    reference must not be gated by keyword-scoring heuristics."""
    model = _RecordingModel(answer="ok")
    passages = [_passage("John 3:16", "John", 3, 16,
                          "For God so loved...", -1e9, 1)]  # direct-lookup sentinel
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model, retrieve_fn=lambda q, **_: passages,
        threshold=-5.0,
    )
    assert out.abstained is False, (
        "Direct-lookup passages must never trigger abstention — the "
        "user was explicit."
    )
    assert model.calls, "model should have been called"


# ---------------------------------------------------------------------------
# Grounded — retrieval / model errors
# ---------------------------------------------------------------------------

def test_grounded_retrieve_exception_recorded_as_error_no_abstain():
    model = _RecordingModel(answer="unused")
    def boom(query, **kwargs):
        raise RuntimeError("index broken")
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model, retrieve_fn=boom,
    )
    assert model.calls == []
    assert out.answer is None
    assert out.error == "RuntimeError: index broken"
    assert out.abstained is False
    # retrieval is None here because we don't know k/context/threshold
    # applied to a failed retrieval — but the error field carries the
    # diagnosis. See the pipeline docstring.
    assert out.retrieval is None


def test_grounded_model_exception_keeps_retrieval_populated():
    passages = [_passage("John 3:16", "John", 3, 16, "x", -12.0, 1)]
    model = _ErrorModel(TimeoutError("model slow"))
    out = answer_question(
        "q", mode=MODE_GROUNDED, system_prompt="s",
        call_model=model, retrieve_fn=lambda q, **_: passages,
    )
    assert model.calls == 1
    assert out.answer is None
    assert out.error == "TimeoutError: model slow"
    assert out.abstained is False
    # Passages that WERE retrieved are still surfaced so the run file
    # records what the model would have seen.
    assert out.retrieval is not None
    assert len(out.retrieval["passages"]) == 1


# ---------------------------------------------------------------------------
# Unknown mode
# ---------------------------------------------------------------------------

def test_unknown_mode_raises():
    with pytest.raises(UnknownModeError):
        answer_question(
            "q", mode="turbo", system_prompt="s",
            call_model=_RecordingModel(),
        )


# ---------------------------------------------------------------------------
# Prompt assembly helper
# ---------------------------------------------------------------------------

def test_build_grounded_user_message_labels_each_passage():
    passages = [
        _passage("John 3:16", "John", 3, 16, "For God so loved...", -12.0, 1),
        _passage("Romans 8:28", "Romans", 8, 28, "And we know...", -10.0, 2),
    ]
    msg = build_grounded_user_message("Why does God allow suffering?", passages)
    assert "[John 3:16] For God so loved..." in msg
    assert "[Romans 8:28] And we know..." in msg
    assert "Question: Why does God allow suffering?" in msg


# ---------------------------------------------------------------------------
# _decide_abstention (unit level)
# ---------------------------------------------------------------------------

def test_decide_abstention_returns_none_when_top_beats_threshold():
    passages = [_passage("Psalms 23:1", "Psalms", 23, 1, "x", -20.0, 1)]
    assert _decide_abstention(passages, threshold=-5.0) is None


def test_decide_abstention_returns_reason_when_top_above_threshold():
    passages = [_passage("Genesis 1:1", "Genesis", 1, 1, "x", -3.0, 1)]
    reason = _decide_abstention(passages, threshold=-5.0)
    assert reason and "above abstention threshold" in reason


def test_decide_abstention_empty_returns_reason():
    reason = _decide_abstention([], threshold=-5.0)
    assert reason == "no passages retrieved for the query"
