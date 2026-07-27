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
commit, (c) the exact corpus bytes that were in place when the run
executed, and (d) the exact system prompt used. A result you can't
trace to what produced it is not evidence.

Schema is frozen in docs/SCHEMAS.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import ollama

from src.pipeline import (
    DEFAULT_ABSTENTION_THRESHOLD,
    DEFAULT_CONTEXT,
    DEFAULT_K,
    MODE_BASELINE,
    MODE_GROUNDED,
    answer_question,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "eval" / "questions.jsonl"
DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "eval" / "runs"
DEFAULT_DB = REPO_ROOT / "data" / "corpus" / "bible.db"
BASELINE_SYSTEM_PROMPT = REPO_ROOT / "prompts" / "system.v1.txt"
GROUNDED_SYSTEM_PROMPT = REPO_ROOT / "prompts" / "system.v2.txt"
DEFAULT_SYSTEM_PROMPT = BASELINE_SYSTEM_PROMPT  # back-compat for tests
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT_S = 120.0


# ---------------------------------------------------------------------------
# System prompt loading
# ---------------------------------------------------------------------------

def load_system_prompt(path: Path = DEFAULT_SYSTEM_PROMPT) -> tuple[str, str]:
    """Return (text, sha256) for the system prompt. Hash is over the raw
    bytes on disk so it's stable across trailing-newline edits at review
    time and reproducible in CI."""
    raw = path.read_bytes()
    text = raw.decode("utf-8").strip()
    sha = hashlib.sha256(raw).hexdigest()
    return text, sha


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------

def read_corpus_sha256(db_path: Path, translation: str = "BSB") -> str | None:
    """Read the locally-computed corpus SHA256 recorded by the ingest.

    Returns None if the DB or corpus_meta row is missing — we still run,
    but the run_meta will flag the missing hash and a warning is printed.
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

class EmptyQuestionsError(RuntimeError):
    """Raised when questions.jsonl has no questions. See Stage 3 Task 5."""


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

def build_prompt_messages(system_prompt: str, question: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]


def ask(
    client: ollama.Client,
    model: str,
    system_prompt: str,
    question: str,
    options: dict,
) -> str:
    """Send one question to the local Ollama model and return the raw answer."""
    response = client.chat(
        model=model,
        messages=build_prompt_messages(system_prompt, question),
        options=options,
    )
    # `ollama.chat` returns an object with attribute access; be defensive
    # in case a future version returns a plain dict.
    msg = getattr(response, "message", None) or response.get("message")
    content = getattr(msg, "content", None) or msg.get("content")
    return content


def make_ollama_caller(client, model: str, options: dict):
    """Wrap the Ollama client as the (system, user) -> answer callable
    the pipeline expects. Kept out of the pipeline itself so tests can
    inject a stub callable and the pipeline module has no dependency on
    the ollama package."""
    def call(system: str, user: str) -> str:
        return ask(client, model, system, user, options)
    return call


# Reference serialisation lives in src.corpus.references so the runner
# and the citation checker use the same code path. Do not re-implement.


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
    system_prompt_sha256: str,
    questions_sha256: str,
    mode: str = MODE_BASELINE,
    retrieval_params: dict | None = None,
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
        "system_prompt_sha256": system_prompt_sha256,
        "questions_sha256": questions_sha256,
        "mode": mode,
        "retrieval_params": retrieval_params,
    }


def hash_questions_file(path: Path) -> str:
    """SHA256 over the raw bytes of the questions file. Used by
    compare_runs to refuse cross-comparisons of runs against different
    question sets."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_index_version(db_path: Path, translation: str = "BSB") -> str | None:
    """Read the FTS5 index_version fingerprint if present. Provenance
    only — the pipeline still refuses to query on an out-of-sync index."""
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT index_version FROM retrieval_index_meta "
                "WHERE translation = ?",
                (translation,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _resolve_system_prompt_path(mode: str, override: Path | None) -> Path:
    if override is not None:
        return override
    if mode == MODE_GROUNDED:
        return GROUNDED_SYSTEM_PROMPT
    return BASELINE_SYSTEM_PROMPT


def run(
    model: str,
    questions_path: Path,
    runs_dir: Path,
    options: dict,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    db_path: Path = DEFAULT_DB,
    ollama_host: str | None = None,
    system_prompt_path: Path | None = None,
    mode: str = MODE_BASELINE,
    retrieval_k: int = DEFAULT_K,
    retrieval_context: int = DEFAULT_CONTEXT,
    abstention_threshold: float = DEFAULT_ABSTENTION_THRESHOLD,
) -> Path:
    questions = load_questions(questions_path)
    if not questions:
        raise EmptyQuestionsError(
            f"{questions_path} is empty. Write your own eval questions "
            f"(see the schema in README.md and docs/SCHEMAS.md) before "
            f"running — placeholders would poison the baseline they're "
            f"measured against."
        )

    prompt_path = _resolve_system_prompt_path(mode, system_prompt_path)
    system_prompt, system_prompt_sha256 = load_system_prompt(prompt_path)

    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = next_available_path(runs_dir / f"{stamp}.jsonl")

    corpus_sha256 = read_corpus_sha256(db_path)
    git_sha, git_dirty = read_git_state()
    questions_sha256 = hash_questions_file(questions_path)
    index_version = _read_index_version(db_path) if mode == MODE_GROUNDED else None
    retrieval_params = None
    if mode == MODE_GROUNDED:
        retrieval_params = {
            "k": retrieval_k,
            "context": retrieval_context,
            "threshold": abstention_threshold,
            "index_version": index_version,
        }
    meta = build_run_meta(
        model, options, timeout_s, corpus_sha256, git_sha, git_dirty,
        system_prompt_sha256, questions_sha256,
        mode=mode, retrieval_params=retrieval_params,
    )

    # A per-call timeout is what the brief requires. Setting it on the
    # Client applies it to every underlying HTTP request to Ollama.
    client = ollama.Client(host=ollama_host, timeout=timeout_s)
    call_model = make_ollama_caller(client, model, options)

    print(f"[run ] mode={mode} model={model} questions={len(questions)} → {out_path}")
    if corpus_sha256 is None:
        print("[warn] corpus_sha256 unavailable — run ingest so results can be traced")
    if mode == MODE_GROUNDED and index_version is None:
        print("[warn] retrieval index not built — run `python -m src.retrieval.build_index`")

    with out_path.open("w") as out:
        out.write(json.dumps(meta) + "\n")
        out.flush()
        for q in questions:
            print(f"  - {q['id']}: {q['question'][:80]}")
            started = time.monotonic()
            result = answer_question(
                q["question"],
                mode=mode,
                system_prompt=system_prompt,
                call_model=call_model,
                k=retrieval_k,
                context=retrieval_context,
                threshold=abstention_threshold,
                db_path=db_path,
                index_version=index_version,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            record: dict = {
                "type": "answer",
                "question_id": q["id"],
                "question": q["question"],
                "prompt": result.prompt,
                "answer": result.answer,
                "refs_in_answer": result.refs_in_answer,
                "model": model,
                "model_tag": model,
                "options": options,
                "system_prompt_sha256": system_prompt_sha256,
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "git_commit_sha": git_sha,
                "git_dirty": git_dirty,
                "corpus_sha256": corpus_sha256,
                "latency_ms": latency_ms,
                "error": result.error,
                "retrieval": result.retrieval,
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
    p.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    p.add_argument(
        "--mode",
        choices=(MODE_BASELINE, MODE_GROUNDED),
        default=MODE_BASELINE,
        help="baseline: v1 prompt, no retrieval. grounded: v2 prompt, "
             "retrieval passages inline in the user message, abstain "
             "when nothing above the score threshold is retrieved.",
    )
    p.add_argument(
        "--system-prompt", type=Path, default=None,
        help="Override the default prompt for the chosen mode. "
             "Defaults to prompts/system.v1.txt for baseline, "
             "prompts/system.v2.txt for grounded.",
    )
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                   help="Per-question timeout in seconds")
    # Sampling params — recorded in run_meta whether the model honours them
    # or not, so a run's methodology is always explicit.
    p.add_argument("--temperature", type=float, default=0.0,
                   help="Sampling temperature (default 0 for determinism)")
    p.add_argument("--top-p", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--host", default=None, help="Ollama host URL (default localhost)")
    p.add_argument("--retrieval-k", type=int, default=DEFAULT_K,
                   help="Grounded mode only: top-k passages to retrieve.")
    p.add_argument("--retrieval-context", type=int, default=DEFAULT_CONTEXT,
                   help="Grounded mode only: surrounding-verse window per hit "
                        "(clipped to chapter boundary).")
    p.add_argument("--abstention-threshold", type=float,
                   default=DEFAULT_ABSTENTION_THRESHOLD,
                   help="Grounded mode only: abstain when the top passage's "
                        "BM25 score is above this value (more negative = "
                        "stronger match; default anchored to real-corpus "
                        "distributions — see Stage 4 report).")
    args = p.parse_args(argv)

    options = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "seed": args.seed,
    }
    try:
        run(
            model=args.model,
            questions_path=args.questions,
            runs_dir=args.runs_dir,
            options=options,
            timeout_s=args.timeout,
            db_path=args.db_path,
            ollama_host=args.host,
            system_prompt_path=args.system_prompt,
            mode=args.mode,
            retrieval_k=args.retrieval_k,
            retrieval_context=args.retrieval_context,
            abstention_threshold=args.abstention_threshold,
        )
    except EmptyQuestionsError as e:
        print(f"[refuse] {e}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
