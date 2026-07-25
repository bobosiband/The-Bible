"""Question-answering pipeline: (question, mode) → answer record fields.

Two modes:

- **baseline**: use the v1 system prompt and call the model directly.
  No retrieval, `retrieval` stays `null`. This measures fabrication
  with nothing in the prompt discouraging it — the number the grounded
  mode is compared against.

- **grounded**: retrieve BSB passages via `src.retrieval.retrieve`;
  if the top passage's score is above the abstention threshold, call
  the model with the v2 prompt and the passages inline; otherwise
  emit an abstention record and do NOT call the model.

Abstention is a first-class outcome. Sending an empty context to the
model would produce ungrounded text against a prompt that told it to
ground its answer — that's a self-inflicted fabrication case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.corpus.references import parse_references, reference_to_dict
from src.retrieval import RetrievedPassage, retrieve

# Chosen from measured BM25 top-scores on the real BSB corpus:
# - Very common single words ("the", "God", "Lord", "a") score between
#   0 and -4 — they are semantic noise, and answering with them would
#   ground the model in nothing useful.
# - Distinctive terms and phrases ("mercy", "salvation", "resurrection",
#   "shepherd I shall not want") score below -5 and are genuinely
#   informative.
# Keeping the threshold at -5.0 abstains on the noise band without
# rejecting real hits. See Stage 4 report for the full distribution.
DEFAULT_ABSTENTION_THRESHOLD = -5.0
DEFAULT_K = 5
DEFAULT_CONTEXT = 0

MODE_BASELINE = "baseline"
MODE_GROUNDED = "grounded"
_ALLOWED_MODES = frozenset({MODE_BASELINE, MODE_GROUNDED})


class UnknownModeError(ValueError):
    """Raised for `mode` values other than 'baseline' or 'grounded'."""


@dataclass(frozen=True)
class PipelineOutput:
    """The question-dependent parts of an `answer` record.

    The caller (run_eval) folds these into a full record together with
    per-run metadata (model, git SHA, corpus SHA, ...).
    """
    prompt: str
    answer: str | None
    refs_in_answer: list[dict]
    retrieval: dict | None
    abstained: bool
    error: str | None = None
    # Passages kept as objects so tests can assert on them without
    # re-deserialising the dict form.
    passages: list[RetrievedPassage] = field(default_factory=list)


def _format_passages(passages: list[RetrievedPassage]) -> str:
    """Render passages as a labelled block for the model. Each passage
    is prefixed with its canonical reference in square brackets so the
    model can cite it by that exact string."""
    lines = []
    for p in passages:
        lines.append(f"[{p.reference}] {p.text}")
    return "\n\n".join(lines)


def build_grounded_user_message(question: str, passages: list[RetrievedPassage]) -> str:
    """Assemble the grounded user message: passages block + question."""
    return (
        f"Passages:\n\n{_format_passages(passages)}\n\n"
        f"Question: {question}"
    )


def _serialise_passage(p: RetrievedPassage) -> dict:
    return {
        "reference": p.reference,
        "book": p.book,
        "chapter": p.chapter,
        "verse_start": p.verse_start,
        "verse_end": p.verse_end,
        "end_chapter": p.end_chapter,
        "text": p.text,
        "score": p.score,
        "rank": p.rank,
    }


def _retrieval_object(
    mode: str,
    passages: list[RetrievedPassage],
    k: int,
    context: int,
    threshold: float,
    abstained: bool,
    abstention_reason: str | None,
    index_version: str | None,
) -> dict:
    return {
        "mode": mode,
        "parameters": {
            "k": k,
            "context": context,
            "threshold": threshold,
            "index_version": index_version,
        },
        "passages": [_serialise_passage(p) for p in passages],
        "abstained": abstained,
        "abstention_reason": abstention_reason,
    }


def _decide_abstention(
    passages: list[RetrievedPassage], threshold: float,
) -> str | None:
    """Return an abstention reason string, or None if the pipeline should
    proceed to call the model. Kept separate so tests can drive it in
    isolation and so a future threshold policy is a one-function change."""
    if not passages:
        return "no passages retrieved for the query"
    top = passages[0]
    if top.score > threshold:
        return (
            f"top passage score {top.score:.3f} above abstention threshold "
            f"{threshold:.3f} (higher = weaker BM25 match)"
        )
    return None


def _baseline_prompt_text(system_prompt: str, question: str) -> str:
    """Serialised prompt for provenance. Same convention run_eval used
    pre-Stage-4: `[system]\\n...\\n\\n[user]\\n...`."""
    return f"[system]\n{system_prompt}\n\n[user]\n{question}"


def _grounded_prompt_text(system_prompt: str, user_msg: str) -> str:
    return f"[system]\n{system_prompt}\n\n[user]\n{user_msg}"


def answer_question(
    question: str,
    *,
    mode: str,
    system_prompt: str,
    call_model: Callable[[str, str], str],
    k: int = DEFAULT_K,
    context: int = DEFAULT_CONTEXT,
    threshold: float = DEFAULT_ABSTENTION_THRESHOLD,
    db_path: Path | None = None,
    translation: str = "BSB",
    index_version: str | None = None,
    retrieve_fn: Callable[..., list[RetrievedPassage]] | None = None,
) -> PipelineOutput:
    """Produce the answer-record fields for one question in one mode.

    Contract:
    - `call_model(system, user)` returns the raw model text on success.
      Any exception the callable raises is caught and recorded as the
      `error` field; the record's `answer` is set to `None`. This
      matches run_eval's existing 'never abort a whole run for one
      bad question' behaviour.
    - Baseline: `retrieval` is `None`, `abstained` is `False`, the
      user message is the question verbatim.
    - Grounded: retrieval is called; if the top passage's score is
      above `threshold` (i.e. not negative enough) or no passages are
      returned, the pipeline abstains — `answer` is `None`,
      `abstained` is `True`, `retrieval.abstention_reason` explains,
      and `call_model` is NOT called.
    - Unknown mode → UnknownModeError.
    """
    if mode not in _ALLOWED_MODES:
        raise UnknownModeError(
            f"unknown mode {mode!r}; expected one of "
            f"{sorted(_ALLOWED_MODES)}"
        )

    if mode == MODE_BASELINE:
        prompt_text = _baseline_prompt_text(system_prompt, question)
        try:
            answer = call_model(system_prompt, question)
        except Exception as e:
            return PipelineOutput(
                prompt=prompt_text, answer=None, refs_in_answer=[],
                retrieval=None, abstained=False,
                error=f"{type(e).__name__}: {e}",
            )
        refs = [reference_to_dict(r) for r in parse_references(answer or "")]
        return PipelineOutput(
            prompt=prompt_text, answer=answer, refs_in_answer=refs,
            retrieval=None, abstained=False, error=None,
        )

    # Grounded mode.
    kwargs: dict = {"k": k, "context": context, "translation": translation}
    if db_path is not None:
        kwargs["db_path"] = db_path
    retriever = retrieve_fn or retrieve
    try:
        passages = retriever(question, **kwargs)
    except Exception as e:
        # A retrieval failure (missing DB, out-of-sync index) is a setup
        # problem — surface it as the error field, do NOT abstain (an
        # abstention record would falsely claim the corpus was OK but
        # simply had nothing relevant).
        prompt_text = _grounded_prompt_text(system_prompt, question)
        return PipelineOutput(
            prompt=prompt_text, answer=None, refs_in_answer=[],
            retrieval=None, abstained=False,
            error=f"{type(e).__name__}: {e}",
        )

    reason = _decide_abstention(passages, threshold)
    if reason is not None:
        retrieval_obj = _retrieval_object(
            mode=MODE_GROUNDED, passages=[], k=k, context=context,
            threshold=threshold, abstained=True, abstention_reason=reason,
            index_version=index_version,
        )
        # Record the prompt as it WOULD have been assembled with an
        # empty passages block; makes the run file self-describing
        # even for abstentions.
        prompt_text = _grounded_prompt_text(
            system_prompt,
            build_grounded_user_message(question, []),
        )
        return PipelineOutput(
            prompt=prompt_text, answer=None, refs_in_answer=[],
            retrieval=retrieval_obj, abstained=True, error=None,
            passages=[],
        )

    user_msg = build_grounded_user_message(question, passages)
    prompt_text = _grounded_prompt_text(system_prompt, user_msg)
    try:
        answer = call_model(system_prompt, user_msg)
    except Exception as e:
        retrieval_obj = _retrieval_object(
            mode=MODE_GROUNDED, passages=passages, k=k, context=context,
            threshold=threshold, abstained=False, abstention_reason=None,
            index_version=index_version,
        )
        return PipelineOutput(
            prompt=prompt_text, answer=None, refs_in_answer=[],
            retrieval=retrieval_obj, abstained=False,
            error=f"{type(e).__name__}: {e}",
            passages=passages,
        )
    refs = [reference_to_dict(r) for r in parse_references(answer or "")]
    retrieval_obj = _retrieval_object(
        mode=MODE_GROUNDED, passages=passages, k=k, context=context,
        threshold=threshold, abstained=False, abstention_reason=None,
        index_version=index_version,
    )
    return PipelineOutput(
        prompt=prompt_text, answer=answer, refs_in_answer=refs,
        retrieval=retrieval_obj, abstained=False, error=None,
        passages=passages,
    )
