"""Run every question in data/eval/questions.jsonl through a local Ollama
model and save the answers to a timestamped run file.

Prereq: Ollama running locally (`ollama serve`) with the target model pulled
(e.g. `ollama pull qwen2.5:3b`).

Usage:
    python -m src.eval.run_eval                    # default model
    python -m src.eval.run_eval --model qwen2.5:3b
    python -m src.eval.run_eval --questions path/to/other.jsonl
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import ollama

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "eval" / "questions.jsonl"
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "eval" / "runs"
DEFAULT_MODEL = "qwen2.5:3b"

# The system prompt is deliberately small and stable so eval runs are
# comparable across model versions. Tuning this changes what "the eval
# measured" means, so treat it as part of the eval methodology.
SYSTEM_PROMPT = (
    "You are Shepherd, an offline Bible-study assistant. "
    "Answer the user's question clearly and cite the Bible passages you rely on "
    "using the form 'Book Chapter:Verse' (e.g. 'John 3:16'). "
    "If you are not confident, say so instead of guessing."
)


def load_questions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    questions = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            q = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{lineno}: invalid JSON — {e}")
        if "question" not in q:
            raise SystemExit(f"{path}:{lineno}: missing 'question' field")
        q.setdefault("id", f"q{lineno:03d}")
        questions.append(q)
    return questions


def ask(model: str, question: str) -> tuple[str, int]:
    """Send one question to the local Ollama model and return (answer, elapsed_ms)."""
    started = time.monotonic()
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    # `ollama.chat` returns an object with attribute access; be defensive
    # in case a future version returns a plain dict.
    msg = getattr(response, "message", None) or response.get("message")
    content = getattr(msg, "content", None) or msg.get("content")
    return content, elapsed_ms


def run(model: str, questions_path: Path, runs_dir: Path) -> Path:
    questions = load_questions(questions_path)
    if not questions:
        print(f"[warn] {questions_path} has no questions; nothing to run.")
        sys.exit(0)

    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = runs_dir / f"{stamp}.jsonl"

    print(f"[run ] model={model} questions={len(questions)} → {out_path}")
    with out_path.open("w") as out:
        for q in questions:
            print(f"  - {q['id']}: {q['question'][:80]}")
            answer, elapsed_ms = ask(model, q["question"])
            record = {
                "id": q["id"],
                "question": q["question"],
                "model": model,
                "answer": answer,
                "elapsed_ms": elapsed_ms,
            }
            if "expected_refs" in q:
                record["expected_refs"] = q["expected_refs"]
            out.write(json.dumps(record) + "\n")
            out.flush()
    print(f"[done] wrote {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag")
    p.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = p.parse_args(argv)
    run(args.model, args.questions, args.runs_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
