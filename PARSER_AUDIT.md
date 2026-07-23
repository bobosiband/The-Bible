# Parser audit — `src/corpus/references.py`

Date: 2026-07-24
Auditor: Stage 2 review
Status: **Report only.** No parser changes have been made. See "Untouched"
below for proof.

## Method

Every case in Task 4 of the Stage 2 brief was run against
`parse_references()` (and, where applicable, `get_verse` / `get_range`
against the loaded BSB corpus). Observed output is recorded per case.
"Arguably correct" behaviour is stated but no repair is proposed —
those decisions are the repo owner's to make.

Executable evidence: `tests/test_parser_adversarial.py` (51 cases,
all currently passing — they assert *what the parser does*, not what it
should do).

## Legend

- **PASS** — current behaviour is the natural reading of the input; no
  open decision.
- **FAIL** — current behaviour is arguably wrong. Recorded as an open
  decision in the table at the bottom of this file.

---

## 1. Single-chapter books

Highest-value case per the brief. Jude, Obadiah, Philemon, 2 John, 3 John
each have only one chapter, so "Jude 5" in common usage means Jude 1:5.

| Input           | Returned                        | Arguably should be                | Verdict |
|-----------------|---------------------------------|-----------------------------------|---------|
| `Jude 5`        | `Reference("Jude", 5)`          | `Reference("Jude", 1, 5)`         | FAIL |
| `Obadiah 3`     | `Reference("Obadiah", 3)`       | `Reference("Obadiah", 1, 3)`      | FAIL |
| `Philemon 6`    | `Reference("Philemon", 6)`      | `Reference("Philemon", 1, 6)`     | FAIL |
| `2 John 4`      | `Reference("2 John", 4)`        | `Reference("2 John", 1, 4)`       | FAIL |
| `3 John 2`      | `Reference("3 John", 2)`        | `Reference("3 John", 1, 2)`       | FAIL |

Downstream consequence: for `Jude 5`, `get_range("Jude", 5)` looks up a
non-existent chapter 5 and returns `[]`, so a plausible reference is
silently unresolvable.

## 2. Abbreviations with and without periods

| Input       | Returned                         | Verdict |
|-------------|----------------------------------|---------|
| `Gen 1:1`   | `Reference("Genesis", 1, 1)`     | PASS |
| `Gen. 1:1`  | `Reference("Genesis", 1, 1)`     | PASS |
| `Gn 1:1`    | `Reference("Genesis", 1, 1)`     | PASS |
| `Matt 5:3`  | `Reference("Matthew", 5, 3)`     | PASS |
| `Mt 5:3`    | `Reference("Matthew", 5, 3)`     | PASS |
| `Mt. 5:3`   | `Reference("Matthew", 5, 3)`     | PASS |

Periods are stripped uniformly during pre-processing; there is no
distinction between abbreviated and full forms once normalised.

## 3. Roman numerals and spacing

| Input          | Returned                            | Arguably should be                | Verdict |
|----------------|-------------------------------------|-----------------------------------|---------|
| `I Cor 13:4`   | `[]`                                | `Reference("1 Corinthians", 13, 4)` | FAIL |
| `II Tim 3:16`  | `[]`                                | `Reference("2 Timothy", 3, 16)`   | FAIL |
| `III John 2`   | `[Reference("John", 2)]`            | `Reference("3 John", 1, 2)`       | FAIL (worst — misparses to a different book) |
| `1 Cor`        | `[]`                                | (open — see below)                | FAIL? |
| `1Cor`         | `[]`                                | (open — see below)                | FAIL? |
| `1 Jn`         | `[]`                                | (open — see below)                | FAIL? |
| `1John`        | `[]`                                | (open — see below)                | FAIL? |

The bare-book cases (`1 Cor`, `1Cor`, `1 Jn`, `1John`) currently return
nothing because the regex requires a chapter number after the book. That
could be by design (no chapter → nothing to look up) or a limitation
(users often refer to a whole book). Open.

`III John 2` is particularly bad: the parser skips `III ` as noise and
matches the bare `John`, so the reference is silently rewritten to a
different book of the Bible.

## 4. Psalm forms

| Input      | Returned                       | Verdict |
|------------|--------------------------------|---------|
| `Psalm 23` | `Reference("Psalms", 23)`      | PASS |
| `Psalms 23`| `Reference("Psalms", 23)`      | PASS |
| `Ps 23`    | `Reference("Psalms", 23)`      | PASS |
| `Ps. 23`   | `Reference("Psalms", 23)`      | PASS |

Whole-chapter references produce `verse=None`, which `get_range` handles
by returning the entire chapter.

## 5. Cross-chapter ranges

