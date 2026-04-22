> [Русская версия](README.md)

# NWN Modules Translator

Web-based translator for Neverwinter Nights modules powered by OpenRouter.

## Features

- FastAPI + Vue web UI
- Context-aware translation for dialogs and journals
- Preservation of NWN tokens such as `<FirstName>` and `<CustomToken:123>`
- Support for `.dlg`, `.jrl`, `.uti`, `.utc`, `.are`, `.utt`, `.utp`, `.utd`, `.ute`, `.utm`, `.ifo`, `.git`, and `.ncs`
- Rebuild flow after manual translation edits in the web editor
- Docker setup for production deployment

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

## Web Interface

Backend:

```bash
python -m nwn_translator.web
```

or

```bash
nwn-translate-web
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

During development the frontend is served at `http://localhost:5173`, with `/api` proxied to FastAPI.

Windows users can use `run-web-ui.bat` after dependencies are installed.

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

The application will be available on port `8080`.

## Configuration

Primary environment variables:

- `NWN_TRANSLATE_API_KEY` — OpenRouter API key
- `NWN_TRANSLATE_MAX_CONCURRENT` — maximum parallel translation requests
- `NWN_WEB_HOST` — web server host
- `NWN_WEB_PORT` — web server port
- `NWN_WEB_CORS_ORIGINS` — allowed CORS origins
- `NWN_WEB_STATIC_DIR` — production SPA static directory
- `NWN_WEB_TASK_ROOT` — workspace root for web tasks

Example `.env`:

```env
NWN_TRANSLATE_API_KEY=sk-or-v1-...
NWN_TRANSLATE_MAX_CONCURRENT=12
NWN_WEB_HOST=127.0.0.1
NWN_WEB_PORT=8000
```

## Diagnostics

`scripts/` now contains a single reusable diagnostic helper:

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

## License

MIT, see [LICENSE](LICENSE).
