# CLAUDE.md — house rules for AI assistants working on Shepherd

## What Shepherd is

An offline Bible-study assistant, built by fine-tuning Qwen3.5-2B. Everything
must run locally: CPU only, 16GB RAM laptop, no paid APIs, no GPU.

The repo owner is a strong Python developer who is new to ML. Favour clear,
readable code over clever code, and add brief comments explaining ML-specific
or non-obvious design decisions. Do not comment ordinary Python.

## Hard rules

1. **Public-domain Bible texts only.** Never download, bundle, or reference
   NIV, ESV, NASB, CSB, NLT, NKJV, or any other copyrighted translation.
   Approved: Berean Standard Bible (BSB), King James Version (KJV), World
   English Bible (WEB), American Standard Version (ASV). Every downloaded
   corpus file must have its source URL and license recorded in
   `data/corpus/SOURCES.md`.

2. **No AI attribution in git history.** Do not add `Co-Authored-By: Claude`,
   `Generated with Claude Code`, `🤖`, or any similar trailer to commit
   messages or PR descriptions. Commits should read as the owner's authored
   work.

3. **Minimal dependencies.** Currently allowed: `requests`, `ollama`,
   `pytest`. Ask before adding anything else — including "helper" libraries
   for ML, HTTP, or CLI parsing. The standard library is usually enough.

4. **No speculative stubs.** Do not create empty files or placeholder
   functions for future features. In particular:
   - `src/eval/citation_check.py` — the owner is writing this himself.
   - Training code, LoRA config, notebooks — not yet.
   - Retrieval, embeddings, vector search — not yet.
   - Web UI, server, API — not yet.

## Commit conventions

Conventional Commits. One logical change per commit.

```
feat: add scripture reference parser
fix: handle "1 Cor" with space in reference regex
test: cover malformed references
docs: document questions.jsonl schema
chore: initialize repo skeleton
refactor: split ingest helpers
```

Run `pytest` before each commit that touches code. If tests fail, fix the
underlying issue — do not skip hooks or commit around them.

## Working style

- Start non-trivial tasks with a short plan; wait for approval before large
  changes.
- Small commits. When a task spans several files, split into logical commits
  rather than dumping everything into one.
- Prefer editing existing files to creating new ones.
- If you need a new dependency, stop and ask.
