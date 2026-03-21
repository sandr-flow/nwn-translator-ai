> [Русская версия](README.md)

# NWN Modules Translator

AI-powered tool for translating Neverwinter Nights modules from any language to any language using OpenRouter.

## Features

- **OpenRouter** — one API key gives access to many models (Claude, GPT, Gemini, DeepSeek, etc.)
- **Smart Token Preservation** — game tokens like `<FirstName>`, `<Class>`, `<CustomToken:123>` are preserved intact
- **Context-Aware Translation** — dialog trees are translated as complete units for coherence
- **Batch Processing** — dialogs, journals, items, creatures, areas, doors, triggers, and stores
- **Web Interface** — FastAPI + Vue SPA for browser-based translation
- **Docker** — ready-to-use docker-compose for production deployment

## Installation

### From PyPI

```bash
pip install nwn-modules-translator
```

### Development from Repository

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

**Linux / macOS:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

For web interface support:
```bash
pip install -e ".[web]"
```

## CLI Usage

### Translate a Module

API key is read from `.env` (`NWN_TRANSLATE_API_KEY`) or passed via `--api-key`:

```bash
nwn-translate translate module.mod --lang russian
```

Specify output file:
```bash
nwn-translate translate module.mod --lang french -o module_fr.mod
```

Choose an OpenRouter model:
```bash
nwn-translate translate module.mod --lang russian --model anthropic/claude-sonnet-4
```

### Test Connection

```bash
nwn-translate test --lang russian
nwn-translate test --lang spanish --text "Hello, adventurer!"
```

### List NWN Tokens

```bash
nwn-translate tokens
```

### List Models

```bash
nwn-translate providers
```

### Start Web Server

```bash
nwn-translate web --host 127.0.0.1 --port 8000
```

## Web Interface

The project includes a FastAPI backend and Vue SPA frontend.

### Development

Backend:
```bash
pip install -e ".[web]"
nwn-translate web
```

Frontend (separate terminal):
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` — `/api` requests are proxied to FastAPI.

Single command (backend + frontend):
```bash
python scripts/run_web_ui.py
```

Windows: double-click `run-web-ui.bat` (after installing dependencies).

### Docker (Production)

```bash
docker compose -f docker/docker-compose.yml up --build
```

The application will be available on port `8080` (nginx → FastAPI).

## Supported Content Types

| Extension | Type           | What is Translated                     |
|-----------|----------------|----------------------------------------|
| `.dlg`    | Dialogs        | Complete dialog trees with context     |
| `.jrl`    | Journals       | Quest names and descriptions           |
| `.uti`    | Items          | Item names and descriptions            |
| `.utc`    | Creatures      | NPC names and descriptions             |
| `.are`    | Areas          | Area names and descriptions            |
| `.utt`    | Triggers       | Trigger names and descriptions         |
| `.utp`    | Placeables     | Placeable names and descriptions       |
| `.utd`    | Doors          | Door names and descriptions            |
| `.utm`    | Stores         | Store names                            |
| `.ifo`    | Module Info    | Module name and description            |
| `.git`    | Instances      | Placed object names in areas           |

## Token Preservation

The tool automatically preserves NWN game tokens during translation:

- `<FirstName>`, `<LastName>`, `<Class>`, `<Race>`, `<Gender>`
- `<HeShe>`, `<HisHer>`, `<HimHer>`, `<SirMadam>`, `<LordLady>`, etc.
- `<CustomToken:123>` — custom token references

Tokens are replaced with placeholders before sending to AI and restored after translation.

## Configuration

### Environment Variables

| Variable                         | Description                                   | Default          |
|----------------------------------|-----------------------------------------------|------------------|
| `NWN_TRANSLATE_API_KEY`          | OpenRouter API key                            | —                |
| `NWN_TRANSLATE_MAX_CONCURRENT`   | Max parallel API requests                     | `12`             |
| `NWN_WEB_HOST`                   | Web server bind address                       | `127.0.0.1`      |
| `NWN_WEB_PORT`                   | Web server port                               | `8000`           |
| `NWN_WEB_CORS_ORIGINS`           | Allowed CORS origins (comma-separated)        | `*`              |
| `NWN_WEB_STATIC_DIR`             | Path to SPA static files (production)         | —                |
| `NWN_WEB_TASK_ROOT`              | Task workspace root directory                 | `workspace/web`  |

### `.env` File

Create a `.env` file in the project root:

```env
NWN_TRANSLATE_API_KEY=sk-or-v1-...
NWN_TRANSLATE_MAX_CONCURRENT=12
```

## Architecture

```
.mod file
  │
  ├── ERF Reader ─── Extract resources
  │                      │
  │                      ▼
  │              GFF Parser ─── Parse binary files
  │                      │
  │                      ▼
  │              Extractors ─── Extract text (.dlg, .utc, .uti, …)
  │                      │
  │                      ▼
  │              World Scanner ─── Collect world context (NPCs, locations, quests)
  │                      │
  │                      ▼
  │              Context Translator ─── AI translation via OpenRouter
  │                      │
  │                      ▼
  │              GFF Patcher ─── Binary injection of translated strings
  │                      │
  │                      ▼
  └── ERF Writer ─── Build translated .mod file
```

## Development

### Tests

```bash
pytest
pytest --cov=src
```

### Linting

```bash
black src tests
pylint src/nwn_translator
mypy src
```

## License

MIT License — see [LICENSE](LICENSE) file for details.
