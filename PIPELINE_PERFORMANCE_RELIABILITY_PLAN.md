# План повышения производительности и надежности пайплайна

## Исходные данные

Анализ основан на артефактах из `Almraiven full translate`:

- `Almraiven.mod`: оригинальный модуль, 14 034 ресурса, 43.8 MB.
- `Almraiven-rus.mod`: переведенный модуль, 14 034 ресурса, 46.4 MB.
- `full translate log.jsonl`: 19 934 успешные записи перевода, все через `google/gemini-3-flash-preview`.
- `console errors log.txt`: 183 строки диагностик, включая 137 `token/tag mismatch`, 12 timeout-ов и 3 финальных `Translation failed`.

Распределение успешных переводов:

| Тип | Записей | Уникальных оригиналов | Повторных записей сверх уникальных |
| --- | ---: | ---: | ---: |
| `.dlg` | 9 486 | 8 975 | 511 |
| `.git` | 3 615 | 1 434 | 2 181 |
| `.ncs` | 3 344 | 2 071 | 1 273 |
| `.uti` | 1 358 | 922 | 436 |
| `.utc` | 846 | 401 | 445 |
| `.utp` | 662 | 505 | 157 |
| `.jrl` | 433 | 433 | 0 |

Важные детали:

- 3 119 из 3 344 NCS-строк короче 50 символов, но после LLM gate они переводятся как обычные одиночные long-items, потому что `ncs_string` не входит в батчевые типы `TranslationManager`.
- 142 DLG-файла имеют всего 2-5 переведенных строк, еще 81 DLG-файл имеет одну строку. Сейчас контекстный переводчик обрабатывает DLG последовательно по файлам.
- В JSONL 1 755 групп повторяющихся оригиналов; всего 7 550 записей относятся к повторяющимся строкам. Часть этого уже обслуживается кэшем, но лог показывает, что нагрузку и отображение прогресса нужно измерять не только по строкам, а по фактическим LLM-запросам.
- Самые частые NCS-контексты: `SetCustomToken` (1 438), `SpeakString` (991), generic script function (394), `ActionSpeakString` (87), `SendMessageToPC` (82).
- Основной источник ошибок надежности - сохранение токенов/тегов: 137 mismatch-ов, больше всего в `01_hench_re.dlg`, `_vi_rnd_m3.dlg`, `_sj_guard4.dlg`.
- В error-log есть признаки false positive у dash-маркеров: несколько `.are` names ожидали последовательность `["-", "-"]`, хотя для названий областей дефис часто является обычной пунктуацией, а не action markup.
- Глоссарий строился из 1 344 имен в 34 батчах; один ключ `Kit` не был принят из-за парсинга. Даже при текущей per-batch relevance-фильтрации `Glossary.to_prompt_block(texts=...)` пользовательские наблюдения про запросы с ~20 000 input tokens выглядят правдоподобно: dialog prompt получает одновременно world context, glossary block, speech-style rules и сам dialog script.

Отдельный пример из `big glossary issue example.txt` подтверждает, что проблема шире, чем только `GLOSSARY`. Prompt для `_vi_rnd_f6.dlg` содержит:

| Секция | Строк | Символов | Грубая оценка токенов (`chars / 4`) |
| --- | ---: | ---: | ---: |
| Rules до world context | 66 | 6 054 | 1 514 |
| `WORLD CONTEXT` | 487 | 85 328 | 21 332 |
| `GLOSSARY` | 307 | 20 597 | 5 149 |
| Race terms | 3 | 153 | 38 |
| User dialog script | 114 | 5 501 | 1 375 |
| Всего | 982 | 117 646 | 29 412 |

То есть собственно переводимый dialog script составляет примерно 4.7% prompt-а по символам, а `WORLD CONTEXT` + `GLOSSARY` занимают около 90%. В примере отправлены 478 world-context entries и 306 glossary entries. По точному вхождению в dialog script находятся только 16 glossary names: `Almraiven`, `Auren`, `Auren Society`, `Brynlo`, `Diving Dolphin`, `Gewia`, `Gewia the Wererat`, `Halruaa`, `Human Female`, `Mount Talath`, `Silver Necklace`, `street-side`, `Talath`, `The North Wall`, `Underdark`, `Wererat`. Грубый матч world context дает много ложных совпадений из-за generic имени `Human Female`, поэтому фильтр релевантности должен учитывать не только токены имени, но и source priority, tag/speaker match и запрет generic creature labels как самостоятельного evidence.

