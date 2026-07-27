# Shepherd data schemas

This document freezes the on-disk record shapes that citation_check.py and
any future analysis tool are written against. **After Stage 3, these
schemas change only with explicit approval from the repo owner** — the
whole point of pinning them is that a downstream tool can trust them.

Two schemas are frozen here:

- `QuestionRecord` — one line in `data/eval/questions.jsonl`
- `EvalRunEntry` — one line in `data/eval/runs/<UTC>.jsonl` (a `run_meta`
  header on line 1 followed by one `answer` record per question)

Also documented:

- Rules that matter more than the field list
- The shared normalisation module
- The versioned system prompt

---

## QuestionRecord — `data/eval/questions.jsonl`

One JSON object per line. Written by the repo owner. Never populated
automatically; empty by design until real questions are added.

### Fields

| Field           | Type           | Required | Notes |
|-----------------|----------------|----------|-------|
| `id`            | string         | yes      | Unique per file. Convention: `q001`, `q002`, ... |
| `question`      | string         | yes      | Verbatim prompt sent to the model, appended after the system prompt. |
| `expected_refs` | array[string]  | no       | **Provisional.** Canonical reference strings like `"John 3:16"` or `"1 Corinthians 13:4-7"`. Reserved for a reference-based citation metric. The decision on whether the metric is reference-based, text-based, or both is not yet made — populate only if useful to you. |
| `notes`         | string         | no       | Free text for the author. Ignored by all tooling. |

### Worked example

```json
{"id": "q001", "question": "Where does Paul describe love?", "expected_refs": ["1 Corinthians 13:4-7"], "notes": "single-passage recall"}
```

---

## EvalRunEntry — `data/eval/runs/<UTC>.jsonl`

Each run file contains:

1. Exactly one `run_meta` record on line 1.
2. One `answer` record per question (in the same order as questions.jsonl).
3. Nothing else.

The runner NEVER overwrites an existing file — if the target filename
already exists, a `-2`, `-3`, ... suffix is inserted before `.jsonl`.

### `run_meta` (line 1)

| Field                  | Type   | Notes |
|------------------------|--------|-------|
| `type`                 | string | Always `"run_meta"`. |
| `run_started_at`       | string | ISO-8601 UTC to the second, e.g. `"2026-07-24T13:22:05+00:00"`. |
| `model`                | string | Ollama model tag. |
| `options`              | object | Sampling params (`temperature`, `top_p`, `seed`) passed to Ollama. |
| `timeout_s`            | number | Per-question timeout in seconds. |
| `git_sha`              | string \| null | 40-char SHA of `HEAD` when the run started. Null outside a git repo. |
| `git_dirty`            | bool   | True if `git status --porcelain` was non-empty at run start. |
| `corpus_sha256`        | string \| null | `sha256_local` from `corpus_meta` for the loaded BSB corpus. Null if the corpus is absent. |
| `system_prompt_sha256` | string | SHA256 over the raw bytes of the loaded system prompt (default `prompts/system.v1.txt` for baseline, `prompts/system.v2.txt` for grounded; overridable per-run via `--system-prompt`). |
| `questions_sha256`     | string | SHA256 over the raw bytes of the questions file (`data/eval/questions.jsonl` by default). Lets `compare_runs.py` refuse to compare runs against a shifted question set. May be absent on pre-Stage-5 run files. |
| `mode`                 | string | `"baseline"` or `"grounded"`. Baseline: v1 prompt, no retrieval, `retrieval` on every answer is null. Grounded: v2 prompt, retrieval passages inline in the user message, `retrieval` populated per the object below. |
| `retrieval_params`     | object \| null | Null in baseline mode. In grounded mode: `{k, context, threshold, index_version}` — the parameters passed to `src.retrieval.retrieve`, duplicated at the run level so run files are self-describing. |

### `answer` (one per question)

