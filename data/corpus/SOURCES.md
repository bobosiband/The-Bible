# Corpus sources

Every corpus or raw-reference file in the repo is recorded here with its
origin, license, and integrity information — including files that no code
path currently loads. Hashes computed locally over on-disk bytes take
precedence over any hash supplied by the source itself.

## BSB — Berean Standard Bible

### Provenance

- **Publisher**: Berean Bible / Bible Hub — https://berean.bible/
- **Redistributor**: AO Lab (Free Use Bible API) — https://bible.helloao.org/
- **Fetched from**: https://bible.helloao.org/api/BSB/complete.json
- **Retrieved on**: 2026-07-23 (UTC)
- **Local file**: `data/corpus/bsb_complete.json` (7,316,221 bytes)

### License

- **License**: Public domain (CC0 1.0 Universal), dedicated 2023-04-30
- **License URL**: https://berean.bible/terms.htm

> **Note on older BSB copies.** Some pre-2023 BSB PDFs and downloads still
> carry an older copyright notice. Those notices are superseded by the
> 2023-04-30 CC0 dedication linked above. If you encounter a BSB file
> anywhere that predates April 2023 and looks copyrighted, it isn't — the
> publisher has since released the entire translation into the public domain.

### Integrity

- **SHA256 (computed locally over `bsb_complete.json` as downloaded)**:
  `5cb6ce27311dda29cb94c10bb968e6185a21f563fb273b2d0e23b833c84f2711`
- **SHA256 (upstream metadata, reported by helloao's `available_translations.json`)**:
  `6cc5238e442b4204b0f617cc5c932bc04f3bae4a0658e6393b0e319653ebe37f`

> **These two hashes differ.** The upstream metadata hash is not a checksum
> of `complete.json`; it appears to be a fingerprint of the underlying
> translation data (or of a different file such as `books.json`) rather
> than of the bundled complete file we actually pulled. This is expected,
> not evidence of corruption. Downstream integrity checks must use the
> locally-computed hash — never trust a source's self-reported hash to
> verify a file supplied by that same source.

### Expected content counts

- **Books**: 66
- **Chapters**: 1,189
- **Verses**: 31,086

> **Why 31,086 and not 31,102?** The number 31,102 is the KJV verse count
> that gets quoted as if it were universal. Different translations make
> different versification decisions — some verses are joined, some
> textually-doubtful verses are relegated to footnotes rather than being
> given a verse number. 31,086 is the BSB's own count as reported by
> `available_translations.json` and is the number the loader asserts
> against. It is intentionally lower than 31,102.

## KJV — Project Gutenberg (raw reference file)

### Provenance

- **Publisher / distributor**: Project Gutenberg — https://www.gutenberg.org/
- **Best-match catalogue entry**: PG ebook **#10**, *The King James Bible*
  — https://www.gutenberg.org/ebooks/10
- **Local file**: `resources/bible.txt` (4,351,186 bytes)
- **File mtime (as-received)**: 2026-02-24
- **Header identifiers**: This particular file has been stripped of the
  standard `*** START OF PROJECT GUTENBERG EBOOK ***` markers and does
  not carry an explicit ebook number in its own header. The Project
  Gutenberg footer (Sections 1–5 of the Project Gutenberg-tm licence and
  contact information for the Literary Archive Foundation) *is* intact
  at the end of the file. Text content matches the KJV verbatim
  (`1:1 In the beginning God created the heaven and the earth.` etc.).
  PG #10 is the canonical PG KJV.

### License

- **KJV text itself**: **Public domain in the United States** (and in
  most jurisdictions worldwide, notwithstanding the UK Crown copyright
  peculiarity for KJV printings in the UK).
- **Project Gutenberg trademark licence** (present verbatim in the file's
  footer): the KJV text is public domain, but the PG *trademark* licence
  attached to this file governs redistribution *under the Project
  Gutenberg name*. If we ever redistribute this file with the PG footer
  attached, the trademark licence conditions apply. Extracting the
  underlying KJV text without the PG trademark and boilerplate is
  unconstrained (it is public-domain text).

### Integrity

- **SHA256 (computed locally over `resources/bible.txt` as-is)**:
  `a7823af1c27c0409f4a0f75c78c396b475591c2ae79ec52bd58638297770473f`

### Loaded by

> **NOT LOADED BY ANY CODE PATH.** `resources/bible.txt` is present as a
> raw reference file only. Nothing in `src/` reads it. The active corpus
> is BSB (`data/corpus/bible.db`); the KJV file is retained for future
> comparison / cross-reference work should the repo owner want it, and
> is *not* wired into ingest, parser, eval, or tests.

## Files in `data/corpus/` (audited 2026-07-24)

| File                | Status     | Notes |
|---------------------|------------|-------|
| `SOURCES.md`        | Committed  | This file. |
| `bsb_complete.json` | Gitignored | Raw download cache. Regenerable by re-running `python -m src.ingest.bsb`. |
| `bible.db`          | Gitignored | SQLite corpus loaded from `bsb_complete.json`. |

## Files in `resources/` (audited 2026-07-24)

| File          | Status    | Notes |
|---------------|-----------|-------|
| `bible.txt`   | Committed | Project Gutenberg KJV, raw reference only, **not loaded**. See KJV section above. |

No undocumented files present in either directory.
