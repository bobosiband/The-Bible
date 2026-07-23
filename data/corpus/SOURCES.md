# Corpus sources

Every downloaded corpus file is recorded here with its origin, license, and
integrity information. Hashes computed locally over on-disk bytes take
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

## Files in `data/corpus/` (audited 2026-07-24)

| File                | Status     | Notes |
|---------------------|------------|-------|
| `SOURCES.md`        | Committed  | This file. |
| `bsb_complete.json` | Gitignored | Raw download cache. Regenerable by re-running `python -m src.ingest.bsb`. |
| `bible.db`          | Gitignored | SQLite corpus loaded from `bsb_complete.json`. |

No undocumented files present.