| Field                  | Type            | Notes |
|------------------------|-----------------|-------|
| `type`                 | string          | Always `"answer"`. |
| `question_id`          | string          | From the QuestionRecord. |
| `question`             | string          | Verbatim. |
| `prompt`               | string          | The exact final string sent to the model, including the system prompt. Preserved so a run can be reproduced even if `prompts/system.txt` is later changed. |
| `answer`               | string \| null  | **Raw model output, verbatim.** Never trimmed, normalised, or repaired. Null on error. |
| `refs_in_answer`       | array[object]   | Every `Reference` the parser extracts from `answer`. Serialised — see below. Empty array on error or when no references present. |
| `model`                | string          | Same as `run_meta.model`. Duplicated per-line so a single row is self-describing. |
| `model_tag`            | string          | Same as `model` on Ollama; reserved so citation_check can distinguish family from tag if the runner adds richer identification later. |
| `options`              | object          | Duplicated from `run_meta` for the same reason. |
| `system_prompt_sha256` | string          | Duplicated from `run_meta`. |
| `timestamp`            | string          | UTC ISO-8601 for when this specific answer was recorded. |
| `git_commit_sha`       | string \| null  | Same as `run_meta.git_sha`. |
| `git_dirty`            | bool            | Same as `run_meta.git_dirty`. |
| `corpus_sha256`        | string \| null  | Same as `run_meta.corpus_sha256`. |
| `latency_ms`           | integer \| null | Wall-clock milliseconds for this question. Populated even on error. |
| `error`                | string \| null  | `"TypeName: message"` on failure; null on success. |
| `retrieval`            | null \| object  | Null in baseline mode. In grounded mode: the retrieval object described below (populated whether the model was called or the pipeline abstained). |
| `expected_refs`        | array[string]   | Only present if `expected_refs` was set in the QuestionRecord. Passed through unchanged. |

### `refs_in_answer[]` sub-schema

Each element is the serialised form of a `src.corpus.references.Reference`
extracted from `answer` by `parse_references(answer)`:

| Field         | Type         | Notes |
|---------------|--------------|-------|
| `book`        | string       | Canonical name (e.g. `"1 Corinthians"`, `"Psalms"`). |
| `chapter`     | integer      | 1-based. |
| `verse`       | int \| null  | Null for whole-chapter refs like `"Psalm 23"`. |
| `end_verse`   | int \| null  | Range end (inclusive); null for single-verse refs. |
| `end_chapter` | int \| null  | Non-null only for cross-chapter ranges (e.g. `"Genesis 1:1-2:3"`). |
| `start`       | integer      | Character offset in the raw `answer` string where the reference substring begins. |
| `end`         | integer      | Character offset (exclusive) where the reference substring ends. |

### Worked `run_meta` example

```json
{"type":"run_meta","run_started_at":"2026-07-24T13:22:05+00:00","model":"qwen2.5:3b","options":{"temperature":0.0,"top_p":1.0,"seed":1},"timeout_s":120.0,"git_sha":"4166107aabbcc00112233445566778899aabbccd","git_dirty":false,"corpus_sha256":"5cb6ce27311dda29cb94c10bb968e6185a21f563fb273b2d0e23b833c84f2711","system_prompt_sha256":"c1d2e3f4...(64 hex chars)","questions_sha256":"9f9ac0d3...(64 hex chars)"}
```

### Worked `answer` example

```json
{"type":"answer","question_id":"q001","question":"Where does Paul describe love?","prompt":"[system]\nYou are Shepherd, an offline Bible-study assistant. Answer the user's question clearly and cite the Bible passages you rely on using the form 'Book Chapter:Verse' (e.g. 'John 3:16'). If you are not confident, say so instead of guessing.\n\n[user]\nWhere does Paul describe love?","answer":"Paul's fullest description of love is in 1 Corinthians 13:4-7, where he says love is patient and kind.","refs_in_answer":[{"book":"1 Corinthians","chapter":13,"verse":4,"end_verse":7,"end_chapter":null,"start":41,"end":58}],"model":"qwen2.5:3b","model_tag":"qwen2.5:3b","options":{"temperature":0.0,"top_p":1.0,"seed":1},"system_prompt_sha256":"c1d2e3f4...","timestamp":"2026-07-24T13:22:07+00:00","git_commit_sha":"4166107aabbcc00112233445566778899aabbccd","git_dirty":false,"corpus_sha256":"5cb6ce27311dda29cb94c10bb968e6185a21f563fb273b2d0e23b833c84f2711","latency_ms":1834,"error":null,"retrieval":null,"expected_refs":["1 Corinthians 13:4-7"]}
```

### Worked `answer` example — error case

```json
{"type":"answer","question_id":"q007","question":"…","prompt":"…","answer":null,"refs_in_answer":[],"model":"qwen2.5:3b","model_tag":"qwen2.5:3b","options":{"temperature":0.0,"top_p":1.0,"seed":1},"system_prompt_sha256":"c1d2e3f4...","timestamp":"2026-07-24T13:24:12+00:00","git_commit_sha":"4166107aabbcc00112233445566778899aabbccd","git_dirty":false,"corpus_sha256":"5cb6ce27311dda29cb94c10bb968e6185a21f563fb273b2d0e23b833c84f2711","latency_ms":120037,"error":"TimeoutError: read timeout after 120s","retrieval":null}
```

