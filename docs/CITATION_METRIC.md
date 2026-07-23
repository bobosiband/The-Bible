# Shepherd citation metric

`src/eval/citation_check.py` provides the harness (schema loading, per-run
aggregation, exit codes, `nearby_text` helper). The scoring function
`classify_citation` is the metric and is implemented by the repo owner.
This document is the specification `classify_citation` is written against.

## Verdicts

One line each on what RESOLVED / UNRESOLVABLE / MISQUOTED / UNSUPPORTED /
ERROR mean, cross-linked to the `Verdict` enum in
`src/eval/citation_check.py`. Docstrings on the enum members are the
source of truth for the terse definitions; this section is where the
nuanced distinctions and edge cases live.

## Locating the quoted text

How the metric decides which stretch of the model's answer to compare
against the corpus verse text — window size, sentence boundaries, quote
mark handling, whether the reference itself is excluded from the window,
and how to handle references that appear inside a larger citation
(e.g. "compare 1 Cor 13:4-7 with Rom 12:9"). This is the metric's core
design decision.

## Judging misquotation

The threshold or comparison used to decide when the quoted text does not
match the corpus verse — normalisation applied (via
`src.corpus.normalize.normalize_text` — do not add a second normaliser),
tolerance for punctuation, whitespace, and ellipsis, and whether
paraphrase counts as MISQUOTED or something else.

## What counts as unsupported

The rule for `RESOLVED`-but-`UNSUPPORTED`: the reference is correct and
the quotation matches the corpus, but the passage does not actually back
the claim the model made using it. How the surrounding claim is
identified and how "support" is judged.

## Known limitations

Categories of citation where the metric will produce false positives or
false negatives that you accept as a known cost — with a short reason
per category — and any recommended manual review process for run files
whose totals sit in an ambiguous range.
