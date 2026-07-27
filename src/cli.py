"""Interactive Shepherd CLI over the shared pipeline.

Two modes:
    python -m src.cli --mode grounded                    # REPL
    python -m src.cli --mode baseline "What is love?"    # one-shot

Wraps `src.pipeline.answer_question` with no extra logic. Prints the
answer, retrieved passages (grounded only), and a provenance footer.
Abstentions are rendered as an honest plain-language message.

NEVER writes into `data/eval/runs/` — this path is for interactive use.
Every eval-preserving decision (deterministic sampling, corpus hash,
prompt hash) lives in `run_eval.py`; the CLI is a UI, not a data source.
"""
from __future__ import annotations

import argparse
import sys

import ollama

from src.eval.run_eval import (
    BASELINE_SYSTEM_PROMPT,
    DEFAULT_DB,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    GROUNDED_SYSTEM_PROMPT,
    load_system_prompt,
    make_ollama_caller,
    read_corpus_sha256,
    _read_index_version,
)
from src.pipeline import (
    DEFAULT_ABSTENTION_THRESHOLD,
    DEFAULT_CONTEXT,
    DEFAULT_K,
    MODE_BASELINE,
    MODE_GROUNDED,
    answer_question,
)


def _render(result, *, mode: str, model: str,
             prompt_path, prompt_sha: str, corpus_sha: str | None) -> str:
    lines: list[str] = []
    if result.error:
        lines.append(f"[error] {result.error}")
        return "\n".join(lines)
    if result.abstained:
        reason = (result.retrieval or {}).get("abstention_reason") or "unknown"
        lines.append("[refused]")
        lines.append(
            "Shepherd is declining to answer rather than guess: "
            f"{reason}"
        )
        return "\n".join(lines)
    lines.append("[answer]")
    lines.append(result.answer or "")
    if result.passages:
        lines.append("")
        lines.append("[retrieved passages]")
        for p in result.passages:
            lines.append(f"  {p.rank}. [{p.reference}] {p.text}")
    if result.refs_in_answer:
        refs = ", ".join(
            r.get("book", "?") + " " + str(r.get("chapter", "?"))
            + (f":{r['verse']}" if r.get("verse") else "")
            for r in result.refs_in_answer
        )
        lines.append("")
        lines.append(f"[references extracted from answer] {refs}")
    lines.append("")
    lines.append(
        f"[provenance] mode={mode} model={model} "
        f"prompt={prompt_path.name} (sha256 {prompt_sha[:12]}…) "
        f"corpus_sha={corpus_sha[:12] + '…' if corpus_sha else 'missing'}"
    )
    return "\n".join(lines)


def _ask(question: str, *, mode: str, model: str, host: str | None,
         timeout_s: float) -> None:
    prompt_path = GROUNDED_SYSTEM_PROMPT if mode == MODE_GROUNDED else BASELINE_SYSTEM_PROMPT
    system_prompt, prompt_sha = load_system_prompt(prompt_path)
    client = ollama.Client(host=host, timeout=timeout_s)
    options = {"temperature": 0.0, "top_p": 1.0, "seed": 1}
    call_model = make_ollama_caller(client, model, options)
    index_version = _read_index_version(DEFAULT_DB) if mode == MODE_GROUNDED else None
    corpus_sha = read_corpus_sha256(DEFAULT_DB)
    result = answer_question(
        question, mode=mode, system_prompt=system_prompt,
        call_model=call_model,
        k=DEFAULT_K, context=DEFAULT_CONTEXT,
        threshold=DEFAULT_ABSTENTION_THRESHOLD,
        db_path=DEFAULT_DB, index_version=index_version,
    )
    print(_render(result, mode=mode, model=model,
                    prompt_path=prompt_path, prompt_sha=prompt_sha,
                    corpus_sha=corpus_sha))


def _repl(mode: str, model: str, host: str | None, timeout_s: float) -> None:
    banner = (
        f"Shepherd CLI — mode={mode} model={model}. "
        f"Blank line or Ctrl-D to quit.\n"
    )
    print(banner)
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            return
        try:
            _ask(question, mode=mode, model=model, host=host, timeout_s=timeout_s)
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}", file=sys.stderr)
        print()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Interactive Shepherd CLI.")
    p.add_argument("question", nargs="?", default=None,
                   help="One-shot question. Omit for REPL mode.")
    p.add_argument("--mode", choices=(MODE_BASELINE, MODE_GROUNDED),
                   default=MODE_GROUNDED)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--host", default=None, help="Ollama host URL")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = p.parse_args(argv)

    if args.question:
        _ask(args.question, mode=args.mode, model=args.model,
             host=args.host, timeout_s=args.timeout)
        return 0
    _repl(args.mode, args.model, args.host, args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