---

## Rules that matter more than the field list

1. **`answer` is stored verbatim.** Never trim, normalise, or repair it.
   Any post-processing is the citation checker's job and must operate
   on the raw string.

2. **`refs_in_answer` is extraction only.** It records what
   `parse_references` produced; it does NOT filter, score, judge, or
   deduplicate. If the model wrote nonsense that happens to match the
   book+chapter regex, the nonsense reference appears here. That is
   correct — a citation checker needs the raw signal to compute
   precision.

3. **One shared normalisation function, used everywhere.** Any downstream
   comparison must import from `src.corpus.normalize`:

   - `normalize_text(s)` — for comparing model quotes against verse text
     (Unicode NFC, straighten curly quotes, collapse whitespace).
   - `canonical_reference_string(book, chapter, verse, end_verse, end_chapter)`
     — for the printed form of a reference. `Reference.__str__` calls
     through here so citation_check and the runner agree.

   Two normalisers *will* disagree eventually and the disagreement
   will look like a model error. Import from `normalize.py` or add
   yours there.

4. **CorpusUnavailableError is distinct from "verse not found".** If
   `get_verse` / `get_range` raise `CorpusUnavailableError`, the DB is
   missing — a setup problem. If they return `None` / `[]`, the verse
   or chapter genuinely isn't there. Do not conflate.

5. **The system prompt is a versioned file.** It lives at
   `prompts/system.vN.txt` (currently `prompts/system.v1.txt`; see
   `prompts/README.md` for the versioning rule — new versions are new
   files, never in-place edits). Its SHA256 is captured per-run in every
   record. Any edit invalidates comparisons across runs; the hash makes
   that invalidation explicit rather than silent.

---

## `retrieval` object (grounded mode only)

Present on every `answer` record in a grounded-mode run — whether the
model was called or the pipeline abstained. Null in baseline runs.

| Field                 | Type              | Notes |
|-----------------------|-------------------|-------|
| `mode`                | string            | Always `"grounded"` when present. Runs in `"baseline"` mode set `retrieval` to `null` on the answer, not to a mode-tagged object. |
| `parameters`          | object            | `{k, context, threshold, index_version}` — duplicated from `run_meta.retrieval_params` so a single answer row is self-describing. `index_version` is the SHA256 of `src/retrieval/build_index.py` at the time the index was built, so a run can be pinned to the exact indexing logic. |
| `passages`            | array[object]     | Zero or more `RetrievedPassage` objects (see sub-schema below). Empty array on abstention. |
| `abstained`           | boolean           | `true` if the pipeline refused to call the model — either because retrieval returned no passages, or because the top passage's score was above the abstention threshold. `answer` is `null` when `abstained` is `true`. |
| `abstention_reason`   | string \| null    | Non-null iff `abstained` is `true`. Human-readable explanation, e.g. `"no passages retrieved for the query"` or `"top passage score -1.234 above abstention threshold -5.000 (higher = weaker BM25 match)"`. |

### Abstention

Grounded mode never calls the model with an empty context. If retrieval
returns no passages, or the top passage's BM25 score is above the
threshold (BM25 is negative — a "high" score is a weak match), the
pipeline emits an answer record with `answer=null`, `error=null`,
`abstained=true`, and a populated `retrieval` object describing why.
This is a first-class outcome, distinct from `error` (which surfaces
setup failures like a missing DB or a broken model call).

## RetrievedPassage — `retrieval.passages[]` sub-schema

Populated by `src.retrieval.retrieve` and carried into the `retrieval`
key on grounded-mode `answer` records. One element per verse (or verse
range, after context expansion) the grounded pipeline sent to the model.

