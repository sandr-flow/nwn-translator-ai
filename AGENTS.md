# AGENTS.md

Canonical conventions for coding agents. [CLAUDE.md](CLAUDE.md) points here.

## Language conventions

- **Replies to the user are in Russian.**
- **Code stays in English** (identifiers, comments, docstrings). Existing Russian UI strings (`frontend/src/locales.js` RU block, FastAPI error messages) stay Russian.

## Working principles

### Think before coding
Don't assume. Don't hide confusion. Surface tradeoffs before touching code.

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### Simplicity first
Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- Sanity check: would a senior engineer call this overcomplicated? If yes, simplify.

### Surgical changes
Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently. The project already enforces black (line length 100) and mypy — don't fight either.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that **your** changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

### Goal-driven execution
Define success criteria up front. Loop until verified.

Transform fuzzy tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass."
- "Fix the bug" → "Write a test that reproduces it, then make it pass."
- "Refactor X" → "Ensure tests pass before and after."

For multi-step tasks, state a brief plan with a verification step per item:
```
1. [step] → verify: [check]
2. [step] → verify: [check]
```
Strong success criteria let you loop independently. Weak criteria ("make it work") force constant clarification.

### Documentation
Public docs describe the product as it is. Do not pin historical data: old metrics, pass/fail snapshots, "we used to X / then dropped Y", changelog-in-disguise. Scratch and one-off measurements stay in `docs/local/` (gitignored).

## Project

Translator for Neverwinter Nights (NWN/NWN:EE) `.mod` / `.erf` / `.hak` archives: extract strings from GFF and compiled NCS, translate via an OpenAI-compatible provider (OpenRouter or POLZA.AI, chosen by API key prefix), byte-patch them back.

User-facing surface: FastAPI + Vue web UI and the Python library (`translate_module`). There is no CLI.

## Common commands

```bash
pip install -e ".[dev]"          # core + tests/lint
pip install -e ".[web]"          # FastAPI / uvicorn

pytest
pytest --cov=src
black src tests
pylint src/nwn_translator        # advisory
mypy src                         # expected to pass (black line length 100 too)

python -m nwn_translator.web     # or nwn-translate-web; Windows: run-web-ui.bat
cd frontend && npm install && npm run dev   # http://localhost:5173, /api → :8000

python scripts/stage.py unpack module.mod --out work
docker compose -f docker/docker-compose.yml up --build   # http://127.0.0.1:8080
```

## Local environment

- Venv: `.venv/`. Env template: `.env.example`.
- Provider from `NWN_TRANSLATE_API_KEY` prefix: `sk-or-...` OpenRouter, `pza...` POLZA.AI, else OpenRouter.
- Model: web/API request or `TranslationConfig(model=...)`. Unset → `DEFAULT_MODEL` in `config.py` (`google/gemini-3.8-flash`).
- Injection encoding: `module_string_encoding_for_target_lang` (`cp1251` / `cp1250` / `cp1252`). Offered languages are the keys of `_LANG_TO_WINDOWS_ENCODING` in `config.py`.
- Do not commit `workspace/`, `check_this/`, `docs/local/`, `frontend/dist/`, `frontend/node_modules/`, caches, logs.

## Pipeline

`translate_module` / `run_translation_pipeline` in `main.py` build `PipelineState` and call `run_pipeline` in `pipeline/stages.py`. Isolated stages: `scripts/stage.py`. Artifacts: `pipeline/artifacts.py`.

1. **Unpack** — `formats/erf.py`
2. **World scan** — `context/world_context.py` (when `use_context`)
3. **Extract** — `resources.py` maps each extension to its loader, extractor and injector; extractors live in `extractors/` (GFF/NCS parse: `formats/gff.py`, `formats/ncs.py`). Only embedded strings; StrRef-only fields are left for the player's `dialog.tlk`.
4. **Entities** — `context/entity_candidates.py` (candidates from extracted name fields) and `context/entity_extractor.py` (names the model finds in texts)
5. **Glossary** — `glossary_curator.py` curates the candidates, `glossary_builder.py` translates them into the `Glossary` of `glossary.py`; the static race terms of `race_dictionary.py` are merged in at prompt time. `llm_batches.py` runs the batched requests of stages 4-5.
6. **Translate** — `translators/translation_manager.py` (non-dialog strings: `script_gate.py` approves NCS literals, `work_plan.py` plans single/batch requests, `model_calls.py` sends them with the timeout and retry policy) and `translators/context_translator.py` (dialogs as whole conversations, planned by `dialog_plan.py`). `token_handler.py` protects NWN tokens and inline tags.
7. **Inject** — `injectors/` over the patchers in `formats/gff.py` / `formats/ncs.py` (byte-patch, not a full GFF rewrite)
8. **Repack** — `formats/erf.py`

Extractors copy each selected field record offset into item metadata. GFF injection patches these extracted occurrences by `(resource, item_id)`; rebuild re-extracts offsets from the current file.