По `Almraiven` проблема особенно заметна: в `GLOSSARY` 126 entries имеют `Almraiven` прямо в имени, в `WORLD CONTEXT` 141 entry-line содержит `Almraiven` в имени, tag или описании, но сам dialog script упоминает `Almraiven` только 4 раза. В `WORLD CONTEXT` только 5 entries имеют `Almraiven` в имени/tag (`Almraiven Resident`, `Almraiven Shopper`, `Almraiven Sitter`), остальные подтянуты через описания или общие совпадения. Это не должно попадать в prompt без жесткого лимита и ранжирования.

В полном JSONL также есть явный мусор типа условных `Rat 1`, `Rat 2`, `Rat 3`: найдено 539 записей, 94 уникальных generic numbered labels, почти все из `.git` и `.utp`. Примеры: `Food 5`, `Food 7`, `Chest Static 1`, `Candle 003`, `Underdark Qube 1`, `Underdark Pyramid 2`, `DLA Top Pipe 1`, `Display Case 1`, `Boarded Door 003`, `Awning 001`. Такие строки могут переводиться как UI/game labels при необходимости, но не должны становиться сущностями, world-context anchors или glossary entries.

## Главная цель

Снизить число LLM-вызовов и input tokens без потери качества инъекции. Каждое изменение должно иметь метрику до/после:

- количество LLM-вызовов по фазам;
- суммарные input/output tokens по фазам;
- timeout rate;
- parse/mismatch/retry rate;
- число успешно пропатченных ресурсов;
- бинарная целостность архива: resource count и типы ресурсов не меняются.

## Этап 0. Инструментация перед оптимизациями

Сейчас JSONL фиксирует успешные item-level переводы, но не дает полной картины стоимости запроса. Перед изменением логики стоит добавить run-level telemetry:

- `request_id`, phase (`glossary`, `entity_extraction`, `ncs_gate`, `ncs_translate`, `dialog`, `generic_batch`, `generic_single`);
- provider/model, batch size, char count, approximate token count или usage tokens из ответа, если провайдер их вернул;
- latency, retry count, timeout, parse recovery path;
- размер `stable` и `variable` частей prompt-а отдельно;
- размер world context и glossary block отдельно.

Проверка:

- один прогон на Almraiven пишет machine-readable summary в конец JSONL или отдельный `.metrics.json`;
- можно ответить на вопрос "сколько фактических LLM-запросов ушло на NCS/DLG/glossary и сколько токенов съел glossary".

## Этап 1. Перепланировать сбор сущностей и вычитку глоссария

Проблема: текущая модель данных смешивает разные вещи в один поток: реальные named entities, generic labels, технические названия инстансов, area/journal hierarchy, race/class labels и LLM-extracted guesses. После этого `WORLD CONTEXT` и `GLOSSARY` строятся из слишком широкого набора, а relevance-фильтр уже на поздней стадии не может отличить важное имя от мусора вроде `Food 5` или сотен `Almraiven - ...`.

Новая цель этапа: сделать сущность не строкой, а evidence-backed record, затем детерминированно выбросить очевидный мусор, затем отдать оставшиеся сомнительные и важные кандидаты отдельной LLM-вычитке, и только после этого строить compact prompt context.

### 1.1. Сбор кандидатов как `EntityCandidate`

Вместо списка `(name, category)` собирать структуру:

- `name`: исходная строка;
- `normalized_name`: нормализованный ключ;
- `category`: `character`, `location`, `quest`, `item`, `faction`, `term`, `unknown`;
- `source`: `utc_name`, `are_name`, `jrl_category`, `uti_name`, `git_instance`, `dlg_speaker`, `entity_extractor`;
- `resource`: filename/resref/tag;
- `field`: `FirstName`, `LastName`, `LocalizedName`, `Name`, etc.;
- `frequency`: сколько раз встречается в extracted content;
- `contexts`: 1-3 source snippets для LLM-вычитки кандидата, не замена полному описанию выбранной сущности;
- `is_speaker_or_dialog_actor`: встречается ли как speaker/tag в DLG;
- `technical_score`: deterministic score для resref/camelCase/numbered/generic labels;
- `priority`: вычисляется позже.

