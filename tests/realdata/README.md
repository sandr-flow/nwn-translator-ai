# Realdata corpus tests

End-to-end checks that run the binary-format code against a local corpus of real
NWN modules. They catch regressions in ERF/GFF/NCS parse, patch, and repack that
synthetic unit tests miss.

## Running

```bash
pytest -m realdata                 # whole suite (slow: minutes per module)
pytest -m realdata -k Almraiven    # one module
pytest                             # normal run — realdata is deselected
```

The suite is **deselected by default** (`addopts = -m 'not realdata'` in
`pyproject.toml`). It is opt-in because it extracts and repacks multi-MB
archives.

## Corpus

- Default location: `test_corpus/` (gitignored). Override with `NWN_TEST_CORPUS`.
- Inventory and provenance: `test_corpus/manifest.json`.
- If the corpus is absent, each test is collected as a single **skipped** case,
  so `pytest -m realdata` is green on a machine without the corpus.
- Current corpus: 6 modules (Almraiven, A Dance with Rogues, LES LIONS DIFFAMES
  [French/cp1252], Midnight, Prophet III, Torn Asunder part 1). TLK resolution
  was removed from the pipeline — StrRef-only fields are left untouched — so
  custom-TLK strings are intentionally out of scope.

## The five runs

| File | What it pins |
|---|---|
| `test_parse_all.py` | Every GFF/NCS resource parses without raising; NCS preamble `T` matches file size. |
| `test_identity_roundtrip.py` | `extract → repack` (no translation) is byte-identical: same resources, type IDs, bytes. A second case repacks with overrides disabled, so type IDs come from the canonical table alone. |
| `test_noop_patch.py` | Injecting `{original: original}` changes no bytes. |
| `test_mock_translate.py` | Full pipeline with a deterministic marker provider; output reads back, GFF fields carry the marker, every `.ncs` reparses with a correct `T`. |
| `test_encoding_diacritics.py` | For modules with a declared non-English language in the manifest (currently the French cp1252 module): extraction with the matching `source_encoding` yields ≥20 diacritic strings and zero Cyrillic mojibake; marker-patching those strings and re-extracting returns them byte-exactly. Skipped for English/undeclared modules. |

`test_mock_translate.py` uses `MockTranslateProvider` (`_mock_provider.py`) with
`use_context=False`, so the only network surface — `translate` — is replaced and
the world-context / glossary / contextual-dialog subsystems stay out of the loop.

## Baseline (current `main`)

| Run | Result | Notes |
|---|---|---|
| parse-all | **pass** 6/6 | Parser never raises across the corpus. |
| identity round-trip | **pass** 6/6 (both cases) | ERF read/write is byte-faithful, including type IDs from the canonical table with overrides disabled. |
| no-op patch | **pass** 6/6 | Injectors skip identical text, so a no-op truly changes nothing. |
| mock-translate | **pass** 6/6 | NCS opcode sizes and preamble `T` updates are correct; fields that sanitize to empty (punctuation-only / token-only) are exempt from the marker metric. |
| encoding diacritics | **pass** on declared non-English modules | French/cp1252 module covered. |

### H6 batch-dedup metric (Almraiven, mock-translate)

Placeholder nonces are derived from the source text (not a random
`secrets.token_hex(4)`), so two equal token-bearing strings sanitize identically
and collapse to one batch entry / API call. Measured on Almraiven (the
`Deduplicated N items down to M unique texts` log line):

| | Items | Unique texts |
|---|---|---|
| Before (random nonce) | 35833 | 17626 |
| After (deterministic nonce) | 35833 | **16247** |

−1379 unique batch entries (~7.8%) = that many fewer paid LLM calls on this one
module.
