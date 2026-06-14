# Realdata corpus tests (Stage 0 of `REMEDIATION_PLAN.md`)

End-to-end checks that run the binary-format code against a local corpus of real
NWN modules. They are the verification net the remediation plan builds **first**,
so every later binary fix (C1–C3, M-G*, M-W*, P1) has a regression baseline on
live data instead of only synthetic unit tests.

## Running

```bash
pytest -m realdata                 # whole suite (slow: minutes per module)
pytest -m realdata -k Almraiven    # one module
pytest                             # normal run — realdata is deselected
```

The suite is **deselected by default** (`addopts = -m 'not realdata'` in
`pyproject.toml`). It is opt-in because it extracts and repacks multi-MB
archives.

## Corpus (V1)

- Default location: `test_corpus/` (gitignored). Override with `NWN_TEST_CORPUS`.
- Inventory and provenance: `test_corpus/manifest.json`.
- If the corpus is absent, each test is collected as a single **skipped** case,
  so `pytest -m realdata` is green on a machine without the corpus.
- Current corpus: 5 modules (Almraiven, A Dance with Rogues, LES LIONS DIFFAMES
  [French/cp1252], Midnight, Prophet III). **Known gap:** no Russian module with
  a custom cp1251 `dialog.tlk` yet (needed to fully exercise H9 / M-E cp1251).

## The four runs (V2)

| File | Run | What it pins |
|---|---|---|
| `test_parse_all.py` | V2.1 | Every GFF/NCS resource parses without raising; NCS preamble `T` matches file size. |
| `test_identity_roundtrip.py` | V2.2 | `extract → repack` (no translation) is byte-identical: same resources, type IDs, bytes. A second case repacks with overrides disabled, so type IDs come from the canonical table alone. |
| `test_noop_patch.py` | V2.3 | Injecting `{original: original}` changes no bytes. |
| `test_mock_translate.py` | V2.4 | Full pipeline with a deterministic marker provider; output reads back, GFF fields carry the marker, every `.ncs` reparses with a correct `T`. |

V2.4 uses `MockTranslateProvider` (`_mock_provider.py`) with `use_context=False`,
so the only network surface — `translate` — is replaced and the
world-context / glossary / contextual-dialog subsystems stay out of the loop.

## Baseline (2026-06-14, current `main`)

| Run | Result | Notes |
|---|---|---|
| V2.1 parse-all | **pass** 5/5 | Parser never raises across the corpus. C1 is latent here: with an unknown/under-sized opcode the parser falls back to a 2-byte instruction instead of raising, so a desync rarely surfaces as an exception, and `T == file size` is a pure input-integrity check. C1 is caught downstream in V2.4. |
| V2.2 identity round-trip | **pass** 5/5 (both cases) | ERF read/write is byte-faithful, including type IDs from the canonical table with overrides disabled. (Previously the wrong type-id table was only masked by `type_overrides`.) M-W8 does not corrupt these modules' descriptions. |
| V2.3 no-op patch | **pass** 5/5 | Injectors skip identical text, so a no-op truly changes nothing. |
| V2.4 mock-translate | **xfail** (one known issue) | C1+C2 fixed; one token edge remains — see below. |

### V2.4 history and remaining known issue

- **C1 (fixed)** — CPDOWNSP/CPTOPSP/CPDOWNBP/CPTOPBP now read 6 argument bytes
  (int32 offset + uint16 size), and EQUAL/NEQUAL on structs (type `0x24`) read a
  trailing uint16. String-const *counts* did not move (the lenient parser
  realigns after ~2 bytes), but their *offsets* did — which is what the patcher
  relies on.
- **C2 (fixed)** — the NCS patcher now rewrites the preamble size field `T` after
  a length-changing splice. On LES LIONS DIFFAMES this closed all 142/1092 stale
  `T` failures; output `.ncs` now reparse with `T == file size`.
- **M-T2 / H7 (open)** — one dialog string made entirely of inline tags
  (`<StartHighlight>Partir</Start>`) loses its marker through the
  token-protection path: 1/8473 GFF fields on that module. This is the only
  remaining V2.4 failure and keeps the test `xfail`.

When M-T2/H7 closes, V2.4 turns green: remove the `xfail` marker on
`test_mock_translate_roundtrip` and, if desired, tighten it to `strict=True`.
