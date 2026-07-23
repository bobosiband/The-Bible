# Shepherd

An offline Bible-study assistant. The goal is to fine-tune a small open-weights
model (Qwen3.5-2B) so it can answer study questions and quote Scripture without
any paid API or cloud dependency. It has to run on a laptop with 16GB of RAM
and no GPU.

This repo currently contains only the data layer and the eval scaffolding.
Training, retrieval, and the UI will come later.

---

## Layout

```
src/
  ingest/       download a Bible corpus into SQLite
  corpus/       parse Scripture references and look verses up
  eval/         run questions through a local Ollama model
data/
  corpus/       bible.db (gitignored), SOURCES.md (committed)
  eval/
    questions.jsonl   your eval questions (empty; you write these)
    runs/             timestamped eval outputs (gitignored)
tests/          pytest suite (mostly the reference parser for now)
resources/      pre-existing raw files (KJV .txt from Project Gutenberg)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ingest a Bible corpus

```bash
python -m src.ingest.bsb
```

Downloads the Berean Standard Bible from [bible.helloao.org](https://bible.helloao.org)
and loads it into `data/corpus/bible.db`. Idempotent — re-runs are cheap and
skip work that's already done. Source and license go into
`data/corpus/SOURCES.md`.

**Public-domain translations only.** Do not add code that downloads NIV, ESV,
NASB, CSB, NLT, or any other copyrighted translation.

## Look up a verse

```python
from src.corpus.references import parse_references, get_verse, get_range

parse_references("Compare John 3:16 with 1 Cor 13:4-7 and Ps 23.")
# → [Reference(book='John', chapter=3, verse=16),
#    Reference(book='1 Corinthians', chapter=13, verse=4, end_verse=7),
#    Reference(book='Psalms', chapter=23)]

get_verse("John", 3, 16)                 # → "For God so loved the world..."
get_range("1 Corinthians", 13, 4, 7)     # → [(4, "Love is patient..."), ...]
```

## Run an eval

Start Ollama and pull the model you want:

```bash
ollama serve &
ollama pull qwen2.5:3b
```

Then run every question in `data/eval/questions.jsonl` through it:

```bash
python -m src.eval.run_eval --model qwen2.5:3b
```

Output lands in `data/eval/runs/<UTC-timestamp>.jsonl`.

### `data/eval/questions.jsonl` schema

One JSON object per line. The file **starts and stays empty until you
personally write questions** — placeholders or auto-generated questions
poison the baseline eval that they'd be measured against.

```
{
  "id":            "q001",             // required, unique per file
  "question":      "…",                // required, the prompt sent to the model
  "expected_refs": ["John 3:16"],      // PROVISIONAL — see note below
  "notes":         ""                  // optional, free text
}
```

> **`expected_refs` is provisional.** It's reserved for a
> reference-based citation metric, but the decision on whether the
> Shepherd eval is reference-based, text-based, or both has not been
> made yet. Populate it only if it's useful to you; downstream tools
> won't treat its absence as an error. See `docs/SCHEMAS.md` when it
> exists.

## Corpus tests and `--require-corpus`

A slice of the test suite asserts things about the real ingested BSB
corpus (verse counts, whole-text sweep for JSON leakage, book-name
consistency between parser and DB). Those tests carry the `@pytest.mark.corpus`
marker and skip when `data/corpus/bible.db` is absent.

A green suite without the corpus proves nothing about corpus fidelity.
To turn those skips into hard failures — recommended in CI or before
any commit that touches the ingest or corpus code:

```bash
pytest --require-corpus         # or: SHEPHERD_REQUIRE_CORPUS=1 pytest
```

When you run pytest normally and any corpus test skips, the run prints
a loud summary line so you know you're not getting the full audit.

## Tests

```bash
pytest
```

## Contributing / commit style

Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.
Small, logical commits. No AI attribution in commit messages or PR bodies.
