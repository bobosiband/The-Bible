# Corpus audit — BSB via helloao

Date: 2026-07-24
Auditor: Stage 2 review
Corpus SHA256 (local): `5cb6ce27311dda29cb94c10bb968e6185a21f563fb273b2d0e23b833c84f2711`

## Scope

Verify that verse text stored in `data/corpus/bible.db` faithfully reflects
the BSB JSON at `data/corpus/bsb_complete.json`. The concern: the source
JSON does not store verse text as a string. Each verse's `content` is an
array mixing plain strings with objects (`{noteId}`, `{lineBreak}`,
`{text, poem}`, `{text, wordsOfJesus}`). A naive loader can corrupt the
text in ways invisible in a spot-check but poisonous for every accuracy
number downstream.

## Fidelity assertions verified

All the following pass across all 31,086 verses of the loaded corpus:

| Assertion                                                        | Passes |
|-------------------------------------------------------------------|--------|
| No verse text is empty or whitespace-only                         | ✅ |
| No verse text contains `[object`                                  | ✅ |
| No verse text contains the literal token `noteId`                 | ✅ |
| No verse text contains the literal token `lineBreak`              | ✅ |
| No verse text contains a leaked Python `dict` repr (`{'`)         | ✅ |
| No verse text contains a leaked JSON object (`{"`)                | ✅ |
| No verse text starts or ends with whitespace                      | ✅ |
| No verse text contains a run of two or more spaces                | ✅ |
| No verse text has a space immediately before `”` or `’`           | ✅ |

## Specific case fixtures verified

| Case                              | Result | Notes |
|-----------------------------------|--------|-------|
| Gen 1:3 — footnote mid-verse      | ✅     | Loader joins the two text segments with exactly one space; the `{noteId: 0}` marker is dropped. Verse reads as one clean sentence. |
| Gen 1:5 — inline `lineBreak`      | ✅     | The `{lineBreak: true}` marker is dropped; both halves are concatenated with a single joining space; verse contains both `God called the light` and `the first day`. |
| Ps 49 — Hebrew subtitle           | ✅     | Chapter-level `heading`, `hebrew_subtitle`, and `line_break` nodes are all skipped. Verse 1 begins with `Hear this, all you peoples;`, never with `For the choirmaster` or `The Evanescence of Wealth`. |
| Red-letter (Matt 5:3)             | ✅     | Objects with a `text` field carrying `poem`/`wordsOfJesus` are fully preserved. Matt 5:3 reads `"Blessed are the poor in spirit, for theirs is the kingdom of heaven.` (no closing quote — see caveat below, this is source-level, not a loader bug). |
| Ps 117 — shortest chapter         | ✅     | Two verses, numbered 1 and 2. |
| Ps 119 — longest chapter          | ✅     | 176 verses.  |
| John 3:16 — canonical spot-check  | ✅     | `For God so loved the world … eternal life.` |
| John 3:3 — closing-quote handling | ✅     | Sentence ends `born again.”` — no space before the closing curly quote. Regression-tested. |

## Bug found and fixed

### 1. Space-before-closing-curly-quote after a footnote

**Symptom.** Any verse whose closing curly quote (`”` U+201D or `’` U+2019)
sits in its own JSON segment because a `{noteId}` object separates it from
its sentence was stored with a stray space in front of the quote.

- John 3:3 stored as `…born again. ”` (extra space).
- 139 verses in total exhibited this pattern before the fix.

**Cause.** `src/ingest/bsb.py::_verse_text` stripped each string segment
and joined the results with `" "`. When the next segment was just a
closing-quote character, the space in the joiner became a false interior
space.

**Fix.** After the space-join, a compiled regex `_ATTACHING_PUNCT` collapses
any whitespace that immediately precedes a member of the attaching-punct
set `,.;:?!)]}”’`. Straight quotes are deliberately excluded (ambiguous
open/close). The BSB uses curly quotes consistently so this is safe.

**Verification.** After re-ingest, the SQL check
`SELECT COUNT(*) FROM verses WHERE text LIKE '% ”%' OR text LIKE '% ’%'`
returns `0` (was `139`). A regression test in `tests/test_corpus_text.py`
locks this in.