| Input               | Returned                                      | Arguably should be                                      | Verdict |
|---------------------|-----------------------------------------------|---------------------------------------------------------|---------|
| `Genesis 1:1-2:3`   | `Reference("Genesis", 1, 1, 2)`               | A cross-chapter range Reference, or two `Reference`s     | FAIL |
| `Ps 22:1-23:6`      | `Reference("Psalms", 22, 1, 23)`              | Same                                                     | FAIL |

Cross-chapter ranges aren't part of the `Reference` schema (which has
one `chapter` field). The regex only sees `chapter:verse-verse` and
absorbs the `2` from `2:3` as the range end, silently dropping the `:3`.

## 6. Within-chapter ranges

| Input           | Returned                              | Verdict |
|-----------------|---------------------------------------|---------|
| `1 Cor 13:4-7`  | `Reference("1 Corinthians", 13, 4, 7)`| PASS |
| `Matt 5:3-12`   | `Reference("Matthew", 5, 3, 12)`      | PASS |

## 7. Comma lists

| Input                | Returned                            | Arguably should be                                                                          | Verdict |
|----------------------|-------------------------------------|---------------------------------------------------------------------------------------------|---------|
| `Romans 3:23, 6:23`  | `[Reference("Romans", 3, 23)]`      | Two refs: `Reference("Romans", 3, 23)` and `Reference("Romans", 6, 23)`                     | FAIL |
| `John 1:1, 14`       | `[Reference("John", 1, 1)]`         | Two refs: `Reference("John", 1, 1)` and `Reference("John", 1, 14)`                          | FAIL |