CExoLocString: parser takes the first non-empty substring; patcher writes one substring with LanguageID 0 (community standard for languages with no official NWN id). Extra gender/language variants are collapsed, with a warning.

`rebuild_module` re-injects editor edits by `item_id` (on-disk text is already translated).

## Extractor / Injector contract

- New file type: an extractor (`BaseExtractor.extract` → `ExtractedContent`) plus one entry in `RESOURCE_KINDS` (`resources.py`: extension → extractor, loader, injector); `TRANSLATABLE_TYPES` derives from it. A single-struct GFF kind is a `FieldSpec` table in `extractors/simple_extractors.py`.
- All GFF resources use `inject_gff` (`injectors/gff_injector.py`) with extracted items and their `record_offset`. NCS keeps its specialized bytecode patcher (`inject_ncs`).
- `.git`: `extractors/git_fields.py` defines `INSTANCE_FIELDS` (per instance list: the translatable fields with their type and context) and `INSTANCE_NESTED_ITEM_LISTS`; `GitExtractor` supplies concrete field records to the shared injector.
- Engine tags (`WP_`, `DST_`, `NW_`, `POST_`, `ARCH_`, `YOURTAGHERE`, spaceless identifiers) are not translated. Source of truth: `context/string_filters.py` (`ENGINE_TAG_PREFIXES`, `should_skip_entity_source_text`).
- `.git` instances bake into a save on first area visit; later re-translation affects only unvisited areas.

## Other subsystems

- **`ai_providers/`** — `base.py` (the `TranslationProvider` protocol the pipeline uses, data types), `openrouter_provider.py` (the only implementation: OpenAI-compatible transport and the model tasks), `errors.py` (transient retry policy, error mapping), `ncs_gate.py` (NCS gate payload, strict verdicts, bisection), `batch_payload.py`, `openrouter_models.py` (model catalog, reasoning-effort clamp); `polza_provider.py` only changes the base URL.
- **NCS selection** — `extractors/ncs_context.py` traces argument-specific bytecode consumers; `nss_index.py` supplies engine argument roles and matching-source context, never module-wide proof; `ncs_concat.py` merges string concatenations into one unit. `ncs_extractor.py` emits candidates for the model gate. See `docs/ncs-translation.md` for validation.
- **`prompts/`** — `_builder.py` (translation prompts, content profiles, dialog system prompt), `dialog.py` (dialog user, repair and retry messages), `token_retry.py`, `terminology.py` (entity, curation and glossary prompts), `ncs_gate.py`, `examples.py` (per-language few-shot examples).
- **`web/`** — FastAPI app (`app.py`, `routes.py`), task manager (`task_manager.py`) and editor rows (`editor.py`). Persistence is raw `sqlite3` (no ORM): schema and additive migrations (`CREATE TABLE IF NOT EXISTS`, `_ADDED_COLUMNS` / `ALTER TABLE` in `_migrate`) live in `database.py`. No Alembic.
- **`scripts/`** — `stage.py` (isolated stages), `dump_gff_strings.py` (dump CExoLocString from a file or module), `evaluate_ncs_corpus.py` and `run_ncs_translation_compare.py` (NCS selection measurements), `equivalence.py` (golden-master harness: full runs against a deterministic fake model, recorded and compared).

## Frontend

Vue 3 with Composition API (`<script setup>`).
Bundler: Vite 5.
Styles: Tailwind CSS 3 (PostCSS, `frontend/src/style.css`).
State in `composables/`; i18n in `locales.js`.

## Versioning

Semantic versioning, `MAJOR.MINOR.PATCH`. The version lives in three places
that must match: `pyproject.toml`, `frontend/package.json` and
`frontend/src/version.js` (shown in the UI footer). The release channel label
is a separate locale string.

- **PATCH** — bug fixes and internal changes with no visible change in
  translation behaviour or the API.
- **MINOR** — new features, and any change to what the pipeline sends to the
  model or writes into the module (batching, prompts, extractors, glossary).
- **MAJOR** — `1.0.0` when the product leaves beta; afterwards, incompatible
  changes to the API, artifacts or the library interface.

Bump the version in the last commit of a change set, before merging into
`main`. Older releases used a closed-beta scheme `0.1xxx` (`0.1004` reads as
`0.10.4`).

## Test expectations

- pytest `addopts` deselects `realdata` (`pyproject.toml`). Unit tests build GFF dicts by hand.
- Corpus e2e: `pytest -m realdata` (`test_corpus/` or `NWN_TEST_CORPUS`). Skips if the corpus is absent. See `tests/realdata/README.md`.
- Extractor/injector changes need regression tests for the positive case and internal-tag skips.
- `tests/test_prompt_snapshots.py` pins the prompt texts and `tests/test_extractor_snapshot.py` the extractor output; a changed snapshot means a changed request or extraction, so update one only for an intended change.
