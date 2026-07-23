# System prompts

The Shepherd runner loads a versioned system prompt from this directory
and records its SHA256 in every eval run (see `docs/SCHEMAS.md`, field
`system_prompt_sha256`). A new prompt version is a new file with a new
name — never an in-place edit — so runs before and after a prompt
change can be told apart by the hash they recorded.

## Versions

### `system.v1.txt` — current default

**Deliberately minimal.** The baseline run measures how often the model
fabricates Scripture with *nothing in the prompt discouraging it*. It
sets the assistant persona and the citation format and nothing else.
Any subsequent version (v2, v3, …) that changes the fabrication rate
does so measurably against this baseline.

Do not enrich v1. Adding "do not invent citations" or few-shot examples
is a v2 decision, and it comes after there is a v1 number to compare
against.

## Adding a new version

1. Copy the current version to `system.vN.txt` (increment N).
2. Edit the new file.
3. In `src/eval/run_eval.py`, update `DEFAULT_SYSTEM_PROMPT` to point at
   `system.vN.txt`, OR pass `--system-prompt prompts/system.vN.txt` on
   the CLI for a specific run.
4. Add a new subsection here documenting what changed and why.
5. Never delete an old version file — old run files reference their
   hash, and future analyses may want to reproduce them.
