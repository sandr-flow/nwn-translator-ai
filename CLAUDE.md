# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language conventions

- **User-facing replies must be in Russian.** Every chat message back to the user (explanations, status updates, summaries, questions) is written in Russian.
- **Code stays in English.** All identifiers, comments, and docstrings are written in English regardless of the chat language. Existing user-facing UI strings in Russian (for example `frontend/src/locales.js` RU block and FastAPI error messages) remain as they are; do not translate them to English.

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

## Project

AI-powered translator for Neverwinter Nights (NWN/NWN:EE) modules. It takes a `.mod`, `.erf`, or `.hak` archive, extracts translatable strings from binary GFF resources and compiled NCS scripts, translates them via an OpenAI-compatible provider, and byte-patches the strings back into a new archive without fully rewriting GFF files.

The active user-facing surface is a FastAPI + Vue web UI plus the Python library API. A stale historical `nwn-translate` CLI exists only in generated `egg-info` / `__pycache__` artifacts; do not document or rely on it unless a source `src/nwn_translator/cli.py` and matching `pyproject.toml` entrypoint are restored.

## Common commands

```bash
# Install for development
pip install -e ".[dev]"          # core + dev tools
pip install -e ".[web]"          # adds FastAPI / uvicorn for the web UI

# Tests
pytest
pytest tests/test_git_extractor.py
pytest tests/test_extractors.py::TestEncounterExtractor
pytest -k "placeable and locname"
pytest --cov=src

# Lint / format / type-check
black src tests
pylint src/nwn_translator
mypy src

# Web UI dev (two terminals, or use run-web-ui.bat on Windows)
python -m nwn_translator.web
cd frontend && npm install && npm run dev   # http://localhost:5173, /api proxied

# Docker (production)
docker compose -f docker/docker-compose.yml up --build
```

Code is expected to pass black (line length 100) and mypy; pylint is advisory.

## Local environment

- The project-local virtual environment is `.venv/`.
- `.env` is local and gitignored. Use `.env.example` as the template.
- `NWN_TRANSLATE_API_KEY` selects the provider by prefix: `sk-or-...` for OpenRouter, `pza...` for POLZA.AI, fallback to OpenRouter for unknown prefixes.
- The model is not read from `NWN_TRANSLATE_MODEL`; pass it through the web/API request or `TranslationConfig(model=...)`.
- `workspace/`, `check_this/`, `frontend/dist/`, `frontend/node_modules/`, caches and logs are local artifacts and should not be committed.

## Pipeline

`translate_module` / `run_translation_pipeline` in `src/nwn_translator/main.py` orchestrate the run:

1. **ERF read** (`file_handlers/erf_reader.py`) unpacks the input archive to a temp dir.
2. **GFF/NCS parse** (`file_handlers/gff_parser.py`, `gff_handler.py`, `ncs_parser.py`) parses resources. Only embedded strings are translated; fields stored as a StrRef with no embedded text are left untouched (the engine resolves them from the player's `dialog.tlk` at runtime).
3. **Extract** (`extractors/`) produces `ExtractedContent` with `TranslatableItem`s. Extractors are registered in `extractors/__init__.py`.
4. **World context** (`context/world_context.py`, `context/entity_extractor.py`) scans extracted content for NPCs, areas, quests, and proper nouns.
5. **Glossary** (`glossary.py`, `race_dictionary.py`) builds and injects terminology into prompts.
6. **Translate** (`translators/translation_manager.py`, `context_translator.py`) batches non-dialog items and translates dialogs contextually. `token_handler.py` protects NWN tokens and inline tags with placeholders before LLM calls and restores them afterwards.
7. **Inject** (`injectors/`, `file_handlers/gff_patcher.py`, `ncs_patcher.py`) byte-patches localized GFF fields and NCS string constants.
8. **ERF write** (`file_handlers/erf_writer.py`) bundles patched resources into the output archive.

The key consequence of injection: extractors must preserve `_record_offsets` on parsed structs, and injectors must patch the same field names the extractor read. Field mismatches silently drop translations.

CExoLocString policy: the parser reads the first non-empty substring, and the patcher always writes back a single substring with LanguageID 0 — the universal English slot every client displays directly or via the engine's language fallback (target languages such as Russian have no official NWN language id; the community standard is codepage bytes in slot 0). Original gender/language sub-variants are collapsed on patch, with a warning when more than one substring is overwritten.

## Extractor / Injector contract

- **Extractors** live in `src/nwn_translator/extractors/`. Each subclass of `BaseExtractor` declares `SUPPORTED_TYPES` and returns `ExtractedContent(content_type=..., items=[TranslatableItem(...)])`. A new file type needs the extractor class, registration in `extractors/__init__.py`, and an entry in `TRANSLATABLE_TYPES` in `config.py`.
- **Injectors** live in `src/nwn_translator/injectors/`. Simple field-level resources go through `GenericInjector` (`SUPPORTED_TYPES` + `FIELD_MAP`). Dialogs, journals, `.git` instance lists, and `.ncs` bytecode have bespoke injectors.
- `.git` is special: area instances contain per-instance `LocalizedName`, `LocName`, `Description`, and nested inventory/store shelf strings. Keep `GitExtractor` and `git_injector.patch_git_file` in sync via `INSTANCE_LISTS` and `INSTANCE_NESTED_ITEM_LISTS`.
- Internal engine tags (`WP_...`, `DST_...`, `NW_...`, `POST_...`, `ARCH_...`, `YOURTAGHERE`, spaceless `snake_case`/CamelCase identifiers) must not be translated. `context/string_filters.py` is the single source of truth: `ENGINE_TAG_PREFIXES` is shared with the NCS extractor, and `should_skip_entity_source_text` is the gate both the `.git` extractor and entity extraction call. Add new prefixes there, not in a local list.
- NWN save-game behaviour: `.git` instances are baked into a player's save on first area visit. Re-translating later affects only unvisited areas; visited areas require a new game.

## Other subsystems

- **`ai_providers/`** - OpenRouter and POLZA.AI. `openrouter_provider.py` owns shared OpenAI-compatible request logic, retries, reasoning-effort fallback, batch translation, glossary calls, and the NCS translate gate. `polza_provider.py` subclasses it with a different base URL.
- **`prompts/`** - prompt builder and per-language examples. Changes here affect translation quality across all content types.
- **`translators/prefix_translation_cache.py`** - prefix reuse for repeated text variants.
- **`web/`** - FastAPI app with routes, schemas, SQLite database, and task manager. SPA lives in `frontend/`.
- **`scripts/dump_gff_strings.py`** - diagnostic helper for inspecting GFF strings in single files or modules.

## Test expectations

- `tests/` uses pytest with `addopts = "-v --tb=short -m 'not realdata'"` from `pyproject.toml`.
- Many tests construct parsed-GFF dicts by hand; do not depend on `check_this/` fixtures for automated tests.
- `tests/realdata/` holds opt-in end-to-end checks against a local module corpus (`test_corpus/`, gitignored; path via `NWN_TEST_CORPUS`). They carry the `realdata` marker and are **deselected by default**; run with `pytest -m realdata`. They skip cleanly when the corpus is absent. See `tests/realdata/README.md` for the five runs (parse-all, identity round-trip, no-op patch, mock-translate, encoding diacritics) and the current known-issues baseline.
- When changing extractor/injector behaviour, add focused regression tests that cover both the positive extraction/patching case and internal-tag negative cases where relevant. Treat these regression tests as the verification step for the change (see "Goal-driven execution").
