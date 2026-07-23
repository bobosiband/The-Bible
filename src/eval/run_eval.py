"""Run every question in data/eval/questions.jsonl through a local Ollama
model and save the answers to a timestamped run file.

Prereq: Ollama running locally (`ollama serve`) with the target model pulled
(e.g. `ollama pull qwen2.5:3b`).

Usage:
    python -m src.eval.run_eval                    # default model
    python -m src.eval.run_eval --model qwen2.5:3b
    python -m src.eval.run_eval --questions path/to/other.jsonl

Every run file starts with a `run_meta` record so results can be pinned
back to (a) the exact model + sampling parameters, (b) the exact code
commit, and (c) the exact corpus bytes that were in place when the run
executed. A result you can't trace to what produced it is not evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import ollama

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "eval" / "questions.jsonl"
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "eval" / "runs"
DEFAULT_DB = REPO_ROOT / "data" / "corpus" / "bible.db"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT_S = 120.0

# The system prompt is deliberately small and stable so eval runs are
# comparable across model versions. Tuning this changes what "the eval
# measured" means, so treat it as part of the eval methodology.
SYSTEM_PROMPT = (
    "You are Shepherd, an offline Bible-study assistant. "
    "Answer the user's question clearly and cite the Bible passages you rely on "
    "using the form 'Book Chapter:Verse' (e.g. 'John 3:16'). "
    "If you are not confident, say so instead of guessing."
)


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------

def read_corpus_sha256(db_path: Path, translation: str = "BSB") -> str | None:
    """Read the locally-computed corpus SHA256 recorded by the ingest.

    Returns None if the DB or corpus_meta row is missing — we still run,
    but the run_meta will flag the missing hash.
    """
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT sha256_local FROM corpus_meta WHERE translation = ?",
                (translation,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def read_git_state(repo_root: Path = REPO_ROOT) -> tuple[str | None, bool]:
    """Return (commit_sha, is_dirty). Both None/False if not in a git repo."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, stderr=subprocess.DEVNULL,
        ).decode().strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_root, stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, False
    return sha, bool(status.strip())


def next_available_path(target: Path) -> Path:
    """Return `target` if it doesn't exist, else `target` with a `-2`, `-3`,
    ... suffix inserted before the extension. Guarantees no overwrite."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    n = 2
    while True:
        candidate = target.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# Question I/O
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------

def ask(client: ollama.Client, model: str, question: str, options: dict) -> str:
    """Send one question to the local Ollama model and return the answer text."""
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        options=options,
    )
    # `ollama.chat` returns an object with attribute access; be defensive
    # in case a future version returns a plain dict.
    msg = getattr(response, "message", None) or response.get("message")
    content = getattr(msg, "content", None) or msg.get("content")
    return content


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_run_meta(
    model: str,
    options: dict,
    timeout_s: float,
    corpus_sha256: str | None,
    git_sha: str | None,
    git_dirty: bool,
) -> dict:
    return {
        "type": "run_meta",
        "run_started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "options": options,
        "timeout_s": timeout_s,
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "corpus_sha256": corpus_sha256,
    }


def run(
    model: str,
    questions_path: Path,
    runs_dir: Path,
    options: dict,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    db_path: Path = DEFAULT_DB,
    ollama_host: str | None = None,
) -> Path:
    questions = load_questions(questions_path)
    if not questions:
        print(f"[warn] {questions_path} has no questions; nothing to run.")
        sys.exit(0)

    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = next_available_path(runs_dir / f"{stamp}.jsonl")

    corpus_sha256 = read_corpus_sha256(db_path)
    git_sha, git_dirty = read_git_state()
    meta = build_run_meta(model, options, timeout_s, corpus_sha256, git_sha, git_dirty)

    # A per-call timeout is what the brief requires. Setting it on the
    # Client applies it to every underlying HTTP request to Ollama.
    client = ollama.Client(host=ollama_host, timeout=timeout_s)

    print(f"[run ] model={model} questions={len(questions)} → {out_path}")
    if corpus_sha256 is None:
        print("[warn] corpus_sha256 unavailable — run ingest so results can be traced")

    with out_path.open("w") as out:
        out.write(json.dumps(meta) + "\n")
        out.flush()
        for q in questions:
            print(f"  - {q['id']}: {q['question'][:80]}")
            record = {"type": "answer", "id": q["id"], "question": q["question"]}
            if "expected_refs" in q:
                record["expected_refs"] = q["expected_refs"]
            started = time.monotonic()
            try:
                record["answer"] = ask(client, model, q["question"], options)
            except Exception as e:
                # Per brief: on failure, record and continue — never abort
                # the whole run because one question timed out or errored.
                record["error"] = f"{type(e).__name__}: {e}"
            record["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            out.write(json.dumps(record) + "\n")
            out.flush()
    print(f"[done] wrote {out_path}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag")
    p.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    p.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                   help="Per-question timeout in seconds")
    # Sampling params — recorded in run_meta whether the model honours them
    # or not, so a run's methodology is always explicit.
    p.add_argument("--temperature", type=float, default=0.0,
                   help="Sampling temperature (default 0 for determinism)")
    p.add_argument("--top-p", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--host", default=None, help="Ollama host URL (default localhost)")
    args = p.parse_args(argv)

    options = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "seed": args.seed,
    }
    run(
        model=args.model,
        questions_path=args.questions,
        runs_dir=args.runs_dir,
        options=options,
        timeout_s=args.timeout,
        db_path=args.db_path,
        ollama_host=args.host,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