Проверка: unit test строит кандидаты из `.utc`, `.are`, `.jrl`, `.git`, `.dlg` и показывает, что одинаковое имя с разными sources не теряет evidence.

### 1.2. Детерминированный prefilter до любой LLM

До LLM вычитки отбрасывать или понижать приоритет очевидного мусора:

- numbered generic labels: `Rat 1`, `Rat 2`, `Food 5`, `Chest Static 1`, `Candle 003`, `Boarded Door 003`, `Awning 001`;
- route/instance/resref labels: `auren2`, `CBA_RND_F1`, `NW_COMMONER`, `wp_*`, `dst_*`;
- generic creature/person labels без уникального имени: `Human Male`, `Human Female`, `Commoner`, `Patron`, `Almraiven Resident`, `Almraiven Shopper`;
- generic placeable/environment names с числом: `Boat 1`, `Oak Tree 1`, `Flower Patch 4`, `Display Case 2`;
- single common nouns без strong evidence: `Kit`, `Food`, `Armor`, `Patron`;
- area-path prefixes: не держать 126 отдельных entries `Almraiven - ...`, если для текущего dialog нужен только `Almraiven`, `Diving Dolphin`, `Halruaa`, etc.

Правило: prefilter не обязан удалять строку из перевода. Он решает только, может ли строка стать сущностью/глоссарной опорой. Например `Food 5` можно перевести как label, но нельзя включать в glossary.

Отдельно нужен template-dedup для numbered labels: если строки отличаются только числом или zero-padded числом, переводить общий шаблон один раз и восстанавливать номер после перевода. Примеры групп:

- `Food 1`, `Food 5`, `Food 18` -> translate template `Food {n}` -> `Еда {n}` или более подходящий label;
- `Chest Static 1`, `Chest Static 2` -> `Static Chest {n}`/`Chest Static {n}` как один шаблон;
- `Candle 001`, `Candle 002`, `Candle 003` -> сохранить padding и перевести base label один раз;
- `Underdark Qube 1`, `Underdark Qube 2` -> перевести `Underdark Qube {n}` один раз, но не превращать `Qube 1` и `Qube 2` в отдельные glossary entities.

Это снижает LLM-вызовы и повышает консистентность, но требует allowlist/heuristics: не применять template-dedup к строкам, где число меняет смысл естественной фразы, даты, цены, уровни квестов или уже готовые предложения.

Проверка:

- tests на семейства `Rat 1/2/3`, `Food 5`, `Candle 003`, `Human Female`, `Almraiven Resident`;
- на Almraiven full log 539 generic numbered records не попадают в glossary candidates;
- эти же 539 generic numbered records группируются в template families для перевода с восстановлением числа, если проходят safety heuristics;
- `Brynlo`, `Gewia`, `Diving Dolphin`, `The North Wall`, `Mount Talath` проходят prefilter.

### 1.3. Отдельная LLM-вычитка кандидатов

После deterministic prefilter запускать отдельную LLM-фазу `GlossaryCurator`, не переводчик. Она получает не все строки подряд, а компактные records с evidence:

```json
{
  "name": "Gewia the Wererat",
  "category": "character",
  "sources": ["dlg_speaker", "entity_extractor"],
  "frequency": 3,
  "contexts": ["Gewia the Wererat, bolts from the establishment..."],
  "technical_flags": []
}
```

Модель возвращает строго JSON:

```json
{
  "Gewia the Wererat": {
    "decision": "keep",
    "canonical_name": "Gewia the Wererat",
    "reason": "unique_character_epithet",
    "priority": 90
  },
  "Food 5": {
    "decision": "drop",
    "reason": "numbered_generic_placeable",
    "priority": 0
  }
}
```

Решения:

