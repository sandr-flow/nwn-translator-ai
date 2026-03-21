# NWN Modules Translator

AI-powered tool for translating Neverwinter Nights modules from any language to any language.

## Features

- **OpenRouter**: One API key gives access to many models (Claude, GPT, Gemini, DeepSeek, …)
- **Smart Token Preservation**: Game tokens like `<FirstName>`, `<Class>`, etc. are preserved
- **Context-Aware Translation**: Dialog trees are translated as complete units to maintain context
- **Batch Processing**: Translates all dialog, journal, item, and area descriptions in a module
- **Simple CLI**: Put `NWN_TRANSLATE_API_KEY` in `.env`, then run translate with `--lang`

## Installation

```bash
pip install nwn-modules-translator
```

### Разработка из репозитория (venv)

Рекомендуется отдельное виртуальное окружение:

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

Веб-API и зависимости фронта к ядру:
```bash
pip install -e ".[web]"
```

Запуск бэкенда (API для SPA):
```bash
nwn-translate web --host 127.0.0.1 --port 8000
# или
nwn-translate-web
```

Фронтенд (отдельный терминал, Node.js):
```bash
cd frontend
npm install
npm run dev
```

Откройте в браузере адрес Vite (обычно http://127.0.0.1:5173) — запросы к `/api` проксируются на FastAPI.

Docker-сборка для продакшена: см. [docker/docker-compose.yml](docker/docker-compose.yml).

## Usage

Basic translation (key from `.env` as `NWN_TRANSLATE_API_KEY`, or use `--api-key`):
```bash
nwn-translate module.mod --lang spanish
```

Specify output file:
```bash
nwn-translate module.mod --lang french -o module_fr.mod
```

Pick an OpenRouter model:
```bash
nwn-translate module.mod --lang russian --model anthropic/claude-3.5-sonnet
```

List default and popular models:
```bash
nwn-translate providers
```

## Supported Content Types

- Dialogs (.dlg) - Complete dialog trees with context
- Journal entries (.jrl) - Quest journals and categories
- Items (.uti) - Item names and descriptions
- Creatures (.utc) - Creature names and descriptions
- Areas (.are) - Area names and descriptions
- Placeables (.utt) - Placeable names and descriptions
- Doors (.utt) - Door names and descriptions
- Stores (.utm) - Store names and descriptions

## Token Preservation

The tool automatically preserves NWN game tokens:
- `<FirstName>`, `<LastName>`, `<Class>`, `<Race>`, `<Gender>`
- `<CustomToken:123>` - Custom token references
- All tokens are restored after translation

## AI Providers

- **Grok** (xAI) - Default, fast and affordable
- **OpenAI** - GPT-4 and GPT-3.5
- **Gemini** (Google) - Gemini Pro
- **Mistral** - Mistral AI models

## License

MIT License - See LICENSE file for details