## Not a bug (recorded so it is not re-diagnosed)

### Matt 5:3 has no closing quote

The Beatitudes are punctuated in BSB as a single quotation spanning
Matt 5:3-12. The closing `”` sits at the end of 5:12, not at the end of
each verse. Matt 5:3 in the DB reads:

    “Blessed are the poor in spirit, for theirs is the kingdom of heaven.

That is faithful to the source JSON, which does not include a closing
quote in verse 3. Not a loader issue.

### Upstream SHA256 does not match local SHA256

Documented in `data/corpus/SOURCES.md`. The upstream hash appears to
fingerprint the translation data or a different file (`books.json`), not
`complete.json`. Not a corruption signal.

## Corpus totals

Asserted equal to the BSB's own declared counts (see SOURCES.md for the
31,086-vs-31,102 note):

- Books: **66**
- Chapters: **1,189**
- Verses: **31,086**

## Files audited in `data/corpus/`

Every present file is documented in SOURCES.md:

- `SOURCES.md` — committed
- `bsb_complete.json` — gitignored, regenerable
- `bible.db` — gitignored, regenerable

No undocumented files.

---

## Stage 4 verification

Date: 2026-07-24. First time the Stage 2 fidelity claims have been
executed against a real ingested DB with the loader_version guard in
place.

### Forced re-ingest

Command:

```
$ python -m src.ingest.bsb --force
[skip] bsb_complete.json already downloaded
[done] bible.db: 66 books, 1189 chapters, 31086 verses (sha256=5cb6ce27311d…)
```

### `corpus_meta` after `--force`

| Field           | Value |
|-----------------|-------|
| `translation`   | `BSB` |
| `source_url`    | `https://bible.helloao.org/api/BSB/complete.json` |
| `retrieved_at`  | `2026-07-23T05:50:34+00:00` |
| `sha256_local`  | `5cb6ce27311dda29cb94c10bb968e6185a21f563fb273b2d0e23b833c84f2711` |
| `sha256_upstream` | `6cc5238e442b4204b0f617cc5c932bc04f3bae4a0658e6393b0e319653ebe37f` |
| `book_count`    | `66` |
| `chapter_count` | `1189` |
| `verse_count`   | `31086` |
| `loader_version`| `11ca4f036948096f840c7a984b473324c23238cfb2c392eb6464d44c6d5f5977` |

The `sha256_local` matches the pinned `EXPECTED_SHA256` in
`src/ingest/bsb.py` and the value documented in `SOURCES.md`. The
`--force` path also confirmed the upstream file has not changed.

### Verse-text drift from the forced reload

Row-by-row diff of every verse's text before and after `--force`:

- Rows differing: **0** out of 31,086.
- SHA256 of the concatenated (book|chapter|verse|text) stream: identical
  pre- and post-force (`abf0d4d637960761eddf577f57f6c48a56857caddccc2a01fba00d91b8a838eb`).

The DB was already carrying the post-Stage-2 corrected text (the
closing-quote spacing fix). This is expected: the ingest was re-run
after that fix in Stage 2. The `--force` run confirms it beyond doubt
and establishes a fresh `loader_version` fingerprint tied to the
current `src/ingest/bsb.py`.

### `pytest --require-corpus` result

```
============================= 199 passed in 0.46s ==============================
```

Notably:

- `tests/test_book_map_consistency.py` — **3 passed**. All 66 parser
  canonical names resolve against the DB and all 66 DB names
  round-trip through `normalize_book`. `Song of Solomon` (the
  fragility candidate) is intact on both sides.
- `tests/test_corpus_text.py` — **22 passed**. Every whole-corpus
  assertion (no `[object`, no `noteId`, no double spaces, no space
  before closing curly quote, etc.) holds.
- `tests/test_ingest.py` — **12 passed**, including all Stage 3
  loader-guard and idempotency tests.

Corpus fidelity is now claimed against a real DB, not asserted in the
abstract.

