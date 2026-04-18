# Token economy roadmap

Фаза 1 (prompt caching + memoization) — **done**. См. изменения в
`prompts/_builder.py`, `ai_providers/base.py`, `ai_providers/openrouter_provider.py`,
`translators/context_translator.py`, `config.py`.

## Фаза 2 — сокращение промпта (provider-agnostic)

1. **Фильтр глоссария по батчу.** `Glossary.to_prompt_block()` принимает набор
   текстов батча и отдаёт только те entries, чьи имена встречаются
   (case-insensitive, whole-word). Полный блок остаётся для диалогов.
   Точки: `glossary.py:53-65`, вызовы в `translation_manager.py:463` и
   `openrouter_provider.translate*`. Блок должен оставаться в **variable**-части,
   stable-префикс не трогать.

2. **Dedup коротких items по sanitized внутри батча.** В `batch_one`
   (`translation_manager.py:447`) группировать повторяющиеся sanitized-тексты,
   отправлять по разу, результат размножать по индексам. Актуально для `.git`
   и tag-имён.

3. **Skip non-translatable после sanitize.** Если sanitized-текст после снятия
   `<<TOKEN_*>>`, NWN-тегов и пунктуации пустой — возвращать оригинал без вызова
   API. Guard в `translation_manager._translate_uncached_concurrent`.

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
