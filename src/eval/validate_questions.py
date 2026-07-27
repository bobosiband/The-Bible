"""Validate `data/eval/questions.jsonl` and report line-numbered errors.

Used by `src/eval/run_eval.load_questions` (which turns errors into
`SystemExit`) and by `make validate-questions` (which prints them and
exits non-zero). Same validation rules for both — keeping them in one
place stops the runner and the pre-flight check from drifting.

CLI:
    python -m src.eval.validate_questions [path]

Exit codes:
    0 — file exists and every non-blank/non-# line parses.
    2 — one or more validation errors (printed one per line).
    3 — file does not exist.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO_ROOT / "data" / "eval" / "questions.jsonl"


@dataclass(frozen=True)
class ValidationError:
    """One problem with one line. Rendered as `path:lineno: message`
    (matching the compiler-style diagnostic format that editors
    know how to jump to)."""
    path: Path
    lineno: int
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.lineno}: {self.message}"


def _iter_records(path: Path):
    """Yield (lineno, raw_line) for every line that is neither blank
    nor a comment. Line numbers are 1-based (matches editors)."""
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield lineno, line


def validate_file(path: Path) -> list[ValidationError]:
    """Return a list of `ValidationError` for `path`. Empty list means
    the file is valid (including an empty file with no questions — the
    runner treats emptiness separately, per the Stage 3 refusal rule).

    Raises FileNotFoundError if `path` does not exist. That is a distinct
    condition from "file exists but has bad content"; the CLI maps it
    to a different exit code and message.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    errors: list[ValidationError] = []
    seen_ids: dict[str, int] = {}
    for lineno, line in _iter_records(path):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(ValidationError(path, lineno, f"invalid JSON — {e}"))
            continue
        if not isinstance(rec, dict):
            errors.append(ValidationError(
                path, lineno,
                f"expected a JSON object, got {type(rec).__name__}",
            ))
            continue
        if "question" not in rec:
            errors.append(ValidationError(
                path, lineno, "missing required field 'question'",
            ))
        elif not isinstance(rec["question"], str) or not rec["question"].strip():
            errors.append(ValidationError(
                path, lineno, "'question' must be a non-empty string",
            ))
        qid = rec.get("id")
        if qid is not None:
            if not isinstance(qid, str) or not qid:
                errors.append(ValidationError(
                    path, lineno, "'id' must be a non-empty string when present",
                ))
            elif qid in seen_ids:
                errors.append(ValidationError(
                    path, lineno,
                    f"duplicate id {qid!r} (first seen on line {seen_ids[qid]})",
                ))
            else:
                seen_ids[qid] = lineno
        if "expected_refs" in rec:
            er = rec["expected_refs"]
            if not isinstance(er, list) or not all(isinstance(x, str) for x in er):
                errors.append(ValidationError(
                    path, lineno,
                    "'expected_refs' must be an array of strings when present",
                ))
    return errors


def parse_valid_records(path: Path) -> list[dict]:
    """Return the parsed records from `path`. Callers that want validation
    should call `validate_file` first — this helper assumes validation
    already ran, and silently skips lines that don't parse (so the
    runner's Stage-3 refusal logic still surfaces the empty-file case
    even in the face of a file that is entirely comments).

    Auto-assigns `id = f"q{lineno:03d}"` when missing, matching the
    behaviour the runner had before the validate_questions extraction.
    """
    out: list[dict] = []
    for lineno, line in _iter_records(path):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict) or "question" not in rec:
            continue
        rec.setdefault("id", f"q{lineno:03d}")
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", nargs="?", type=Path, default=DEFAULT_QUESTIONS)
    args = p.parse_args(argv)
    try:
        errors = validate_file(args.path)
    except FileNotFoundError:
        print(f"[abort] questions file not found: {args.path}", file=sys.stderr)
        return 3
    if errors:
        for e in errors:
            print(e.format(), file=sys.stderr)
        print(
            f"[fail] {len(errors)} validation error(s) in {args.path}",
            file=sys.stderr,
        )
        return 2
    print(f"[ok] {args.path} validates cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
