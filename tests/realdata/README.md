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
  a custom cp1251 `dialog.tlk` yet (needed for M-E cp1251 round-trip). TLK
  resolution has been removed entirely — StrRef-only fields are left untouched —
  so custom-TLK strings are intentionally out of scope.

## The five runs (V2, M-E)

| File | Run | What it pins |
|---|---|---|
| `test_parse_all.py` | V2.1 | Every GFF/NCS resource parses without raising; NCS preamble `T` matches file size. |
| `test_identity_roundtrip.py` | V2.2 | `extract → repack` (no translation) is byte-identical: same resources, type IDs, bytes. A second case repacks with overrides disabled, so type IDs come from the canonical table alone. |
| `test_noop_patch.py` | V2.3 | Injecting `{original: original}` changes no bytes. |
| `test_mock_translate.py` | V2.4 | Full pipeline with a deterministic marker provider; output reads back, GFF fields carry the marker, every `.ncs` reparses with a correct `T`. |
| `test_encoding_diacritics.py` | M-E | For modules with a declared non-English language in the manifest (currently the French cp1252 module): extraction with the matching `source_encoding` yields ≥20 diacritic strings and zero Cyrillic mojibake; marker-patching those strings and re-extracting returns them byte-exactly. Skipped for English/undeclared modules. |

V2.4 uses `MockTranslateProvider` (`_mock_provider.py`) with `use_context=False`,
so the only network surface — `translate` — is replaced and the
world-context / glossary / contextual-dialog subsystems stay out of the loop.

## Baseline (2026-06-14, current `main`)

| Run | Result | Notes |
|---|---|---|
| V2.1 parse-all | **pass** 5/5 | Parser never raises across the corpus. C1 is latent here: with an unknown/under-sized opcode the parser falls back to a 2-byte instruction instead of raising, so a desync rarely surfaces as an exception, and `T == file size` is a pure input-integrity check. C1 is caught downstream in V2.4. |
| V2.2 identity round-trip | **pass** 5/5 (both cases) | ERF read/write is byte-faithful, including type IDs from the canonical table with overrides disabled. (Previously the wrong type-id table was only masked by `type_overrides`.) M-W8 does not corrupt these modules' descriptions. |
| V2.3 no-op patch | **pass** 5/5 | Injectors skip identical text, so a no-op truly changes nothing. |
| V2.4 mock-translate | **pass** 5/5 (2026-07-28) | C1+C2 fixed; the inline-tag marker loss is resolved — see below. |

### V2.4 history and remaining known issue

- **C1 (fixed)** — CPDOWNSP/CPTOPSP/CPDOWNBP/CPTOPBP now read 6 argument bytes
  (int32 offset + uint16 size), and EQUAL/NEQUAL on structs (type `0x24`) read a
  trailing uint16. String-const *counts* did not move (the lenient parser
  realigns after ~2 bytes), but their *offsets* did — which is what the patcher
  relies on.
- **C2 (fixed)** — the NCS patcher now rewrites the preamble size field `T` after
  a length-changing splice. On LES LIONS DIFFAMES this closed all 142/1092 stale
  `T` failures; output `.ncs` now reparse with `T == file size`.
- **H7 (fixed)** — engine tokens and inline tags nested inside a dash action
  marker (`-glances at <FirstName>-`) are now protected before the LLM call and
  tracked for validation, so corruption inside a marker is caught. This was a
  distinct token edge; it does not touch this module's remaining failure (no
  dash marker is involved) and does not change its marker count.
- **inline-tag-only field (fixed 2026-07-28)** — dialog strings whose only word
  sat entirely inside inline tags (`<StartHighlight>Partir</Start>`,
  `<StartAction>Attack</Start>`) silently skipped translation. Root cause: the
  passthrough gate's placeholder-stripping regex used a permissive greedy
  `[A-Za-z0-9_]+` core, which on
  `__NWN_INLINE_x_0__Attack__NWN_INLINE_x_1__` matched from the first
  placeholder to the last `__`, swallowing the word — the string was then
  misclassified as "tokens/punctuation only" and never sent to the provider
  (that is why `TokenHandler` was clean in isolation). The regex now matches
  the exact placeholder core (8-hex nonce + counter). A corpus inventory found
  21 such fields (20 Midnight + 1 LES LIONS); the other 410 marker-less fields
  were legitimately untranslatable (punctuation-only like `. . .`, or
  token+punctuation like `"<Deity>!"`) — the V2.4 metric now exempts fields
  that sanitize to empty, the `xfail` marker is removed, and the run is green.

**Baseline update (2026-07-24):** the inline-tag-only marker loss is not limited
to LES LIONS — every corpus module currently xfails V2.4 on the same issue class
(strings made entirely of `<StartAction>…</Start>` / `<CUSTOM…>` tags, plus
punctuation-only strings like `. . .`). Measured on Midnight: 26/8118 fields,
byte-identical between the branch head and the working tree (verified via a
`git worktree` + `PYTHONPATH` run), so the source-encoding work introduced no
regression here.

**Baseline update (2026-07-27):** tag stripping is now gated on deviation from
the original's tag multiset, so legitimately unpaired Start-tags (e.g. Midnight's
`<CUSTOM1004>(sigh)</Start>` convention, 171 strings) round-trip `exact_valid`
on the first try instead of burning the retry budget and losing the tag. The
V2.4 marker metric is unchanged (26/8118 on Midnight): the marker is plain text
and survived tag loss in prose strings, and the remaining 26 fields are the
tag-only / punctuation-only class whose marker loss happens outside
`TokenHandler`.

**Baseline update (2026-07-28):** the class above is resolved (greedy
placeholder regex in the passthrough gate — see the fixed bullet); V2.4 runs
green 5/5 with the metric exempting fields that sanitize to empty.

### H6 batch-dedup metric (Almraiven, mock-translate)

Placeholder nonces are now derived from the source text instead of a random
`secrets.token_hex(4)`, so two equal token-bearing strings sanitize identically
and collapse to one batch entry / API call. Measured on Almraiven (the
`Deduplicated N items down to M unique texts` log line):

| | Items | Unique texts |
|---|---|---|
| Before (random nonce) | 35833 | 17626 |
| After (deterministic nonce) | 35833 | **16247** |

−1379 unique batch entries (~7.8%) = that many fewer paid LLM calls on this one
module. To reproduce a before/after on an older commit, run the worktree's own
source via `PYTHONPATH=<worktree>/src` — the editable install otherwise imports
the main tree.
