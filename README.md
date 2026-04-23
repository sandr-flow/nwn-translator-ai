> [English version](README_EN.md)

# NWN Modules Translator

Веб-инструмент и Python-библиотека для перевода модулей Neverwinter Nights / NWN:EE через OpenAI-compatible AI providers. Сейчас поддерживаются OpenRouter и POLZA.AI; провайдер выбирается автоматически по префиксу API-ключа.

## Возможности

- Перевод `.mod`, `.erf` и `.hak` архивов NWN.
- FastAPI backend и Vue 3 + Vite + Tailwind web UI.
- Контекстный перевод диалогов с учётом структуры веток, областей, NPC, квестов и глоссария.
- Защита NWN-токенов и inline-тегов, например `<FirstName>`, `<CustomToken:123>`, `<StartAction>`.
- Байтовый patch GFF/NCS строк без полной пересборки бинарных GFF ресурсов.
- Поддержка `.dlg`, `.jrl`, `.uti`, `.utc`, `.are`, `.utt`, `.utp`, `.utd`, `.ute`, `.utm`, `.ifo`, `.git`, `.ncs`.
- Rebuild после ручного редактирования переводов в web-редакторе.
- SQLite-хранилище задач для web UI, чтобы долгие переводы переживали reconnect.
- Docker-конфигурация для production-развёртывания.

## Установка

### Разработка

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

В этом рабочем репозитории локальное окружение обычно находится в `.venv/`.

## Web UI

Backend:

```bash
python -m nwn_translator.web
```

или установленный entrypoint:

```bash
nwn-translate-web
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Во время разработки frontend доступен на `http://localhost:5173`, запросы `/api` проксируются в FastAPI на `http://localhost:8000`.

Windows: можно использовать `run-web-ui.bat`, если Python- и npm-зависимости уже установлены.

## Python API

Проект больше не публикует актуальный CLI `nwn-translate`; для программного запуска используйте библиотечный API:

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

API-ключ берётся из `NWN_TRANSLATE_API_KEY` или передаётся в `TranslationConfig(api_key=...)`.

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

Приложение будет доступно на порту `8080`.

## Конфигурация

Основные переменные окружения:

| Переменная | Назначение | По умолчанию |
| --- | --- | --- |
| `NWN_TRANSLATE_API_KEY` | API-ключ OpenRouter (`sk-or-...`) или POLZA.AI (`pza...`) | обязательно |
| `NWN_TRANSLATE_MAX_CONCURRENT` | Максимум параллельных AI-запросов | `12` |
| `NWN_TRANSLATE_PROMPT_CACHE` | Включить explicit prompt-cache breakpoints, `0` отключает | `1` |
| `NWN_GLOSSARY_LLM_TIMEOUT` | Timeout одного LLM-вызова глоссария, секунд | `300` |
| `NWN_GLOSSARY_RUN_TIMEOUT` | Общий timeout wrapper-а глоссария, секунд | `360` |
| `NWN_WEB_HOST` | Host web-сервера | `127.0.0.1` |
| `NWN_WEB_PORT` | Port web-сервера | `8000` |
| `NWN_WEB_RELOAD` | Auto-reload backend в dev-режиме | выключено |
| `NWN_WEB_CORS_ORIGINS` | Разрешённые CORS origins через запятую | `*` |
| `NWN_WEB_STATIC_DIR` | Путь к production static files SPA | не задано |
| `NWN_WEB_TASK_ROOT` | Корневая директория задач web UI | `workspace/web` |
| `NWN_WEB_DB_PATH` | Путь к SQLite базе задач | `workspace/web/translations.db` |
| `NWN_WEB_TRUSTED_PROXIES` | IP reverse proxies, для которых учитывается `X-Forwarded-For` | не задано |

Пример `.env`:

```env
NWN_TRANSLATE_API_KEY=sk-or-v1-...
NWN_TRANSLATE_MAX_CONCURRENT=12
NWN_WEB_HOST=127.0.0.1
NWN_WEB_PORT=8000
```

Модель задаётся через параметры web/API или `TranslationConfig(model=...)`; отдельная переменная `NWN_TRANSLATE_MODEL` в актуальном коде не читается.

## Диагностика

В `scripts/` есть один универсальный диагностический инструмент:

```bash
python scripts/dump_gff_strings.py file path/to/file.utc
python scripts/dump_gff_strings.py file path/to/file.utc --compare path/to/original.utc
python scripts/dump_gff_strings.py module path/to/module.mod talias.utc drixie.dlg
```

## Разработка

Тесты:

```bash
pytest
pytest --cov=src
```

Проверки:

```bash
black src tests
pylint src/nwn_translator
mypy src
```

Код ожидается совместимым с black line length 100 и mypy. Pylint полезен как advisory-проверка.

## Лицензия

MIT, см. [LICENSE](LICENSE).
