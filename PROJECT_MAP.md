# Карта проекта NWN Modules Translator

Документ описывает структуру проекта, назначение каждого файла и этап пайплайна, на котором он используется.

---

## Точка входа

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/cli.py` | CLI-интерфейс (Click). Команды: `translate`, `test`, `tokens`, `providers`, `web`. Точка входа `nwn-translate` из pyproject.toml | Вход в приложение |
| `src/nwn_translator/main.py` | Главный оркестратор `ModuleTranslator`. Координирует: извлечение → перевод → инъекция → сборка .mod | Основной пайплайн |

---

## Веб-интерфейс (опционально, зависимости `[web]`)

| Файл / каталог | Назначение |
|----------------|------------|
| `src/nwn_translator/web/app.py` | Фабрика FastAPI: CORS, lifespan (фоновая очистка задач), опциональная раздача статики (`NWN_WEB_STATIC_DIR`) |
| `src/nwn_translator/web/routes.py` | REST + SSE: `/api/translate`, `/api/tasks/...`, `/api/test-connection`, `/api/models`, `/api/health` |
| `src/nwn_translator/web/task_manager.py` | Задачи в памяти, один активный перевод на IP, `progress_callback` → очередь для SSE, `NWN_WEB_TASK_ROOT` |
| `src/nwn_translator/web/schemas.py` | Pydantic-схемы ответов |
| `src/nwn_translator/web/__main__.py` | Запуск uvicorn (`python -m nwn_translator.web`, скрипт `nwn-translate-web`) |
| `frontend/` | Vue 3 + Vite + Tailwind: загрузка .mod, форма, SSE-прогресс, скачивание результата |
| `docker/Dockerfile` | Образ API (Python + uvicorn) |
| `docker/Dockerfile.nginx` | Сборка SPA + nginx |
| `docker/docker-compose.yml` | Сервисы `app` + `nginx`, volume для файлов задач |
| `docker/nginx.conf` | Статика + прокси `/api/` на FastAPI (SSE без буферизации) |

---

## Конфигурация

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/config.py` | `TranslationConfig`, `ProgressCallback`, `TRANSLATABLE_TYPES`, `STANDARD_TOKENS`, `create_output_path` | Инициализация, все этапы |
| `src/nwn_translator/translation_logging.py` | `TranslationLogWriter`, `FileTranslationLogWriter`, `NullTranslationLogWriter`, `translation_log_writer_for_config` | Лог переводов (файл или кастомный writer) |

---

## Основной пайплайн (по этапам)

### Этап 1: Извлечение .mod

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/file_handlers/erf_reader.py` | `ERFReader` — чтение .mod/.erf, извлечение ресурсов (опционально `progress_callback` вместо tqdm) | 1. Извлечение |
| `src/nwn_translator/file_handlers/erf_writer.py` | `ERFWriter`, `create_mod_from_directory` — сборка нового .mod из директории | 4. Сборка .mod |

### Этап 2: Парсинг GFF и поиск переводимого

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/file_handlers/gff_parser.py` | Низкоуровневый парсер GFF (бинарный формат NWN) | 2. Парсинг |
| `src/nwn_translator/file_handlers/gff_handler.py` | `read_gff`, `GFFHandler` — высокоуровневый API чтения GFF, разрешение StrRef через TLK | 2. Парсинг |
| `src/nwn_translator/file_handlers/tlk_reader.py` | `parse_tlk`, `find_dialog_tlk`, `TLKFile` — чтение dialog.tlk для StrRef | 2. Парсинг |

### Этап 2.5: Экстракторы (извлечение текста из GFF)

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/extractors/base.py` | `BaseExtractor`, `ExtractedContent`, `TranslatableItem`, `DialogNode` | Базовые типы |
| `src/nwn_translator/extractors/dialog_extractor.py` | `DialogExtractor` — диалоги (.dlg), построение дерева | 2.5. Экстракция |
| `src/nwn_translator/extractors/journal_extractor.py` | `JournalExtractor` — журналы (.jrl) | 2.5. Экстракция |
| `src/nwn_translator/extractors/item_extractor.py` | `ItemExtractor` — предметы (.uti) | 2.5. Экстракция |
| `src/nwn_translator/extractors/creature_extractor.py` | `CreatureExtractor` — существа (.utc) | 2.5. Экстракция |
| `src/nwn_translator/extractors/area_extractor.py` | `AreaExtractor`, `TriggerExtractor`, `PlaceableExtractor`, `DoorExtractor`, `StoreExtractor` — области, триггеры, плейсейблы, двери, магазины | 2.5. Экстракция |
| `src/nwn_translator/extractors/module_extractor.py` | `ModuleExtractor` — модуль (.ifo) | 2.5. Экстракция |
| `src/nwn_translator/extractors/__init__.py` | Реестр экстракторов, `get_extractor_for_file` | 2.5. Экстракция |

**Примечание:** `.itp` и `.fac` не входят в `TRANSLATABLE_TYPES` (экстракторов нет).

### Этап 2.6: Контекст (опционально)

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/context/world_context.py` | `WorldScanner`, `WorldContext` — сканирование мира (существа, области, журналы, предметы) для контекста диалогов | 2.6. Контекст |
| `src/nwn_translator/context/dialog_formatter.py` | `DialogFormatter` — форматирование дерева диалога для промпта AI | 3. Перевод (диалоги) |