- `keep`: настоящая важная сущность, можно переводить и использовать в glossary;
- `local_only`: можно использовать только в пределах конкретного ресурса/chunk, не в run-wide glossary;
- `drop`: не сущность, не включать в world/glossary prompt;
- `alias_of`: алиас/вариант уже существующей сущности, например короткое имя против полного имени.

Проверка:

- LLM curation принимает partial JSON и retry only missing keys;
- curated glossary сохраняется в metrics/log с причинами `keep/drop`;
- на sample из `big glossary issue example.txt` 126 `Almraiven - ...` entries схлопываются до малого набора релевантных локаций текущего dialog.

### 1.4. Ранжирование и prompt assembly с бюджетом

Prompt context строится не из полного curated glossary, а из top-N для конкретного запроса:

- exact mention in source text: максимальный приоритет;
- DLG speaker/tag/resource-local entity: высокий приоритет;
- entity appears in same dialog file: высокий приоритет;
- area/journal prefix match (`Almraiven - ...`): низкий приоритет, если точная локация не упомянута;
- generic race/class labels не являются evidence;
- entries сортируются по `exact > local > frequency > curator priority > source priority`;
- жесткие лимиты отдельно для `WORLD CONTEXT`, `GLOSSARY`, `RACE TERMS`.

Важно: бюджет prompt-а достигается выбором меньшего числа релевантных entries, а не урезанием описаний у уже выбранных NPC/entities. Если NPC выбран как релевантный speaker/current dialog actor или явно упомянутая сущность, его описание может быть важным для тона, статуса, пола, расы и контекста, поэтому оно должно сохраняться полностью в рамках существующего формата world context.

Для `_vi_rnd_f6.dlg` ожидаемый compact context должен содержать примерно: `Gewia`, `Gewia the Wererat`, `Brynlo`, `Diving Dolphin`, `Almraiven`, `Auren Society`, `Halruaa`, `The North Wall`, `Mount Talath`, `Underdark`, плюс race terms `dwarf/dwarven`. Он не должен содержать сотни NPC, потому что они тоже `Human Female`, и не должен содержать 126 `Almraiven - ...` локаций.

Проверка:

- snapshot test для `_vi_rnd_f6.dlg`-подобного synthetic prompt: `WORLD CONTEXT` + `GLOSSARY` меньше dialog script или максимум 30-40% общего prompt-а;
- на Almraiven: world/glossary input tokens падают минимум в 5 раз на проблемном prompt-е;
- golden sample из 50 важных имен сохраняет канонические переводы важных NPC/локаций;
- metrics показывают counts: raw candidates, prefilter dropped, curator keep/local_only/drop, prompt-selected entries.

### 1.5. Результаты валидации этапов 0 + 1 на Almraiven

Прогон `scripts/dump_context_glossary.py` на полном `Almraiven.mod` дал артефакты в `workspace/almraiven_context_glossary/`. Изолированная сборка prompt-секций для диалога из `big glossary issue example.txt` через `scripts/compare_prompt_isolated.py` подтверждает работоспособность этапа 1 в целом и обнажает остаточные дефекты ранжирования.

**Метрики prefilter + curation (stage 1.1–1.3):**

- raw candidates: 2 295;
- decisions: `keep` 1 106 / `drop` 863 / `local_only` 294 / `alias_of` 32;
- detached deterministic prefilter работает: все 333 кандидата с `technical_score >= 80` ушли в `drop`;
- generic-имена (`Human Female`, `Human Male`, `Commoner`, `Almraiven Resident`, `Almraiven Shopper`) полностью отсутствуют в curated glossary;
- стоимость stage: `entity_extraction` 225 запросов / 417k input tokens, `glossary_curation` 35 запросов / 172k input tokens, `glossary` translate 30 запросов / 22k input tokens. `entity_extraction` остаётся самой дорогой фазой контекстного пайплайна; план её специально не оптимизирует, но это уже измеряемый baseline.

**Метрики per-prompt selection (stage 1.4) на проблемном диалоге `_vi_rnd_f6.dlg`:**

| Сценарий | WORLD CONTEXT | GLOSSARY | Сумма |
| --- | ---: | ---: | ---: |
| Исходный prompt из `big glossary issue example.txt` | 85 328 | 20 597 | 105 925 |
| Полный (без `source_texts`) | 111 941 | 41 862 | 153 803 |
| Отфильтрованный по репликам диалога | 6 513 | 3 024 | 9 537 |