| Field         | Type            | Notes |
|---------------|-----------------|-------|
| `reference`   | string          | Canonical printed form via `canonical_reference_string` (e.g. `"John 3:16"`, `"1 Corinthians 13:4-7"`, `"Genesis 1:1-2:3"`). The authoritative pointer. |
| `book`        | string          | Canonical book name. Matches the DB and parser namespace. |
| `chapter`     | integer         | Starting chapter of the passage. |
| `verse_start` | integer         | First verse number in the passage. |
| `verse_end`   | integer         | Last verse number in the passage (in `end_chapter` if that is set; otherwise in `chapter`). Equal to `verse_start` for a single-verse passage. |
| `end_chapter` | int \| null     | Non-null only for cross-chapter passages — `null` in the common case. |
| `text`        | string          | Verbatim BSB text of the passage. Multiple verses are joined with a single ASCII space; no re-normalisation. |
| `score`       | number          | BM25 score for keyword-search passages (SQLite's `bm25(verses_fts)`, negative — more negative = better). Direct-lookup passages carry a large-negative sentinel (`-1e9`) so they sit below any BM25 abstention threshold — an explicit user reference is never gated by keyword scoring. |
| `rank`        | integer         | 1-based rank inside the returned list. Ties are broken deterministically by canonical book order → chapter → verse. |

## Compatibility and evolution

- **`retrieval`** is reserved as `null`. When retrieval lands the field
  will hold a small object; the runner will populate it, citation_check
  will read it, no schema break.
- **`expected_refs`** may be absent when the QuestionRecord had none. It
  is not required.
- Additive changes (new optional fields) may be introduced without
  approval; removal or renaming of any field listed above is a breaking
  change and requires explicit sign-off.

---

## CheckerReport — `src/eval/citation_check.py` output

`RunReport.to_json()` writes this shape, and `src/eval/compare_runs.py`
reads it. Frozen after Stage 5 — additive changes only.

```json
{
  "meta": { /* verbatim copy of the run_meta record from the scored run file */ },
  "per_question": [
    {
      "question_id": "q001",
      "question": "…",
      "answer": "…" | null,
      "error": null | "TypeName: message",
      "results": [
        {
          "ref": {
            "book": "1 Corinthians",
            "chapter": 13,
            "verse": 4,
            "end_verse": 7,
            "end_chapter": null,
            "start": 41,
            "end": 58
          },
          "verdict": "RESOLVED" | "UNRESOLVABLE" | "MISQUOTED" | "UNSUPPORTED" | "ERROR",
          "detail": "human-readable explanation from classify_citation",
          "quoted_span": [start_char, end_char] | null,
          "corpus_text": "verbatim BSB text for this reference" | null
        }
      ],
      "counts": {"RESOLVED": 1, "UNRESOLVABLE": 0}
    }
  ],
  "totals": {"RESOLVED": 24, "UNRESOLVABLE": 3, "MISQUOTED": 2,
              "UNSUPPORTED": 1, "ERROR": 0}
}
```

### Rules

1. `per_question` is in **questions.jsonl order** — same order as the
   underlying run file. Not sorted, not deduped.
2. `results` is in the same order as `refs_in_answer` on the underlying
   answer record (positional in the model's text).
3. `counts` may omit zero-valued verdicts per question. `totals` always
   includes all five verdicts, with zeros where applicable.
4. `meta` is copied VERBATIM from the run file's `run_meta`. The report
   never rewrites or normalises provenance — a comparator downstream can
   trust `meta` and the run file to agree byte-for-byte.
5. Error answers (`error != null`) contribute `results: []` and no
   counts, but still appear in `per_question` so the ordering matches
   the run file.
6. The verdict strings are the enum values from `Verdict` in
   `src/eval/citation_check.py`. Additions to `Verdict` are additive
   here too: comparators must handle unknown verdict strings by
   reporting them, never by crashing.

---

## CheckerFixture — `tests/checker_fixtures/*.json` (test-only)

Test-only schema. One JSON object per file. Read by
`tests/test_checker_fixtures.py` and by the fixture generator; not
consumed by any production code path.

```json
{
  "entry":    { /* a single EvalRunEntry `answer` record (line-2+ shape above) */ },
  "expected": {
    "verdicts":     ["RESOLVED", "MISQUOTED"] | "LABELS_PENDING",
    "quoted_spans": [[41, 82], null]          | "LABELS_PENDING",
    "notes":        "free-text pointers for the labeller" | "LABELS_PENDING"
  }
}
```

### Rules

1. Every `expected.*` field is `"LABELS_PENDING"` (literal string) until
   the repo owner labels it. A filled-in `expected.*` field is a claim
   about ground truth; the harness will assert against it.
2. When `expected.verdicts` is `"LABELS_PENDING"`, the test for that
   fixture SKIPS with a clear reason line. It never passes vacuously.
3. When `classify_citation` still raises `NotImplementedError`, every
   fixture test skips regardless of labelling — you can't grade the
   scorer if there is no scorer.
4. Fixtures are constructed mechanically from real BSB text via
   `tests/checker_fixtures/_generate.py`. The generator is committed
   alongside the fixture files so they can be regenerated if the
   `EvalRunEntry` schema evolves — but the harness reads the frozen
   JSON files, not the generator output.
