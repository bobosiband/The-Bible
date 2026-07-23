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
| `system_prompt_sha256` | string | SHA256 over the raw bytes of `prompts/system.txt`. |

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
| `retrieval`            | null            | **Reserved.** Always `null` in Stage 3. Populated in a later stage when retrieval lands. |
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
{"type":"run_meta","run_started_at":"2026-07-24T13:22:05+00:00","model":"qwen2.5:3b","options":{"temperature":0.0,"top_p":1.0,"seed":1},"timeout_s":120.0,"git_sha":"4166107aabbcc00112233445566778899aabbccd","git_dirty":false,"corpus_sha256":"5cb6ce27311dda29cb94c10bb968e6185a21f563fb273b2d0e23b833c84f2711","system_prompt_sha256":"c1d2e3f4...(64 hex chars)"}
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
   `prompts/system.txt`. Its SHA256 is captured per-run in every record.
   Any edit invalidates comparisons across runs; the hash makes that
   invalidation explicit rather than silent.

---

## Compatibility and evolution

- **`retrieval`** is reserved as `null`. When retrieval lands the field
  will hold a small object; the runner will populate it, citation_check
  will read it, no schema break.
- **`expected_refs`** may be absent when the QuestionRecord had none. It
  is not required.
- Additive changes (new optional fields) may be introduced without
  approval; removal or renaming of any field listed above is a breaking
  change and requires explicit sign-off.
