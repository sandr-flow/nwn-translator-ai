# Token economy roadmap

Фаза 1 (prompt caching + memoization) — **done**. См. изменения в
`prompts/_builder.py`, `ai_providers/base.py`, `ai_providers/openrouter_provider.py`,
`translators/context_translator.py`, `config.py`.

Фаза 2 (provider-agnostic сокращение промпта) — **done**. Сделанное:

1. **Фильтр глоссария по батчу.** `Glossary.to_prompt_block(texts=...)` отдаёт
   только entries, имена которых встречаются в текстах батча (whole-word,
   case-insensitive). Реализация: `glossary.py:53-97`. Точки вызова:
   `translation_manager._glossary_block_for_texts` (индивидуальные и батчевые
   запросы). Блок живёт в variable-части, stable-префикс не меняется.
   Регрессии: `tests/test_glossary_filter.py`.

2. **Dedup коротких items по sanitized внутри батча.** В `batch_one`
   (`translation_manager.py`) повторяющиеся sanitized-тексты сворачиваются в
   одну entry API-payload'а, результат размножается по индексам исходного
   батча. Регрессия: `tests/test_translation_manager.py::TestBatchDedupBySanitized`.

3. **Skip non-translatable после sanitize.** `_is_empty_after_sanitize` +
   `_apply_passthrough` короткозамыкают строки, превратившиеся после снятия
   `<<TOKEN_*>>`/NWN-тегов в пунктуацию/пустоту: оригинал кэшируется и
   возвращается без вызова API. Регрессия:
   `tests/test_translation_manager.py::TestPassthroughEmptyAfterSanitize`.

## Фаза 3 — тонкая настройка (требует A/B)

4. **Dynamic few-shot по content_type.** Разбить `prompts/examples/*.py` на
   категории (`proper_names`, `dialog`, `journal`, `item_desc`, `short_label`);
   в stable-часть промпта подставлять только релевантные под тип батча.
   Подбор должен быть **детерминирован по content_type** — иначе кэш ломается
   на границе типов.

5. **Адаптивный `_BATCH_SIZE`.** Для items ≤20 символов поднять до 30–40;
   фикс 15 оставить для остального. `translation_manager.py:321-323`.

6. **Сжать базовые правила.** 12 правил в stable-префиксе дублируются
   (tokens/gender упоминаются несколько раз). Переформулировать плотнее,
   −10–15% constant overhead. A/B-прогон на эталонном модуле обязателен.

## Не делаем

- Понижать `max_tokens` батчей — риск обрезанного ответа, выгода минимальна.
- Structured outputs (`json_schema` strict) — вызывает таймауты на
  DeepSeek/Qwen, см. комментарий в `openrouter_provider.py:483-484`.
- Prompt caching через Anthropic-only API — провайдер-агностичный путь уже
  реализован в Фазе 1.

## Правила, которые нельзя нарушать

- Любая нестабильность stable-префикса убивает кэш и у автокэш-провайдеров
  (OpenAI, DeepSeek). Всё, что меняется между вызовами, — в variable-часть.
- Порядок entries в любом блоке — детерминирован (sorted), иначе cache-miss.
- Флаг `NWN_TRANSLATE_PROMPT_CACHE=0` должен продолжать отключать breakpoint
  (fallback на чистую строку).
