# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language conventions

- **User-facing replies must be in Russian.** Every chat message back to the user (explanations, status updates, summaries, questions) is written in Russian.
- **Code stays in English.** All identifiers, comments, and docstrings are written in English regardless of the chat language. Existing user-facing UI strings in Russian (e.g. `locales.js` RU block, error messages in `routes.py`) remain as they are — do not translate them to English.

## Project

AI-powered translator for Neverwinter Nights (NWN/NWN:EE) modules. Takes a `.mod` file, extracts translatable strings from binary GFF resources, translates them via OpenRouter, and byte-patches the strings back into a new `.mod` without rebuilding the files. Ships with a CLI (`nwn-translate`) and a FastAPI + Vue web UI.

## Common commands

```bash
# Install for development (Windows paths shown in README; use venv your way)
pip install -e ".[dev]"          # core + dev tools
pip install -e ".[web]"          # adds FastAPI / uvicorn for the web UI

# Tests
pytest                                   # full suite
pytest tests/test_git_extractor.py       # single file
pytest tests/test_extractors.py::TestEncounterExtractor   # single class
pytest -k "placeable and locname"        # by expression
pytest --cov=src                         # with coverage

# Lint / format / type-check
black src tests
pylint src/nwn_translator
mypy src

# CLI (entrypoint nwn-translate -> src/nwn_translator/cli.py)
nwn-translate translate module.mod --lang russian
nwn-translate translate module.mod --lang french --model anthropic/claude-sonnet-4 -o out.mod
nwn-translate test --lang russian --text "Hello, adventurer!"
nwn-translate tokens       # list NWN tokens preserved during translation
nwn-translate providers    # list available OpenRouter models
nwn-translate web --host 127.0.0.1 --port 8000

# Web UI dev (two terminals, or use scripts/run_web_ui.py)
nwn-translate web
cd frontend && npm install && npm run dev   # http://localhost:5173, /api proxied

# Docker (production)
docker compose -f docker/docker-compose.yml up --build
```

Code is expected to pass black (line length 100) and mypy; pylint is advisory.

## Pipeline (big picture)

`translate_module` in `src/nwn_translator/main.py` orchestrates the whole run:

1. **ERF read** (`file_handlers/erf_reader.py`) unpacks the `.mod` (an ERF archive) to a temp dir.
2. **GFF parse** (`file_handlers/gff_parser.py`, `gff_handler.py`) — every resource in the module is a GFF binary (`.dlg`, `.utc`, `.uti`, `.are`, `.git`, `.ifo`, `.jrl`, `.utp`, `.utd`, `.utt`, `.ute`, `.utm`). TLK lookup (`tlk_reader.py`) resolves StrRef-only strings against `dialog.tlk`.
3. **Extract** — `extractors/` produces `ExtractedContent` full of `TranslatableItem`s keyed by file type. Registered via a singleton map in `extractors/__init__.py`.
4. **World context** (`context/world_context.py`, `context/entity_extractor.py`) scans all extracted content to build a `WorldContext` (NPCs, areas, quests, glossary). This is what makes dialog translation coherent across files.
5. **Translate** — `translators/translation_manager.py` + `context_translator.py` batch items to OpenRouter via `ai_providers/openrouter_provider.py`. Dialog trees are walked as a whole via `DialogNode` so replies keep the conversational thread. `token_handler.py` protects NWN tokens (`<FirstName>`, `<CustomToken:123>`, etc.) and `tag preservation` protects NWN tag markup — replaced with placeholders before the LLM call and restored after.
6. **Inject** — `injectors/` byte-patches CExoLocString `Value` fields in the extracted GFFs using record offsets captured during parsing (`gff_patcher.py`). No full GFF rewrite — offsets drift would corrupt the file.
7. **ERF write** (`file_handlers/erf_writer.py`) bundles the patched resources back into a new `.mod`.