### Этап 3: Перевод

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/ai_providers/base.py` | `BaseAIProvider`, `TranslationItem`, `TranslationResult`, исключения | Базовые типы |
| `src/nwn_translator/ai_providers/openrouter_provider.py` | `OpenRouterProvider` — единственный провайдер перевода | 3. Перевод |
| `src/nwn_translator/ai_providers/__init__.py` | `create_provider`, экспорт типов и `OpenRouterProvider` | 3. Перевод |
| `src/nwn_translator/translators/translation_manager.py` | `TranslationManager` — пакетный перевод (строки, журналы, предметы и т.д.) | 3. Перевод |
| `src/nwn_translator/translators/context_translator.py` | `ContextualTranslationManager` — контекстный перевод целых диалогов (.dlg) | 3. Перевод |
| `src/nwn_translator/translators/token_handler.py` | `TokenHandler`, `sanitize_text`, `restore_text` — сохранение токенов `<FirstName>` и т.д. | 3. Перевод |

### Этап 4: Инъекция переводов в GFF

| Файл | Назначение | Этап |
|------|------------|------|
| `src/nwn_translator/file_handlers/gff_patcher.py` | `GFFPatcher` — бинарный патч CExoLocString в GFF без полной перезаписи | 4. Инъекция |
| `src/nwn_translator/injectors/base.py` | `BaseInjector`, `InjectedContent` | Базовые типы |
| `src/nwn_translator/injectors/dialog_injector.py` | `DialogInjector`, `JournalInjector`, `GenericInjector` — инъекция в .dlg, .jrl, .uti, .utc, .are и т.д. | 4. Инъекция |
| `src/nwn_translator/injectors/git_injector.py` | `patch_git_file` — патч .git (экземпляры в областях) по накопленным переводам | 4. Инъекция (.git) |
| `src/nwn_translator/injectors/__init__.py` | Реестр инжекторов, `get_injector_for_content` | 4. Инъекция |

---

## Файлы, не участвующие в основном пайплайне

### Используются только в тестах / инфраструктуре

| Файл | Назначение | Статус |
|------|------------|--------|
| `src/nwn_translator/file_handlers/gff_writer.py` | `GFFWriter`, `write_gff`, `write_gff_bytes` — полная сериализация GFF в бинарный формат | Используется в `gff_handler.write` и в тестах. В основном пайплайне **не вызывается** — инжекторы работают через `GFFPatcher`. Остаётся как инфраструктура на случай полной перезаписи GFF. |

---

## Утилиты (scripts/) — вне основного пайплайна

| Файл | Назначение | Этап |
|------|------------|------|
| `scripts/dump_gff_strings.py` | Диагностика: дамп всех CExoLocString из одного GFF-файла. Опция `--compare` для сравнения с оригиналом | Отладка / разработка |
| `scripts/dump_mod_strings.py` | Извлечение ресурса из .mod и дамп CExoLocString. Использует `ERFReader` | Отладка / разработка |
| `scripts/compare_mods.py` | Сравнение ресурсов между оригинальным и переведённым .mod. Использует `ERFReader` | Отладка / разработка |
| `scripts/dump_ifo.py` | Извлечение module.ifo из .mod. Жёстко закодированные пути к тестовым модулям | Отладка (устаревший скрипт) |
| `scripts/compare_talias.py` | Сравнение FirstName/LastName в talias.utc между оригиналом и переводом. Использует `ERFReader`, `read_gff` | Отладка / разработка |

**Примечание:** Скрипты — вспомогательные инструменты для разработки и отладки. Не входят в основной пайплайн. `dump_ifo.py` содержит баг (`ifo_data` может быть не инициализирован) и жёстко закодированные пути.

---

## Тесты

| Файл | Назначение | Этап |
|------|------------|------|
| `tests/test_extractors.py` | Тесты экстракторов | CI |
| `tests/test_gff_writer.py` | Тесты GFFWriter | CI |
| `tests/test_erf_writer.py` | Тесты ERFWriter | CI |
| `tests/test_providers.py` | Тесты AI-провайдеров | CI |
| `tests/test_openrouter_provider.py` | Тесты OpenRouter | CI |
| `tests/test_token_handler.py` | Тесты TokenHandler | CI |
| `tests/test_translation_manager.py` | Тесты TranslationManager | CI |
| `tests/test_integration.py` | Интеграционные тесты | CI |
| `tests/test_integration_dialog.py` | Интеграционные тесты диалогов | CI |

---

## Сводка: вспомогательные скрипты

| Файл | Примечание |
|------|------------|
| `scripts/dump_ifo.py` | Жёстко закодированные пути; при необходимости починить или удалить |

**Загрузка `.env`:** выполняется в `cli.py` при старте CLI (`load_dotenv()`), не при импорте `config.py`.

---

## Диаграмма потока данных

```
[.mod] → ERFReader.read_entries() → extract_all()
    ↓
[временная директория с .dlg, .uti, .utc, .are, ...]
    ↓
read_gff() + get_extractor_for_file() → ExtractedContent
    ↓
TranslationManager / ContextualTranslationManager → translations
    ↓
get_injector_for_content() → GFFPatcher.patch_local_string()
    ↓
patch_git_file() для .git
    ↓
create_mod_from_directory() → [переведённый .mod]
```
