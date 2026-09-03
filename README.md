> [English version](README_EN.md)

# NWN Modules Translator

Веб-инструмент и Python-библиотека для перевода модулей Neverwinter Nights / NWN:EE через OpenAI-compatible AI providers. Сейчас поддерживаются OpenRouter и POLZA.AI; провайдер выбирается автоматически по префиксу API-ключа.

Есть hosted-инстанс в закрытой бете. Если хотите поучаствовать в тестировании, напишите на [sandr.flow.ai@gmail.com](mailto:sandr.flow.ai@gmail.com).

## Как это работает

Перевод проходит как конвейер из последовательных этапов:

1. **Распаковка** архива `.mod`/`.erf`/`.hak` и поиск переводимых ресурсов (GFF и скомпилированные NCS-скрипты).
2. **World-context** — скан NPC, областей, квестов и собственных имён для согласованности перевода.
3. **Глоссарий** — сбор и курирование терминологии, которая затем подставляется в промпты.
4. **Перевод** — диалоги переводятся контекстно (с учётом ветвления), остальные строки — батчами; NWN-токены и inline-теги (`<FirstName>`, `<CustomToken:123>`, `<StartAction>`) защищаются плейсхолдерами.
5. **Инъекция** — байтовый patch строк обратно в GFF/NCS без полной пересборки бинарных ресурсов.
6. **Пересборка** нового архива.

## Возможности

- Перевод `.mod`, `.erf` и `.hak`.
- FastAPI backend и Vue 3 + Vite + Tailwind web UI.
- Типы ресурсов: `.dlg`, `.jrl`, `.uti`, `.utc`, `.are`, `.utt`, `.utp`, `.utd`, `.ute`, `.utm`, `.ifo`, `.git`, `.ncs`.
- Rebuild после ручного редактирования переводов в web-редакторе.
- SQLite-хранилище задач, чтобы долгие переводы переживали reconnect.
- Docker для production-развёртывания.

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
Модель — через web/API или `TranslationConfig(model=...)`; иначе `OpenRouterProvider.DEFAULT_MODEL` (`google/gemini-3.8-flash`).

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

Приложение доступно на [http://127.0.0.1:8080](http://127.0.0.1:8080) (Compose слушает только localhost; снаружи нужен TLS-прокси перед nginx). Ключ в контейнер не передаётся: BYOK, пользователь вводит его в UI.

## Конфигурация

Основные переменные окружения:

| Переменная | Назначение | По умолчанию |
| --- | --- | --- |
| `NWN_TRANSLATE_API_KEY` | API-ключ OpenRouter (`sk-or-...`) или POLZA.AI (`pza...`) | обязательно |
| `NWN_TRANSLATE_MAX_CONCURRENT` | Максимум параллельных AI-запросов | `12` |
| `NWN_TRANSLATE_PROMPT_CACHE` | Explicit prompt-cache breakpoints, `0` отключает | `1` |
| `NWN_GLOSSARY_LLM_TIMEOUT` | Timeout одного LLM-вызова глоссария, секунд | `300` |
| `NWN_GLOSSARY_RUN_TIMEOUT` | Общий timeout глоссария, секунд | `360` |
| `NWN_WEB_HOST` | Host web-сервера | `127.0.0.1` |
| `NWN_WEB_PORT` | Port web-сервера | `8000` |
| `NWN_WEB_RELOAD` | Auto-reload backend в dev-режиме | выключено |
| `NWN_WEB_CORS_ORIGINS` | CORS origins через запятую (или `*`) | пусто (cross-origin запрещён) |
| `NWN_WEB_STATIC_DIR` | Production static files SPA | не задано |
| `NWN_WEB_TASK_ROOT` | Корневая директория задач web UI | `workspace/web` |
| `NWN_WEB_DB_PATH` | SQLite база задач | `workspace/web/translations.db` |
| `NWN_WEB_TRUSTED_PROXIES` | IP reverse proxies для `X-Forwarded-For` | не задано |

Пример `.env`:

```env
NWN_TRANSLATE_API_KEY=sk-or-v1-...
NWN_TRANSLATE_MAX_CONCURRENT=12
NWN_WEB_HOST=127.0.0.1
NWN_WEB_PORT=8000
```

### API-ключ и BYOK

Продукт — **BYOK (Bring Your Own Key)**: в web UI каждый пользователь вводит свой ключ, и `POST /api/translate` всегда требует его от клиента. Серверный `NWN_TRANSLATE_API_KEY` браузеру не отдаётся.

Исключение — локальный запуск на петле (`NWN_WEB_HOST` по умолчанию `127.0.0.1`): `/api/config` отдаёт ключ из `.env` для автозаполнения поля. Бинд на `0.0.0.0`, Docker и инстанс за nginx этот режим не включают.

### Языки и кодировка

| Code page | Языки |
| --- | --- |
| cp1251 | русский, украинский |
| cp1250 | польский, чешский, венгерский, румынский |
| cp1252 | английский, немецкий, французский, испанский, итальянский, португальский, нидерландский |

Неизвестный slug падает в cp1252. Патчер пишет одну подстроку CExoLocString с LanguageID 0.

## Разработка

```bash
pytest
pytest --cov=src
black src tests
pylint src/nwn_translator
mypy src
```

Ожидается black (line length 100) и mypy. Pylint — advisory.

Изолированные этапы пайплайна — `scripts/stage.py`, дамп CExoLocString — `scripts/dump_gff_strings.py`.

## Документация

| Файл | Содержание |
| --- | --- |
| [`README_EN.md`](README_EN.md) | English version of this guide |
| [`AGENTS.md`](AGENTS.md) | Конвенции для coding agents (канон) |
| [`CLAUDE.md`](CLAUDE.md) | Указатель на AGENTS.md для Claude Code |

## Лицензия

MIT, см. [LICENSE](LICENSE).
