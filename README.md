> [English version](README_EN.md)

# NWN Modules Translator

Веб-инструмент для перевода модулей Neverwinter Nights через OpenRouter.

## Возможности

- Перевод модулей через FastAPI + Vue web UI
- Контекстный перевод диалогов и журналов
- Сохранение NWN-токенов вроде `<FirstName>` и `<CustomToken:123>`
- Поддержка `.dlg`, `.jrl`, `.uti`, `.utc`, `.are`, `.utt`, `.utp`, `.utd`, `.ute`, `.utm`, `.ifo`, `.git`, `.ncs`
- Rebuild модуля после ручного редактирования переводов в web-интерфейсе
- Docker-конфигурация для production-развёртывания

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

## Веб-интерфейс

Backend:

```bash
python -m nwn_translator.web
```

или

```bash
nwn-translate-web
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Во время разработки frontend доступен на `http://localhost:5173`, запросы `/api` проксируются в FastAPI.

Windows: можно использовать `run-web-ui.bat`, если зависимости уже установлены.

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

Приложение будет доступно на порту `8080`.

## Конфигурация

Основные переменные окружения:

- `NWN_TRANSLATE_API_KEY` — API key OpenRouter
- `NWN_TRANSLATE_MAX_CONCURRENT` — максимальное число параллельных запросов
- `NWN_WEB_HOST` — адрес web-сервера
- `NWN_WEB_PORT` — порт web-сервера
- `NWN_WEB_CORS_ORIGINS` — разрешённые CORS origins
- `NWN_WEB_STATIC_DIR` — путь к production static files SPA
- `NWN_WEB_TASK_ROOT` — корневая директория задач web-интерфейса

Пример `.env`:

```env
NWN_TRANSLATE_API_KEY=sk-or-v1-...
NWN_TRANSLATE_MAX_CONCURRENT=12
NWN_WEB_HOST=127.0.0.1
NWN_WEB_PORT=8000
```

## Диагностика

В `scripts/` оставлен один универсальный диагностический инструмент:

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

## Лицензия

MIT, см. [LICENSE](LICENSE).