The key consequence of step 6: extractors must stash `_record_offsets` on each parsed struct, and injectors must read the **same field names** the extractor reads. Mismatches silently drop translations (e.g., `.utp` uses `LocName`, not `Name` — both sides had to agree).

## Extractor / Injector contract

- **Extractors** live in `src/nwn_translator/extractors/`. Each subclass of `BaseExtractor` declares `SUPPORTED_TYPES` (list of extensions) and returns `ExtractedContent(content_type=..., items=[TranslatableItem(...)])`. A new file type needs: the extractor class, registration in `extractors/__init__.py` (both `__all__` and the `_EXTRACTOR_MAP` build loop), and an entry in `TRANSLATABLE_TYPES` in `config.py`.
- **Injectors** live in `src/nwn_translator/injectors/`. Simple field-level types go through `GenericInjector` (add the content_type to `SUPPORTED_TYPES` + `FIELD_MAP`). Dialogs, journals, and `.git` instance lists have bespoke injectors because they walk nested lists.
- `.git` is special: area instance data holds per-instance `LocalizedName` / `LocName` / `Description` for placed creatures, placeables, doors, triggers, waypoints, encounters, stores, and nested store shelves. Both `GitExtractor` and `git_injector.patch_git_file` walk this structure; keep them in sync (see `INSTANCE_LISTS`, `INSTANCE_NESTED_ITEM_LISTS`).
- Internal engine tags (`WP_…`, `DST_…`, `NW_…`, spaceless `snake_case` identifiers) are filtered by `is_internal_tag` in `git_injector.py`. Do not translate them.
- NWN save-game behaviour: `.git` instances are baked into the player's save on first visit to an area. Re-translating after the fact only affects **unvisited** areas; visited areas require a new game.

## Other subsystems

- **`ai_providers/`** — OpenRouter-only today. `openrouter_provider.py` handles the chat-completion call, reasoning-effort levels, and timeout/retry. The web UI also uses these providers directly via FastAPI routes.
- **`prompts/`** — Jinja-style prompt builder (`_builder.py`) pulling from structured examples. Changes here affect translation quality across all content types.
- **`glossary.py`** + **`race_dictionary.py`** — glossary pipeline that extracts entities once (`GlossaryBuilder`) and injects terms into later prompts for consistency. Bounded by `GLOSSARY_LLM_TIMEOUT` / `GLOSSARY_RUN_TIMEOUT` env vars.
- **`file_handlers/ncs_parser.py` + `ncs_patcher.py`** — compiled-script (`.ncs`) string patching. NCS translation is limited to string constants embedded in scripts, not logic changes.
- **`web/`** — FastAPI app (`app.py`, `routes.py`) with a SQLite-backed task manager (`task_manager.py`, `database.py`) so long translations survive reconnects. SPA lives in `frontend/` (Vue 3 + Vite + Tailwind).

## Environment

Runtime config is read from env vars (see README "Конфигурация" / `.env.example`). The most relevant for iteration:

- `NWN_TRANSLATE_API_KEY` — OpenRouter key.
- `NWN_TRANSLATE_MAX_CONCURRENT` — parallel OpenRouter requests (default 12; drop to 10 on 429, raise to 15–20 on higher tiers).
- `NWN_GLOSSARY_LLM_TIMEOUT`, `NWN_GLOSSARY_RUN_TIMEOUT` — override glossary call timeouts.
- `NWN_WEB_*` — host/port/CORS/static-dir/task-root for the web server.

## Test expectations

- `tests/` uses pytest with `addopts = "-v --tb=short"` (see `pyproject.toml`). Classes must be named `Test*`, functions `test_*`.
- Many tests construct parsed-GFF dicts by hand (see `tests/test_git_extractor.py`) — no fixtures pulled from the `check_this/` folder. `check_this/` holds ad-hoc module snapshots for manual investigation and is gitignored from commits.
- When changing extractor/injector behaviour, add regression fixtures in `tests/test_extractors.py` or `tests/test_git_extractor.py` that cover both the positive case (string gets extracted/patched) and the internal-tag negative case.