Сокращение к проблемному prompt-у: WORLD CONTEXT ≈ 13×, GLOSSARY ≈ 6.8×, сумма ≈ 11×. Целевое требование «минимум 5×» перевыполнено. Доля контекста в `(контекст + диалог)` падает с ~90% до 68.6% — для маленького диалога порог `< 30-40%` строго не достигается, но абсолютные токены упали в порядок раз.

В отбор корректно попали ожидаемые имена: `Almraiven`, `Brynlo`, `Gewia`, `Gewia the Wererat`, `The Diving Dolphin`, `Halruaa`, `The North Wall`, `Mount Talath`, `Talath`, `Underdark`, `Auren Society`, `street-side`.

**Остаточные дефекты ранжирования, требующие доводки в 1.4:**

1. Substring-match через `is_relevant` пропускает 19 area-paths `Almraiven - <Ward> - <Street>` и 6+ quest titles `Almraiven : <Quest>` в отобранный glossary, потому что подстрока `Almraiven` встречается в диалоге. Текущий score `exact_match * 1000 + token_count` не давит compound-имена, у которых exact-match отсутствует, в самый низ. Нужно отдельное штрафование, либо требование exact-match именно полного entry-key.
2. В отобранный world context затянулись 20+ generic NPC `Human Female` (`[Aleine]`, `[AZUV]`, `[BartenderTT]` и др.). `_GENERIC_PERSON_LABELS` штрафует имя на −500, но exact-match по тегу даёт +900/+1000, что компенсирует штраф. Нужно либо ужесточить штраф, либо требовать exact-match имени, а не только тега, для generic-помеченных entries.
3. Quest titles формата `<Location> : <Title>` стоит отрезать эвристикой ещё до curator-а — они не canonical proper names, но раздувают payload `glossary_curation`.

Артефакты валидации: `workspace/almraiven_context_glossary/{summary,metrics,candidates,glossary,world_context}.json|.txt` и `prompt_compare.txt`.

### 1.6. Доработка фильтров (итерация 2): дефекты 1–3 закрыты

После 1.5 реализованы три фикса; повторный LLM-прогон записан в `workspace/almraiven_context_glossary_v2/`.

**Реализация:**

1. **Compound keys / hierarchical noise** — `context/relevance.py:split_hierarchical`, `common_hierarchy_components`, `hierarchical_entry_passes`. Hierarchical entry проходит фильтр только если хотя бы один не-общий компонент (отброшен общий префикс по частоте ≥ 3) встречается substring-ом в источнике. Применяется в `Glossary._filter_entries_by_texts` и `WorldContext.to_prompt_block`.
2. **Generic NPC через теги** — `string_filters.is_generic_entity_label` распознаёт `<race> <gender>` (включая `Tiefling Female`, `Half-Elf Female`, `Half-Orc Male`, `Dwarven Female`, `Gnome Female`, `Human Boy/Girl/Man/Woman/Child`), а также имена с префиксом `[XX]`. В `WorldContext.to_prompt_block._keep` при активном source-фильтре generic-NPC отбрасываются полностью независимо от substring-матча: они неразличимы между собой, и описательное упоминание `Human Female` в реплике не должно подтягивать всех её носителей.
3. **Quest hierarchy labels** — `string_filters._QUEST_HIERARCHY_RE` отрезает на детерминированном prefilter имена вида `<Location> : <Title>`. 57 кандидатов в Almraiven попали под правило (раньше 0).

**Метрики глобального дампа (v1 → v2):**

- glossary entries: 1 116 → 1 088 (−28);
- candidates `drop`: 863 → 958 (+95), из них 57 — `quest_hierarchy_label`;
- `glossary_curation` input tokens: 171 883 → 165 761 (−3.6%).

