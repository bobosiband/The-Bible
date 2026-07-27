# Checker fixtures

13 mechanically-constructed test fixtures for `classify_citation`. Each
file is a JSON object matching the `CheckerFixture` schema in
`docs/SCHEMAS.md` — an `entry` block that is a valid `EvalRunEntry`
answer record, and an `expected` block whose fields are all
`"LABELS_PENDING"` until the repo owner fills them in.

## Two rules matter more than everything else here

1. **No sketched labels.** Every `expected.*` value is the literal
   string `"LABELS_PENDING"`. A filled-in `expected` field is a claim
   about ground truth that the fixture harness will assert against —
   pre-filling one bakes an unaudited judgement into the metric.

2. **No hand-authored verse text.** Every fixture is built by pulling
   real BSB text from the local corpus via `_generate.py`, then
   perturbing it deterministically (swap one word, change a chapter
   number, drop a reference). The point is that a MISQUOTED case
   really is a byte-for-byte alteration of a real BSB verse — anything
   else measures the fixture author's memory, not the model's fidelity.

## Regenerating

```
.venv/bin/python -m tests.checker_fixtures._generate
```

Requires the BSB corpus at `data/corpus/bible.db`. Overwrites the
`fixture_*.json` files with the same content — the generator is
deterministic. If the `EvalRunEntry` schema evolves, rerun this and
review the diff before committing.

## What the harness does

`tests/test_checker_fixtures.py` loads every `fixture_*.json`, and for
each one:

- SKIPs if `expected.verdicts == "LABELS_PENDING"` (labels not yet
  written).
- SKIPs if `classify_citation` raises `NotImplementedError` (scorer
  not yet written).
- Otherwise runs `classify_citation` on every reference in
  `entry.refs_in_answer` and asserts the resulting verdicts equal
  `expected.verdicts` in order.

Never passes vacuously — a fixture whose labels aren't ready SKIPS with
a clear reason line rather than silently succeeding.
