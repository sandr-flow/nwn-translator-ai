> [Русская версия](README.md)

# NWN Modules Translator

Web tool and Python library for translating Neverwinter Nights / NWN:EE modules through OpenAI-compatible AI providers. The current providers are OpenRouter and POLZA.AI; provider selection is automatic from the API key prefix.

## Features

- Translation of NWN `.mod`, `.erf`, and `.hak` archives.
- FastAPI backend and Vue 3 + Vite + Tailwind web UI.
- Context-aware dialog translation using dialog trees, areas, NPCs, quests, and glossary terms.
- Preservation of NWN tokens and inline tags such as `<FirstName>`, `<CustomToken:123>`, and `<StartAction>`.
- Byte-level GFF/NCS string patching without fully rewriting binary GFF resources.
- Support for `.dlg`, `.jrl`, `.uti`, `.utc`, `.are`, `.utt`, `.utp`, `.utd`, `.ute`, `.utm`, `.ifo`, `.git`, and `.ncs`.
- Rebuild flow after manual translation edits in the web editor.
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

The project no longer publishes a current `nwn-translate` CLI. For programmatic translation, use the library API:

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

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

The application will be available on port `8080`.

## Configuration

Primary environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `NWN_TRANSLATE_API_KEY` | OpenRouter (`sk-or-...`) or POLZA.AI (`pza...`) API key | required |
| `NWN_TRANSLATE_MAX_CONCURRENT` | Maximum parallel AI requests | `12` |
| `NWN_TRANSLATE_PROMPT_CACHE` | Enables explicit prompt-cache breakpoints; set `0` to disable | `1` |
| `NWN_GLOSSARY_LLM_TIMEOUT` | Timeout for one glossary LLM call, seconds | `300` |
| `NWN_GLOSSARY_RUN_TIMEOUT` | Overall glossary wrapper timeout, seconds | `360` |
| `NWN_WEB_HOST` | Web server host | `127.0.0.1` |
| `NWN_WEB_PORT` | Web server port | `8000` |
| `NWN_WEB_RELOAD` | Backend auto-reload in development | disabled |
| `NWN_WEB_CORS_ORIGINS` | Comma-separated allowed CORS origins | `*` |
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

The model is selected through web/API parameters or `TranslationConfig(model=...)`; the current code does not read a separate `NWN_TRANSLATE_MODEL` environment variable.

## Diagnostics

`scripts/` contains one reusable diagnostic helper:

```bash
python scripts/dump_gff_strings.py file path/to/file.utc
python scripts/dump_gff_strings.py file path/to/file.utc --compare path/to/original.utc
python scripts/dump_gff_strings.py module path/to/module.mod talias.utc drixie.dlg
```

## Development

Tests:

```bash
pytest
pytest --cov=src
```

Checks:

```bash
black src tests
pylint src/nwn_translator
mypy src
```

Code is expected to pass black with line length 100 and mypy. Pylint is useful as an advisory check.

## License

MIT, see [LICENSE](LICENSE).