Comma continuation ("same book, next chapter" or "same book+chapter,
another verse") is a common convention not currently supported.

## 8. Alternate names

| Input                  | Returned                             | Verdict |
|------------------------|--------------------------------------|---------|
| `Song of Solomon 2:1`  | `Reference("Song of Solomon", 2, 1)` | PASS |
| `Song of Songs 2:1`    | `Reference("Song of Solomon", 2, 1)` | PASS |
| `Canticles 2:1`        | `Reference("Song of Solomon", 2, 1)` | PASS |
| `Ecclesiastes`         | `[]`                                 | See §3 — bare book handling. |
| `Qoheleth 1:1`         | `[]`                                 | FAIL (alt name not in map) |

`Qoheleth` is a transliteration of the Hebrew name for Ecclesiastes;
academic/study-Bible context sometimes uses it. Not in the parser's
book map. Add or don't — open.

## 9. Common misspelling

| Input                | Returned                             | Verdict |
|----------------------|--------------------------------------|---------|
| `Revelations 22:21`  | `Reference("Revelation", 22, 21)`    | Recorded, no ruling |

`Revelations` (plural) is a common lay misspelling. The current parser
accepts it via a `"Revelations"` alias in the book map. Report only —
no PASS/FAIL called.

## 10. Must NOT resolve

The parser doesn't validate ranges — it happily produces references to
non-existent chapters or verses. The DB lookup is the layer that returns
`None` for missing entries. That separation itself is a design choice.

| Input               | `parse_references` returns                 | `get_verse` / `get_range` returns | Verdict |
|---------------------|--------------------------------------------|-----------------------------------|---------|
| `John 3:99`         | `Reference("John", 3, 99)`                 | `get_verse` → `None`              | PASS (correct: lookup fails as it should) |
| `Psalm 151:1`       | `Reference("Psalms", 151, 1)`              | `get_verse` → `None`              | PASS |
| `Genesis 51:1`      | `Reference("Genesis", 51, 1)`              | `get_verse` → `None`              | PASS |
| `Matthew 29:1`      | `Reference("Matthew", 29, 1)`              | `get_verse` → `None`              | PASS |
| `Jude 26`           | `Reference("Jude", 26)`                    | `get_range("Jude", 26)` → `[]`    | FAIL (see §1 — misparses; then lookup fails) |
| `Obadiah 22`        | `Reference("Obadiah", 22)`                 | `get_range("Obadiah", 22)` → `[]` | FAIL (same) |

## 11. Malformed inputs

The brief requires no unhandled exceptions on any of these:

| Input             | Returned / Behaviour                                | Verdict |
|-------------------|-----------------------------------------------------|---------|
| `""`              | `[]`                                                | PASS |
| `"John"`          | `[]`                                                | PASS (bare book — see §3) |
| `"John 3:"`       | `[Reference("John", 3)]` (trailing colon dropped)   | PASS |
| `"3:16"`          | `[]`                                                | PASS |
| `"John :16"`      | `[]`                                                | PASS |
| `"John 3:16:20"`  | `[Reference("John", 3, 16)]` (extra `:20` ignored)  | PASS |
| `"JohnJohn 3:16"` | `[]`                                                | PASS |
| `None`            | `[]`                                                | PASS |
| `12345` (int)     | **Raises `AttributeError`** on `.replace('.', ' ')` | **FAIL** — spec says no unhandled exceptions |

## 12. Extraction — precision / recall

Input:
> Read John 3:16 and Romans 8:28 and Psalm 23 today. Also 1 in 3:1 odds. Acts 2 chapter three.

Ground truth (three valid references):
`{John 3:16, Romans 8:28, Psalms 23}`

Parser returned (in order):
`[John 3:16, Romans 8:28, Psalms 23, Acts 2]`

- **True positives**: `John 3:16`, `Romans 8:28`, `Psalms 23` (3)
- **False positives**: `Acts 2` (1) — from `"Acts 2 chapter three."` The
  parser has no semantic view of "chapter three" contradicting the earlier
  chapter number, and greedily matches `Acts 2` as a whole-chapter ref.
- **False negatives**: none — the near-miss `"1 in 3:1 odds"` was
  correctly rejected (no valid book precedes `3:1`).

Precision = 3/4 = **0.75**
Recall    = 3/3 = **1.00**

Verdict: **FAIL** on precision — one false positive. Recall is perfect on
this example but the sample size is small.

---

## Grand summary

| Group                                | Cases | PASS | FAIL |
|--------------------------------------|-------|------|------|
| 1. Single-chapter books              | 5     | 0    | 5    |
| 2. Abbreviations w/wo period         | 6     | 6    | 0    |
| 3. Roman numerals + spacing          | 7     | 0    | 7    |
| 4. Psalm forms                       | 4     | 4    | 0    |
| 5. Cross-chapter ranges              | 2     | 0    | 2    |
| 6. Within-chapter ranges             | 2     | 2    | 0    |
| 7. Comma lists                       | 2     | 0    | 2    |
| 8. Alternate names                   | 5     | 3    | 2    |
| 9. Misspelling `Revelations`         | 1     | —    | —    |
| 10. Must NOT resolve                 | 6     | 4    | 2    |
| 11. Malformed                        | 9     | 8    | 1    |
| 12. Extraction (paragraph)           | 1     | 0    | 1    |
| **Total**                            | **50**| **27** | **22** |

## Untouched

Requirement: at the end of Stage 2, `git diff` on the parser source must
be empty.

```
$ git diff HEAD -- src/corpus/references.py
(empty)
$ git diff HEAD -- src/corpus/references.py | wc -l
0
```

Confirmed. No lines added, removed, or moved in `src/corpus/references.py`
during this stage.

---

## Open decisions (for the repo owner)

One row per FAIL. The **Ruling** column is intentionally blank — the
audit surfaces the choice, the owner makes it. No recommendations.

| # | Case | Current behaviour | Ruling |
|---|------|-------------------|--------|
| 1 | `Jude 5` | Returns `Reference("Jude", 5)` (as chapter) | |
| 2 | `Obadiah 3` | Returns `Reference("Obadiah", 3)` (as chapter) | |
| 3 | `Philemon 6` | Returns `Reference("Philemon", 6)` (as chapter) | |
| 4 | `2 John 4` | Returns `Reference("2 John", 4)` (as chapter) | |
| 5 | `3 John 2` | Returns `Reference("3 John", 2)` (as chapter) | |
| 6 | `I Cor 13:4` | Returns `[]`; roman numerals not in book map | |
| 7 | `II Tim 3:16` | Returns `[]`; roman numerals not in book map | |
| 8 | `III John 2` | Returns `Reference("John", 2)` — misparses to a different book | |
| 9 | `1 Cor` (bare) | Returns `[]`; no chapter required to match | |
|10 | `1Cor` (bare, no space) | Returns `[]` | |
|11 | `1 Jn` (bare) | Returns `[]` | |
|12 | `1John` (bare, no space) | Returns `[]` | |
|13 | `Genesis 1:1-2:3` | Returns `Reference("Genesis", 1, 1, 2)` — cross-chapter range not modelled | |
|14 | `Ps 22:1-23:6` | Returns `Reference("Psalms", 22, 1, 23)` — same | |
|15 | `Romans 3:23, 6:23` | Returns only first ref; comma list ignored | |
|16 | `John 1:1, 14` | Returns only first ref; comma-verse ignored | |
|17 | `Qoheleth 1:1` | Returns `[]`; alt name not in book map | |
|18 | `Ecclesiastes` (bare) | Returns `[]`; same as §3 bare-book question | |
|19 | `Jude 26` (lookup) | `get_range` returns `[]` because chapter 26 doesn't exist — knock-on from §1 | |
|20 | `Obadiah 22` (lookup) | `get_range` returns `[]` — knock-on from §1 | |
|21 | `parse_references(12345)` | Raises `AttributeError`; spec says no unhandled exceptions | |
|22 | Extraction FP `Acts 2` | Greedy book+chapter match ignores contradicting `chapter three` immediately after | |
