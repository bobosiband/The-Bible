"""One-command experiment: validate → baseline run → grounded run →
citation check on both → compare_runs → print report location.

Idempotent per invocation — the runner never overwrites (see
`next_available_path` in run_eval), and this orchestrator uses each
step's output path as the next step's input, so a mid-flight failure
leaves a partial trail rather than clobbering prior runs.

Fails gracefully at exactly two points:
  - questions.jsonl missing/invalid/empty → clear message pointing at
    docs/SCHEMAS.md and the write-your-questions rule.
  - classify_citation still NotImplementedError → exit at the checker
    step with a message pointing at docs/CITATION_METRIC.md.

Nothing here parses `--fail-on-*` flags or tries to be clever about
what "success" means. It runs the full chain and hands the reader a
markdown file.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from src.eval import citation_check, compare_runs, run_eval, validate_questions

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORTS_DIR = REPO_ROOT / "reports"


def _run_step(argv: list[str], *, description: str) -> int:
    """Run a `python -m ...` step as a subprocess so its exit code is
    propagated verbatim. Prints the command before running so the log
    reads like a shell transcript."""
    print(f"\n[step] {description}")
    print(f"  $ {' '.join(argv)}")
    result = subprocess.run(argv)
    return result.returncode


def _newest_run_for_mode(runs_dir: Path, mode: str) -> Path | None:
    """Return the newest run file whose run_meta.mode matches `mode`.
    Runs are sorted by filename (UTC timestamp prefix guarantees
    chronological order in the filename)."""
    import json
    matches: list[tuple[str, Path]] = []
    for p in sorted(runs_dir.glob("*.jsonl")):
        with p.open() as f:
            first = f.readline()
        try:
            meta = json.loads(first)
        except json.JSONDecodeError:
            continue
        if meta.get("type") == "run_meta" and meta.get("mode") == mode:
            matches.append((p.name, p))
    return matches[-1][1] if matches else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default=run_eval.DEFAULT_MODEL)
    p.add_argument("--questions", type=Path, default=run_eval.DEFAULT_QUESTIONS)
    p.add_argument("--runs-dir", type=Path, default=run_eval.DEFAULT_RUNS_DIR)
    p.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = p.parse_args(argv)

    # ---- Step 1: validate the questions file. Empty file → refuse before
    # touching Ollama.
    try:
        errors = validate_questions.validate_file(args.questions)
    except FileNotFoundError:
        print(
            f"\n[abort] questions file not found: {args.questions}\n"
            f"See README.md and docs/SCHEMAS.md — you write these.",
            file=sys.stderr,
        )
        return 3
    if errors:
        for e in errors:
            print(e.format(), file=sys.stderr)
        print(f"\n[abort] {len(errors)} validation error(s). "
              f"Fix the file and re-run.", file=sys.stderr)
        return 2
    parsed = validate_questions.parse_valid_records(args.questions)
    if not parsed:
        print(
            f"\n[abort] {args.questions} has no questions.\n"
            f"Write your own eval questions (see docs/SCHEMAS.md — "
            f"placeholders would poison the baseline).",
            file=sys.stderr,
        )
        return 4

    # ---- Step 2: baseline run.
    rc = _run_step([
        sys.executable, "-m", "src.eval.run_eval",
        "--mode", "baseline", "--model", args.model,
        "--questions", str(args.questions),
        "--runs-dir", str(args.runs_dir),
    ], description="baseline run")
    if rc != 0:
        return rc

    # ---- Step 3: grounded run.
    rc = _run_step([
        sys.executable, "-m", "src.eval.run_eval",
        "--mode", "grounded", "--model", args.model,
        "--questions", str(args.questions),
        "--runs-dir", str(args.runs_dir),
    ], description="grounded run")
    if rc != 0:
        return rc

    # ---- Step 4: locate the two newest runs by mode.
    baseline_run = _newest_run_for_mode(args.runs_dir, "baseline")
    grounded_run = _newest_run_for_mode(args.runs_dir, "grounded")
    if baseline_run is None or grounded_run is None:
        print(
            f"\n[abort] could not locate both runs in {args.runs_dir} — "
            f"baseline={baseline_run}, grounded={grounded_run}",
            file=sys.stderr,
        )
        return 5

    # ---- Step 5: citation check on each. Graceful failure at
    # NotImplementedError with a pointer.
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    baseline_report = args.reports_dir / (baseline_run.stem + ".baseline.report.json")
    grounded_report = args.reports_dir / (grounded_run.stem + ".grounded.report.json")

    rc = _run_step([
        sys.executable, "-m", "src.eval.citation_check",
        str(baseline_run), "--out", str(baseline_report),
    ], description="checker (baseline)")
    if rc == citation_check.EXIT_NOT_IMPLEMENTED:
        print(
            "\n[abort] classify_citation is not implemented yet.\n"
            "Implement it in src/eval/citation_check.py per "
            "docs/CITATION_METRIC.md, then re-run `make experiment`.\n"
            "The eval runs are ALREADY DONE — they're in\n"
            f"  baseline: {baseline_run}\n"
            f"  grounded: {grounded_run}\n"
            "so `make experiment` will not re-run the model when you "
            "resume; it will pick up the newest runs of each mode.",
            file=sys.stderr,
        )
        return citation_check.EXIT_NOT_IMPLEMENTED
    if rc != 0:
        return rc

    rc = _run_step([
        sys.executable, "-m", "src.eval.citation_check",
        str(grounded_run), "--out", str(grounded_report),
    ], description="checker (grounded)")
    if rc != 0:
        return rc

    # ---- Step 6: compare_runs.
    compare_md = args.reports_dir / f"compare_{baseline_run.stem}_vs_{grounded_run.stem}.md"
    rc = _run_step([
        sys.executable, "-m", "src.eval.compare_runs",
        str(baseline_report), str(grounded_report),
        "--out", str(compare_md),
    ], description="compare_runs")
    if rc == compare_runs.EXIT_QUESTIONS_MISMATCH:
        print("\n[abort] the two runs used different questions.jsonl. "
              "That should not happen from `make experiment` — a manual "
              "run in between?", file=sys.stderr)
        return rc
    if rc != 0:
        return rc

    print(f"\n[done] report: {compare_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
