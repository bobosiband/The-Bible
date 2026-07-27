"""Render a side-by-side markdown comparison of two CheckerReport files.

Reads two `CheckerReport` JSON documents (schema in docs/SCHEMAS.md),
prints a deterministic markdown report contrasting the two runs by
verdict counts and per-question verdicts.

The comparator has no opinion. It never says "grounded is better"; it
renders counts and flags per-question differences, and stops.

Guarded on provenance:
- `questions_sha256` MUST match — otherwise the two reports describe
  answers to *different* questions and any comparison is meaningless.
- `corpus_sha256`, `git_sha`, `model`, `options` differences trigger a
  loud warning; with `--fail-on-provenance-mismatch` they exit non-zero.
- `mode` and `system_prompt_sha256` are EXPECTED to differ — that is
  the whole point of comparing baseline vs grounded, so they are
  silent.

CLI:
    python -m src.eval.compare_runs baseline.report.json grounded.report.json
        [--out compare.md] [--fail-on-provenance-mismatch]

Output on stdout is byte-identical to what --out writes, so
`diff <(compare_runs ...) compare.md` is empty on a fresh run.

Exit codes:
    0 — success (or provenance warnings without --fail-on-...).
    2 — questions_sha256 mismatch (fatal — different question sets).
    3 — missing `questions_sha256` in either report (pre-Stage-5 file).
    4 — file not found / invalid JSON.
    5 — --fail-on-provenance-mismatch and one or more warnings fired.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

VERDICTS = ("RESOLVED", "UNRESOLVABLE", "MISQUOTED", "UNSUPPORTED", "ERROR")

# Fields that must match for the comparison to make sense. `questions_sha256`
# is fatal on mismatch; the rest are warned about and (optionally) failed.
_FATAL_PROVENANCE_KEYS = ("questions_sha256",)
_WARN_PROVENANCE_KEYS = ("corpus_sha256", "git_sha", "model", "options")

# Fields that are EXPECTED to differ between the two runs — they are the
# variable being tested. Silence any warning about them.
_EXPECTED_DIFFERENCES = ("mode", "system_prompt_sha256", "retrieval_params")

EXIT_QUESTIONS_MISMATCH = 2
EXIT_MISSING_QUESTIONS_HASH = 3
EXIT_BAD_FILE = 4
EXIT_PROVENANCE_FAIL = 5


@dataclass(frozen=True)
class ProvenanceWarning:
    key: str
    left: object
    right: object

    def format(self) -> str:
        return f"[warn] {self.key} differs: {self.left!r} vs {self.right!r}"


def _load_report(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def check_provenance(
    left: dict, right: dict,
) -> tuple[str | None, list[ProvenanceWarning]]:
    """Return (fatal_message, warnings). `fatal_message` is a string when
    the two runs cannot be compared (missing or mismatched
    questions_sha256); otherwise None."""
    left_meta = left.get("meta", {})
    right_meta = right.get("meta", {})
    for key in _FATAL_PROVENANCE_KEYS:
        lv = left_meta.get(key)
        rv = right_meta.get(key)
        if lv is None or rv is None:
            return (
                f"missing {key!r} in one or both reports — cannot verify the "
                f"runs describe the same question set. Re-run with the "
                f"post-Stage-5 runner and try again.",
                [],
            )
        if lv != rv:
            return (
                f"{key!r} mismatch — the two reports describe answers to "
                f"different question sets ({lv!r} vs {rv!r}). Refusing to "
                f"pretend this is a comparison.",
                [],
            )
    warnings: list[ProvenanceWarning] = []
    for key in _WARN_PROVENANCE_KEYS:
        lv = left_meta.get(key)
        rv = right_meta.get(key)
        if lv != rv:
            warnings.append(ProvenanceWarning(key, lv, rv))
    return None, warnings


def _totals_row(label: str, totals: dict, n_refs: int) -> str:
    """Render `RESOLVED: 12 (40.0%)` etc. Percentages ONLY appear next
    to raw counts, never alone — the brief explicitly forbids
    percentage-only presentation at n=30.
    """
    parts = []
    for v in VERDICTS:
        n = totals.get(v, 0)
        pct = f" ({100 * n / n_refs:.1f}%)" if n_refs else ""
        parts.append(f"{v}: {n}{pct}")
    return f"**{label}** — " + "; ".join(parts)


def _per_question_verdicts(report: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for q in report.get("per_question", []):
        qid = q.get("question_id")
        if qid is None:
            continue
        out[qid] = [r.get("verdict", "?") for r in q.get("results", [])]
    return out


def render(
    left_report: dict,
    right_report: dict,
    *,
    left_label: str = "baseline",
    right_label: str = "grounded",
    warnings: list[ProvenanceWarning] | None = None,
) -> str:
    """Return the markdown comparison. Deterministic — no timestamps,
    no ordering based on dict iteration."""
    lines: list[str] = []
    lines.append(f"# Shepherd checker comparison — {left_label} vs {right_label}")
    lines.append("")

    # Provenance block. Every field cited so a reader can grep for a
    # specific SHA without opening the underlying reports.
    lm = left_report.get("meta", {})
    rm = right_report.get("meta", {})
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"| Field | {left_label} | {right_label} |")
    lines.append("|---|---|---|")
    for key in (
        "model", "mode", "corpus_sha256", "git_sha",
        "system_prompt_sha256", "questions_sha256",
        "options",
    ):
        lv = lm.get(key)
        rv = rm.get(key)
        # Match markers on the row so a reader sees agreement/disagreement
        # inline.
        marker = " " if lv == rv else " ⚠"
        # Options are dicts — render as JSON so cell content is stable.
        lv_s = json.dumps(lv, sort_keys=True) if isinstance(lv, dict) else str(lv)
        rv_s = json.dumps(rv, sort_keys=True) if isinstance(rv, dict) else str(rv)
        lines.append(f"| {key}{marker} | `{lv_s}` | `{rv_s}` |")
    lines.append("")

    if warnings:
        lines.append("### Provenance warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- ⚠ **{w.key}** differs: `{w.left!r}` vs `{w.right!r}`")
        lines.append("")

    # Aggregate verdict counts.
    lt = left_report.get("totals", {})
    rt = right_report.get("totals", {})
    ln = sum(lt.get(v, 0) for v in VERDICTS)
    rn = sum(rt.get(v, 0) for v in VERDICTS)
    lines.append("## Aggregate verdicts")
    lines.append("")
    lines.append(f"| Verdict | {left_label} | {right_label} |")
    lines.append("|---|---|---|")
    for v in VERDICTS:
        ln_v = lt.get(v, 0)
        rn_v = rt.get(v, 0)
        lpct = f" ({100 * ln_v / ln:.1f}%)" if ln else ""
        rpct = f" ({100 * rn_v / rn:.1f}%)" if rn else ""
        lines.append(f"| {v} | {ln_v}{lpct} | {rn_v}{rpct} |")
    lines.append(f"| **total refs** | **{ln}** | **{rn}** |")
    lines.append("")

    # Per-question table. Sorted by question_id for deterministic output.
    lines.append("## Per-question verdicts")
    lines.append("")
    lines.append(f"| question_id | {left_label} | {right_label} | changed? |")
    lines.append("|---|---|---|---|")
    lv_map = _per_question_verdicts(left_report)
    rv_map = _per_question_verdicts(right_report)
    all_ids = sorted(set(lv_map) | set(rv_map))
    for qid in all_ids:
        lvs = lv_map.get(qid, [])
        rvs = rv_map.get(qid, [])
        changed = "yes" if lvs != rvs else "no"
        lv_s = ", ".join(lvs) if lvs else "—"
        rv_s = ", ".join(rvs) if rvs else "—"
        lines.append(f"| {qid} | {lv_s} | {rv_s} | {changed} |")
    lines.append("")

    lines.append("_This report is a rendering, not a judgement. "
                 "It counts references, flags per-question differences, "
                 "and shows provenance. Interpretation is the reader's._")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("baseline_report", type=Path, help="CheckerReport JSON for baseline mode")
    p.add_argument("grounded_report", type=Path, help="CheckerReport JSON for grounded mode")
    p.add_argument("--out", type=Path, default=None,
                   help="Write markdown to this path AND stdout.")
    p.add_argument("--fail-on-provenance-mismatch", action="store_true",
                   help="Exit non-zero if any warned-about field differs "
                        "(corpus, git, model, options). questions_sha256 "
                        "mismatch is always fatal regardless of this flag.")
    p.add_argument("--left-label", default="baseline")
    p.add_argument("--right-label", default="grounded")
    args = p.parse_args(argv)

    try:
        left = _load_report(args.baseline_report)
        right = _load_report(args.grounded_report)
    except FileNotFoundError as e:
        print(f"[abort] report not found: {e}", file=sys.stderr)
        return EXIT_BAD_FILE
    except json.JSONDecodeError as e:
        print(f"[abort] invalid JSON in report: {e}", file=sys.stderr)
        return EXIT_BAD_FILE

    fatal, warnings = check_provenance(left, right)
    if fatal:
        print(f"[abort] {fatal}", file=sys.stderr)
        if "missing" in fatal:
            return EXIT_MISSING_QUESTIONS_HASH
        return EXIT_QUESTIONS_MISMATCH
    for w in warnings:
        print(w.format(), file=sys.stderr)

    md = render(
        left, right,
        left_label=args.left_label, right_label=args.right_label,
        warnings=warnings,
    )
    print(md)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md)
        print(f"[wrote] {args.out}", file=sys.stderr)

    if warnings and args.fail_on_provenance_mismatch:
        return EXIT_PROVENANCE_FAIL
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
