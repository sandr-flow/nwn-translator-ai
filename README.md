> [English version](README_EN.md)

# NWN Modules Translator

Инструмент для перевода модулей Neverwinter Nights с любого языка на любой другой с помощью AI (OpenRouter).

## Возможности

- **OpenRouter** — один API-ключ открывает доступ к множеству моделей (Claude, GPT, Gemini, DeepSeek и др.)
- **Сохранение игровых токенов** — `<FirstName>`, `<Class>`, `<CustomToken:123>` и прочие токены NWN сохраняются без изменений
- **Контекстный перевод** — деревья диалогов переводятся целиком, что обеспечивает смысловую связность
- **Пакетная обработка** — диалоги, журналы, предметы, существа, области, двери, триггеры и магазины
- **Веб-интерфейс** — FastAPI + Vue SPA для перевода через браузер
- **Docker** — готовый docker-compose для продакшен-деплоя

## Установка

### Из PyPI

```bash
pip install nwn-modules-translator
```

### Разработка из репозитория

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

Для веб-интерфейса дополнительно:
```bash
pip install -e ".[web]"
```

## Использование CLI

### Перевод модуля

Ключ API берётся из `.env` (`NWN_TRANSLATE_API_KEY`) или передаётся через `--api-key`:

```bash
nwn-translate translate module.mod --lang russian
```

Указать выходной файл:
```bash
nwn-translate translate module.mod --lang french -o module_fr.mod
```

Выбрать модель OpenRouter:
```bash
nwn-translate translate module.mod --lang russian --model anthropic/claude-sonnet-4
```

### Тестирование подключения

```bash
nwn-translate test --lang russian
nwn-translate test --lang spanish --text "Hello, adventurer!"
```

### Список токенов NWN

```bash
nwn-translate tokens
```

### Список моделей

```bash
nwn-translate providers
```

### Запуск веб-сервера

```bash
nwn-translate web --host 127.0.0.1 --port 8000
```

## Веб-интерфейс

Проект включает FastAPI-бэкенд и Vue SPA-фронтенд.

### Разработка

Бэкенд:
```bash
pip install -e ".[web]"
nwn-translate web
```

Фронтенд (отдельный терминал):
```bash
cd frontend
npm install
npm run dev
```

Откройте `http://localhost:5173` — запросы `/api` проксируются на FastAPI.

Одной командой (бэкенд + фронтенд):
```bash
python scripts/run_web_ui.py
```

Windows: можно дважды щёлкнуть `run-web-ui.bat` (после установки зависимостей).

### Docker (продакшен)

```bash
docker compose -f docker/docker-compose.yml up --build
```

Приложение будет доступно на порту `8080` (nginx → FastAPI).

## Поддерживаемые типы контента

| Расширение | Тип            | Что переводится                        |
|------------|----------------|----------------------------------------|
| `.dlg`     | Диалоги        | Полные деревья диалогов с контекстом   |
| `.jrl`     | Журнал квестов | Названия и описания квестов            |
| `.uti`     | Предметы       | Названия и описания предметов          |
| `.utc`     | Существа       | Имена и описания NPC                   |
| `.are`     | Области        | Названия и описания областей           |
| `.utt`     | Триггеры       | Названия и описания триггеров          |
| `.utp`     | Размещаемые    | Названия и описания размещаемых        |
| `.utd`     | Двери          | Названия и описания дверей             |
| `.utm`     | Магазины       | Названия магазинов                     |
| `.ifo`     | Информация     | Название и описание модуля             |
| `.git`     | Экземпляры     | Имена размещённых объектов в областях   |

## Сохранение токенов NWN

Инструмент автоматически сохраняет игровые токены при переводе:

- `<FirstName>`, `<LastName>`, `<Class>`, `<Race>`, `<Gender>`
- `<HeShe>`, `<HisHer>`, `<HimHer>`, `<SirMadam>`, `<LordLady>` и др.
- `<CustomToken:123>` — пользовательские токены

Токены заменяются плейсхолдерами перед отправкой в AI и восстанавливаются после перевода.

## Конфигурация

### Переменные окружения

| Переменная                       | Описание                                      | По умолчанию     |
|----------------------------------|-----------------------------------------------|------------------|
| `NWN_TRANSLATE_API_KEY`          | API-ключ OpenRouter                           | —                |
| `NWN_TRANSLATE_MAX_CONCURRENT`   | Макс. параллельных запросов к API              | `12`             |
| `NWN_WEB_HOST`                   | Адрес привязки веб-сервера                    | `127.0.0.1`      |
| `NWN_WEB_PORT`                   | Порт веб-сервера                              | `8000`           |
| `NWN_WEB_CORS_ORIGINS`           | Разрешённые CORS-источники (через запятую)    | `*`              |
| `NWN_WEB_STATIC_DIR`             | Путь к статике SPA (production)               | —                |
| `NWN_WEB_TASK_ROOT`              | Корневая директория задач                     | `workspace/web`  |

### Файл `.env`

Создайте `.env` в корне проекта:

```env
NWN_TRANSLATE_API_KEY=sk-or-v1-...
NWN_TRANSLATE_MAX_CONCURRENT=12
```

## Архитектура

```
.mod файл
  │
  ├── ERF Reader ─── Извлечение ресурсов
  │                      │
  │                      ▼
  │              GFF Parser ─── Парсинг бинарных файлов
  │                      │
  │                      ▼
  │              Extractors ─── Извлечение текста (.dlg, .utc, .uti, …)
  │                      │
  │                      ▼
  │              World Scanner ─── Сбор контекста мира (NPC, локации, квесты)
  │                      │
  │                      ▼
  │              Context Translator ─── AI-перевод через OpenRouter
  │                      │
  │                      ▼
  │              GFF Patcher ─── Бинарная инъекция переведённых строк
  │                      │
  │                      ▼
  └── ERF Writer ─── Сборка переведённого .mod файла
```

## Разработка

### Тесты

```bash
pytest
pytest --cov=src
```

### Линтинг

```bash
black src tests
pylint src/nwn_translator
mypy src
```

## Лицензия

MIT License — см. файл [LICENSE](LICENSE).