**Метрики per-prompt selection на `_vi_rnd_f6.dlg` (v1 → v2 → v2 +fix #2 расширенный):**

| Сценарий | WORLD CONTEXT | GLOSSARY | Сумма | Доля контекста |
| --- | ---: | ---: | ---: | ---: |
| v1 (до фиксов) | 6 513 | 2 399 | 8 912 | 67.1% |
| v2 (fix #1+#3) | 6 507 | 2 032 | 8 539 | 66.2% |
| v2 + fix #2 расширенный | 2 274 | 2 032 | 4 306 | **49.7%** |

WORLD CONTEXT упал в **2.9×**, общая сумма контекста — в **2.07×** относительно v1. Контекст теперь меньше самого диалога (4 366 chars). Все ключевые сущности диалога (Gewia, Brynlo, Diving Dolphin, Almraiven, Auren Society, Halruaa, Mount Talath, North Wall, Underdark, silver necklace) присутствуют в отборе.

**Регрессионные тесты:** `tests/test_prompt_selection.py::test_world_context_drops_generic_npcs_when_label_appears_descriptively`, `tests/test_prompt_selection.py::test_glossary_drops_compound_entries_without_significant_match`, `tests/test_prompt_selection.py::test_glossary_keeps_compound_entry_when_significant_component_matches`, расширенный `test_is_generic_entity_label` на 13 кейсов, `test_string_filters.py::test_quest_hierarchy_label_dropped` на 4 примера.

**Остаточный мелкий шум** (нижний приоритет, не блокирует переход к этапу 2):

- GLOSSARY бюджет 40 entries: ~25 слабо-релевантных entries (`Council of the Evelyn Society of Thinkers`, `Mistress of the Shawl Staff`, `Ear of an Old Woman`) добираются по weak-match, поскольку строгих совпадений недостаточно для заполнения бюджета. Вариант: при недозаполненном бюджете не добирать через single-token weak match, ставить порог по minimum score.
- KEY ITEMS: предметы с префиксом `Almraiven`/`Auren` подтягиваются substring-ом общего префикса. Hierarchical detector не срабатывает, потому что `Almraiven Robe Yellow` — не разделитель `:` / ` - `.

## Этап 2. Батчевый перевод NCS после gate

Проблема: NCS gate уже работает батчами, но одобренные NCS строки переводятся по одной. В Almraiven это 3 344 записи, 2 071 уникальный оригинал, 3 119 строк короче 50 символов.

План:

1. После `_run_ncs_llm_gate` выделить отдельную очередь approved NCS items.
2. Батчить NCS по профилю `script_message`:
   - короткие `SpeakString`, `SendMessageToPC`, `ActionSpeakString`, `SetCustomToken` в JSON batch;
   - длинные или multiline оставить single-call;
   - ключ результата должен быть `item_id`, а не original text, потому что один и тот же текст может жить в разных offsets.
3. Дедуплицировать внутри NCS батча по sanitized text + hint/function, но реплицировать результат обратно во все `item_id`.
4. Использовать минимальный glossary для NCS:
   - по умолчанию только entries, реально найденные в batch text;
   - для системных сообщений без имен не отправлять glossary вообще.
5. Сохранить fail-closed поведение:
   - если batch JSON не распарсился, split batch пополам;
   - если конкретный item не вернулся, retry single with minimal context;
   - при timeout не блокировать весь файл.

Проверка:

- unit tests: mixed approved/rejected NCS, duplicated strings with distinct item_id, batch parse failure fallback, timeout fallback;
- на Almraiven: NCS translate calls сокращаются с порядка тысяч до порядка сотен или меньше;
- `ncs_translations_by_item_id` полностью покрывает те же approved items, что и до изменения;
- `tests/test_batch_timeouts.py` и NCS patch tests проходят.

## Этап 3. Батчевый путь для маленьких DLG

Проблема: контекстный DLG-перевод хорош для крупных деревьев, но дорог для файлов на 1-5 строк. В Almraiven таких 223 DLG-файла, из них 142 файла имеют 2-5 строк.

План:

1. В `ContextualTranslationManager` добавить small-dialog mode:
   - eligible: `keys_for_api <= 5`, script char count ниже лимита, нет сложных branching hints или есть только линейные node pairs;
   - группировать несколько small DLG-файлов в один provider batch, но сохранять ключи вида `filename::E12`.
2. Использовать компактный dialog profile:
   - без полного world dump;
   - glossary только по exact/relevant names;
   - короткие правила preservation, без длинных speech-style examples, если строки не похожи на broken speech.
3. Оставить крупные/сложные DLG на текущем chunked contextual path.
4. Для mismatch retry не переводить весь small batch заново:
   - retry только invalid keys;
   - cleanup разрешать только для безопасных классов артефактов.

Проверка:

- tests для small DLG batch: два файла в одном запросе, корректное разнесение translations по оригинальным текстам, cache hit между DLG и non-dialog;
- на Almraiven: количество DLG requests падает за счет 223 маленьких файлов;
- качество на sample из коротких реплик не хуже текущего contextual path.

## Этап 4. Упростить и разделить логику токенов

Проблема: текущий `TokenHandler` одновременно защищает engine tokens, inline tags, dialog action markers и dash markers. Error-log показывает, что модель часто удаляет или меняет placeholders, а валидатор иногда считает обычную пунктуацию обязательным артефактом.

План:

1. Разделить protected artifacts на классы:
   - hard structural: `<StartAction>`, `</Start>`, `<CUSTOM1004>`, `<FirstName>`;
   - soft formatting: `<<...>>`, dash action markers;
   - punctuation-only: дефисы в area/item names.
2. Для hard structural продолжать exact sequence validation.
3. Для dash markers включать preservation только в dialog/script prose, но не в name/label профилях.
4. Убрать из prompt-а лишние варианты placeholder-синтаксиса. Один формат placeholder-а легче сохранить, чем четыре разрешенных формы.
5. Сделать deterministic restoration более строгим до LLM и более простым после LLM:
   - модель видит один стабильный placeholder format;
   - validator проверяет наличие hard artifacts;
   - cleanup не должен молча принимать потерю hard tokens.

Проверка:

- regression tests по строкам из error-log: `<StartAction>...</Start>`, `<CUSTOM1004>`, gender tokens, area names with hyphens;
- число false-positive mismatch для `.are` names падает до нуля;
- hard token loss по DLG не принимается cleanup-ом без retry.

## Этап 5. Надежность JSON, timeout и retry

Наблюдения:

- glossary batch 1 вернул валидный на вид JSON с одним ключом `Kit`, но был засчитан как "no usable entries";
- `_entity.dlg` получил invalid/truncated JSON на retry;
- есть 12 translate timeout-ов и 3 финальных failed translations.

План:

1. Для glossary parsing логировать не только raw prefix, но и причину отбраковки каждого ключа.
2. Для batch JSON использовать общий recovery helper:
   - parse first object;
   - validate exact key set;
   - accept partial valid keys;
   - retry only missing/invalid keys.
3. Ввести adaptive batch sizing:
   - уменьшать batch size после timeout/parse fail для конкретного provider/model/content_profile;
   - увеличивать только после серии успешных batch-ов.
4. Для long item timeout не повторять тот же большой prompt:
   - retry with minimal context;
   - no glossary unless exact name match;
   - для NCS уже есть похожая идея, ее стоит обобщить.

Проверка:

- tests на partial JSON acceptance и missing key retry;
- error-log после Almraiven не содержит финальных failed translations для одиночных timeout-ов, если retry с minimal context успешен;
- timeout одного batch-а не превращается в массовый провал.

## Этап 6. Дешевая стратегия проверки без полного LLM-прогона

Полный Almraiven-прогон стоит денег: текущий перевод обошелся примерно в $8, поэтому прогонять весь модуль после каждого крупного этапа нельзя. Даже идеальная реализация шести этапов превратит регрессию в десятки долларов. Правильная стратегия: проверять каждый этап изолированно на сохраненных артефактах, synthetic fixtures и dry-run метриках, а полный платный end-to-end прогон делать один раз после объединения всех изменений.

### 6.1. Общие правила проверки этапов

- Все изменения, которые можно проверить без LLM, проверять без LLM.
- Для LLM-dependent кода использовать fake provider с заранее заданными JSON-ответами, timeout-ами, missing keys и invalid JSON.
- Для prompt-size улучшений использовать `big glossary issue example.txt`, JSONL и extracted fixture records как input, но не вызывать модель.
- Для NCS/DLG batching проверять количество planned calls, batch payloads, key mapping и fallback behavior через fake provider.
- Для качества prompt selection делать snapshot tests: какие entries попали в prompt, какие отфильтрованы, сколько символов в секциях.
- Для инъекции использовать уже существующие small parsed-GFF/NCS fixtures или hand-built test data, а не весь модуль.

### 6.2. Изолированная проверка по этапам

Этап 0, instrumentation:

- fake provider возвращает usage/latency metadata;
- test проверяет, что metrics пишут phase, batch size, prompt chars, token estimate, retry/timeout;
- dry-run на `full translate log.jsonl` строит summary без LLM.

Этап 1, entity collection + `GlossaryCurator`:

- unit tests на `EntityCandidate` сбор из hand-built `.utc`, `.are`, `.jrl`, `.git`, `.dlg`;
- tests на prefilter: `Rat 1/2/3`, `Food 5`, `Candle 003`, `Human Female`, `Almraiven Resident`;
- fake curator возвращает `keep/drop/local_only/alias_of`;
- snapshot на `big glossary issue example.txt`: `Almraiven - ...` entries и generic `Human Female` не раздувают prompt;
- dry-run metric: сколько raw candidates, dropped, curated keep/local_only/drop, selected for prompt.

Этап 2, NCS batch translate:

- fake provider проверяет, что approved NCS items идут batch-ами, rejected не отправляются;
- tests на duplicated sanitized text with distinct `item_id`;
- tests на batch parse failure -> split/retry -> single fallback;
- no-LLM dry-run на JSONL: сколько NCS строк сгруппировалось бы в batches и сколько calls это экономит.

Этап 3, small DLG batching:

- hand-built DLG trees на 1, 3 и 5 строк;
- fake provider возвращает keys `filename::E12`;
- tests проверяют разнесение результатов обратно по файлам, cache hits и retry only invalid keys;
- snapshot prompt для `_vi_rnd_f6.dlg`-подобного sample проверяет compact context без полного world dump.

Этап 4, TokenHandler:

- regression tests только на строки из `console errors log.txt`;
- cases: `<StartAction>...</Start>`, `<CUSTOM1004>`, `<FirstName>`, gender tokens, area names with hyphens;
- no LLM: проверяется sanitize/restore/validate/finalize на заранее заданных model outputs.

Этап 5, JSON/timeout recovery:

- fake provider возвращает partial JSON, invalid JSON, truncated JSON, timeout;
- tests проверяют partial acceptance и retry only missing/invalid keys;
- adaptive batch sizing проверяется счетчиками и следующими planned batch sizes.

### 6.3. Один финальный платный прогон

После всех этапов сделать один полный Almraiven-прогон с включенной instrumentation.

Финальная проверка архива:

- resource count совпадает с оригиналом;
- распределение resource types совпадает;
- `.ncs` offsets patch-атся по `item_id`, а не только по original text;
- `.git` instance strings применяются теми же field names, которые извлек extractor;
- итоговый translated module открывается ERFReader-ом без ошибок.

Финальная проверка качества и стоимости:

- сравнить metrics нового прогона с текущим baseline из `full translate log.jsonl` и `big glossary issue example.txt`;
- подтвердить снижение фактических LLM calls и input tokens по NCS/DLG/glossary;
- случайная выборка 100 строк по типам `.dlg`, `.ncs`, `.git`, `.uti`, `.jrl`;
- отдельная выборка строк с NWN tokens;
- сравнение количества untranslated English-like строк до/после;
- список cleanup-accepted переводов должен быть малым и объяснимым.

## Приоритет внедрения

1. Инструментация запросов и prompt budget: без этого легко оптимизировать не то место.
2. NCS batch translate: самый очевидный выигрыш, потому что gate уже батчевый, а payload короткий.
3. Glossary pruning и лимиты: снижает стоимость почти всех фаз и уменьшает timeout-ы.
4. Small DLG batching: дает выигрыш на большом числе маленьких файлов без риска для крупных диалогов.
5. TokenHandler simplification: повышает надежность, но требует аккуратных regression tests, потому что ошибка здесь может сломать инъекцию.
6. Adaptive retry/JSON recovery: закрепляет надежность после сокращения числа запросов.
