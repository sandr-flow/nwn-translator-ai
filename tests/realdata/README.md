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
- If the corpus is absent, each test is collected as a single **skipped** case,
  so `pytest -m realdata` is green on a machine without the corpus.
- Current corpus: 6 modules (Almraiven, A Dance with Rogues, LES LIONS DIFFAMES
  [French/cp1252], Midnight, Prophet III, Torn Asunder part 1). StrRef-only
  fields are left untouched (the engine resolves them from the player's
  `dialog.tlk`).

## The runs

| File | What it pins |
|---|---|
| `test_parse_all.py` | Every GFF/NCS resource parses without raising; NCS preamble `T` matches file size. |
| `test_identity_roundtrip.py` | `extract → repack` (no translation) is byte-identical: same resources, type IDs, bytes. A second case repacks with overrides disabled, so type IDs come from the canonical table alone. |
| `test_noop_patch.py` | Injecting `{original: original}` changes no bytes. |
| `test_mock_translate.py` | Full pipeline with a deterministic marker provider; output reads back, GFF fields carry the marker, every `.ncs` reparses with a correct `T`. |
| `test_encoding_diacritics.py` | For modules with a declared non-English language in the manifest (currently the French cp1252 module): extraction with the matching `source_encoding` yields ≥20 diacritic strings and zero Cyrillic mojibake; marker-patching those strings and re-extracting returns them byte-exactly. Skipped for English/undeclared modules. |
| `test_rebuild_item_id.py` | Almraiven-only: after mock-translate, `rebuild_module` applies GFF edits addressed by `(file, item_id)` (not by original text) and neighbouring strings stay put. |
| `test_ncs_selection.py` | Reviewed NCS examples with/without matching source: extractor → model-gate stub → translation manager → injector; approved speech changes and all other constants stay intact. This tests routing, not live-model accuracy. |

`test_mock_translate.py` uses `MockTranslateProvider` (`_mock_provider.py`) with
`use_context=False`, so the only network surface — `translate` — is replaced and
the world-context / glossary / contextual-dialog subsystems stay out of the loop.

See [NCS translation and validation](../../docs/ncs-translation.md) for the
production selection contract and how to review live-model decisions.

