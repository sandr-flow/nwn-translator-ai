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
- Model: web/API request or `TranslationConfig(model=...)`. Unset → `OpenRouterProvider.DEFAULT_MODEL` (`google/gemini-3.8-flash`).
- Injection encoding: `module_string_encoding_for_target_lang` (`cp1251` / `cp1250` / `cp1252`). Offered languages are the keys of `_LANG_TO_WINDOWS_ENCODING` in `config.py`.
- Do not commit `workspace/`, `check_this/`, `docs/local/`, `frontend/dist/`, `frontend/node_modules/`, caches, logs.

## Pipeline

`translate_module` / `run_translation_pipeline` in `main.py` build `PipelineState` and call `run_pipeline` in `pipeline/stages.py`. Isolated stages: `scripts/stage.py`. Artifacts: `pipeline/artifacts.py`.

1. **Unpack** — `file_handlers/erf_reader.py`
2. **World scan** — `context/world_context.py` (when `use_context`)
3. **Extract** — `extractors/` (GFF/NCS parse: `gff_parser.py`, `gff_handler.py`, `ncs_parser.py`). Only embedded strings; StrRef-only fields are left for the player's `dialog.tlk`.
4. **Entities** — `context/entity_extractor.py`
5. **Glossary** — `glossary_curator.py`, then `glossary.py` / `race_dictionary.py`
6. **Translate** — `translators/translation_manager.py` (batches) and `context_translator.py` (dialogs). `token_handler.py` protects NWN tokens and inline tags.
7. **Inject** — `injectors/` + `gff_patcher.py` / `ncs_patcher.py` / `git_injector.py` (byte-patch, not a full GFF rewrite)
8. **Repack** — `file_handlers/erf_writer.py`

Extractors must keep `_record_offsets`; injectors must patch the same field names. Mismatches silently drop translations.

CExoLocString: parser takes the first non-empty substring; patcher writes one substring with LanguageID 0 (community standard for languages with no official NWN id). Extra gender/language variants are collapsed, with a warning.

`rebuild_module` re-injects editor edits by `item_id` (on-disk text is already translated).

## Extractor / Injector contract

- New file type: extractor class (`SUPPORTED_TYPES` → `ExtractedContent`), register in `extractors/__init__.py`, add to `TRANSLATABLE_TYPES` in `config.py`.
- Simple GFF resources: `GenericInjector` (`FIELD_MAP`). Dialogs, journals, `.git`, `.ncs` have their own injectors.
- `.git`: keep `GitExtractor` and `git_injector.patch_git_file` in sync via `INSTANCE_LISTS` and `INSTANCE_NESTED_ITEM_LISTS`.
- Engine tags (`WP_`, `DST_`, `NW_`, `POST_`, `ARCH_`, `YOURTAGHERE`, spaceless identifiers) are not translated. Source of truth: `context/string_filters.py` (`ENGINE_TAG_PREFIXES`, `should_skip_entity_source_text`).
- `.git` instances bake into a save on first area visit; later re-translation affects only unvisited areas.

## Other subsystems

- **`ai_providers/`** — `openrouter_provider.py` (shared OpenAI-compatible logic); `polza_provider.py` only changes the base URL.
- **NCS selection** — `extractors/ncs_context.py` traces argument-specific bytecode consumers; `nss_index.py` supplies engine argument roles and matching-source context, never module-wide proof. `ncs_extractor.py` emits candidates for the model gate. See `docs/ncs-translation.md` for validation.
- **`prompts/`** — prompt builder and per-language examples.
- **`web/`** — FastAPI + task manager. Persistence is raw `sqlite3` (no ORM): schema and additive migrations (`CREATE TABLE IF NOT EXISTS`, `_migrate` / `ALTER TABLE`) live in `database.py`. No Alembic.
- **`scripts/dump_gff_strings.py`** — dump CExoLocString from a file or module.

## Frontend

Vue 3 with Composition API (`<script setup>`).
Bundler: Vite 5.
Styles: Tailwind CSS 3 (PostCSS, `frontend/src/style.css`).
State in `composables/`; i18n in `locales.js`.

## Test expectations

- pytest `addopts` deselects `realdata` (`pyproject.toml`). Unit tests build GFF dicts by hand.
- Corpus e2e: `pytest -m realdata` (`test_corpus/` or `NWN_TEST_CORPUS`). Skips if the corpus is absent. See `tests/realdata/README.md`.
- Extractor/injector changes need regression tests for the positive case and internal-tag skips.
