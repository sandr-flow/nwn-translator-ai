> [Русская версия](README.md)

# NWN Modules Translator

Web tool and Python library for translating Neverwinter Nights / NWN:EE modules through OpenAI-compatible AI providers. The current providers are OpenRouter and POLZA.AI; provider selection is automatic from the API key prefix.

A hosted instance is in closed beta. If you'd like to help test it, email [sandr.flow.ai@gmail.com](mailto:sandr.flow.ai@gmail.com).

## How it works

Translation runs as a pipeline of sequential stages:

1. **Unpack** the `.mod`/`.erf`/`.hak` archive and find translatable resources (GFF and compiled NCS scripts).
2. **World context** — scan NPCs, areas, quests, and proper nouns for consistent translation.
3. **Glossary** — collect and curate terminology that is then injected into prompts.
4. **Translate** — dialogs are translated contextually (aware of branching), other strings in batches; NWN tokens and inline tags (`<FirstName>`, `<CustomToken:123>`, `<StartAction>`) are protected with placeholders.
5. **Inject** — byte-level patching of strings back into GFF/NCS without fully rewriting binary resources.
6. **Repack** the new archive.

## Features

- Translation of NWN `.mod`, `.erf`, and `.hak` archives.
- FastAPI backend and Vue 3 + Vite + Tailwind web UI.
- Resource types: `.dlg`, `.jrl`, `.uti`, `.utc`, `.are`, `.utt`, `.utp`, `.utd`, `.ute`, `.utm`, `.ifo`, `.git`, `.ncs`.
- Rebuild after manual translation edits in the web editor.
- SQLite-backed web tasks so long-running translations survive reconnects.
- Docker setup for production deployment.

## Installation

### Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[web]"
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pip install -e ".[web]"
```

This workspace normally uses `.venv/` for the local virtual environment.

## Web UI

Backend:

```bash
python -m nwn_translator.web
```

or the installed entrypoint:

```bash
nwn-translate-web
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

During development the frontend is served at `http://localhost:5173`, with `/api` proxied to FastAPI at `http://localhost:8000`.

Windows users can use `run-web-ui.bat` after Python and npm dependencies are installed.

## Python API

```python
from pathlib import Path

from nwn_translator import TranslationConfig, translate_module

config = TranslationConfig(
    input_file=Path("module.mod"),
    target_lang="russian",
)
output_path = translate_module(config)
print(output_path)
```

The API key is read from `NWN_TRANSLATE_API_KEY` or passed as `TranslationConfig(api_key=...)`.
The model is set via web/API or `TranslationConfig(model=...)`; otherwise `OpenRouterProvider.DEFAULT_MODEL` (`google/gemini-3.8-flash`).

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

The app is at [http://127.0.0.1:8080](http://127.0.0.1:8080) (Compose binds localhost only; remote access needs a TLS proxy in front of nginx). No server key is passed into the container: BYOK, the user enters it in the UI.

## Configuration

Primary environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `NWN_TRANSLATE_API_KEY` | OpenRouter (`sk-or-...`) or POLZA.AI (`pza...`) API key | required |
| `NWN_TRANSLATE_MAX_CONCURRENT` | Maximum parallel AI requests | `12` |
| `NWN_TRANSLATE_PROMPT_CACHE` | Explicit prompt-cache breakpoints; `0` disables | `1` |
| `NWN_GLOSSARY_LLM_TIMEOUT` | Timeout for one glossary LLM call, seconds | `300` |
| `NWN_GLOSSARY_RUN_TIMEOUT` | Overall glossary wrapper timeout, seconds | `360` |
| `NWN_WEB_HOST` | Web server host | `127.0.0.1` |
| `NWN_WEB_PORT` | Web server port | `8000` |
| `NWN_WEB_RELOAD` | Backend auto-reload in development | disabled |
| `NWN_WEB_CORS_ORIGINS` | Comma-separated CORS origins (or `*`) | empty (cross-origin denied) |
| `NWN_WEB_STATIC_DIR` | Production SPA static directory | unset |
| `NWN_WEB_TASK_ROOT` | Web task workspace root | `workspace/web` |
| `NWN_WEB_DB_PATH` | SQLite task database path | `workspace/web/translations.db` |
| `NWN_WEB_TRUSTED_PROXIES` | Reverse proxy IPs allowed for `X-Forwarded-For` | unset |

Example `.env`:

```env
NWN_TRANSLATE_API_KEY=sk-or-v1-...
NWN_TRANSLATE_MAX_CONCURRENT=12
NWN_WEB_HOST=127.0.0.1
NWN_WEB_PORT=8000
```

### API key and BYOK

The product is **BYOK (Bring Your Own Key)**: each user enters their own API key in the web UI, and `POST /api/translate` always requires it from the client. The server does not hand its `NWN_TRANSLATE_API_KEY` to the browser.

The exception is a local loopback run (`NWN_WEB_HOST` defaults to `127.0.0.1`): `/api/config` returns the `.env` key so the UI can autofill. Binding to `0.0.0.0`, Docker, or an instance behind nginx does not enable that mode.

### Languages and encoding

| Code page | Languages |
| --- | --- |
| cp1251 | Russian, Ukrainian |
| cp1250 | Polish, Czech, Hungarian, Romanian |
| cp1252 | English, German, French, Spanish, Italian, Portuguese, Dutch |

An unknown slug falls back to cp1252. The patcher writes a single CExoLocString substring with LanguageID 0.

## Development

```bash
pytest
pytest --cov=src
black src tests
pylint src/nwn_translator
mypy src
```

Code is expected to pass black (line length 100) and mypy. Pylint is advisory.

Isolated pipeline stages: `scripts/stage.py`. Dump CExoLocString fields: `scripts/dump_gff_strings.py`.

## Documentation

| File | Contents |
| --- | --- |
| [`README.md`](README.md) | Russian version of this guide |
| [`AGENTS.md`](AGENTS.md) | Canonical conventions for coding agents |
| [`CLAUDE.md`](CLAUDE.md) | Pointer to AGENTS.md for Claude Code |

## License

MIT, see [LICENSE](LICENSE).
