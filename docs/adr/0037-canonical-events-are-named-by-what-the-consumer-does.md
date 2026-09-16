# ADR-0037: Каноническое событие называется по тому, что с ним делает consumer, а факт surface'а входит в словарь по трём вопросам

## Статус

Принят (HATS-1987, 2026-09-16).

Закрепляет практику, по которой словарь `ai_hats_observe.canonical` строился с
HATS-1966, и отвечает на вопрос, который встанет при втором reader'е
(HATS-1968, per-surface split): *как понять, что частный тип surface'а надо
слить в общий, а не завести рядом*.

## Контекст

`events.jsonl` — одна запись сессии для всех пяти surface'ов. Consumer этой
записи (post-hoc audit, судья, headless-контроллер) не должен знать, какой
surface её произвёл. Значит, у каждого surface'а есть reader, который переводит
его собственные факты в общий словарь, и вопрос «что общее, а что частное»
решается на каждом новом reader'е.

У surface'ов один и тот же факт называется по-разному и ведёт себя по-разному.
Вопрос человеку — это `AskUserQuestion` у claude, `ask_question` у cline,
`question` у opencode и `request_user_input_async` у codex; первые три
блокируют ход до ответа, четвёртый возвращается через 50 мс с
`{"accepted":true}`, а ответ приходит следующим prompt'ом без ссылки на вызов.
Отказ инструмента — проза в `tool_result` у claude, `status: declined` плюс
JSON-вердикт guardian'а у codex, `state.error` у opencode, ничего у agy.
Полная матрица — [1].

Два способа ошибиться. Первый — завести параллельную таксономию: у каждого
surface'а свои события, consumer снова разбирает провайдера. Второй — слить по
имени: назвать codex'ов асинхронный вопрос тем же событием, что и
блокирующий, и получить запись, которая врёт («ждали — дождались» за 50 мс).

## Решения

### D1 — событие называется по обязательству consumer'а, не по слову surface'а

Имя и форма канонического события отвечают на вопрос *что с этим делает тот,
кто читает*: кто должен действовать (`PersonActionRequired` /
`HarnessActionRequired` / `Notice`), ждёт ли run человека (`PersonAsked`),
закончился ли он (`RunEnded`), чья это работа (`agent`). Слово surface'а никогда
не становится именем события. Так уже устроен `signals.py`
(«Naming here follows the obligation, never the surface's own error
vocabulary») — D1 распространяет это на весь словарь.

### D2 — слово surface'а не теряется: `tool`, `raw_code`, `item_emitted`

Перевод честен только если обратим по информации. Каждое каноническое событие
несёт исходное написание рядом с общим смыслом: `PersonAsked.tool =
"AskUserQuestion"`, `Notice.raw_code = "[Request interrupted by user]"`,
`HarnessActionRequired.raw_code = "429"`, а вызов инструмента целиком лежит в
`ItemEmitted(ToolCallItem)`. Consumer, которому нужно частное, берёт его из
этих полей; consumer, которому нужно общее, не смотрит туда вовсе.

### D3 — три вопроса, по порядку, решают «мержить или нет»

Автор reader'а для нового surface'а задаёт их каждому факту, у которого есть
похожее каноническое событие:

1. **Одинаковая реакция consumer'а?** Если контроллер на этот факт делает то же,
   что и на существующее событие, — это то же событие. Имя surface'а идёт в
   `tool` / `raw_code` (D2).
2. **Одинаковый контракт, а не только имя?** У события есть контракт сверх
   названия: `PersonAsked` открыт, пока у `call_id` нет `ToolResultReceived`;
   `ResponseEnded` приходит ровно раз; `Blocking` значит «run дальше не идёт».
   Если факт surface'а контракт не выдерживает — это *не то же событие*, даже
   если по имени «вопрос». Развилка: расширить контракт (и все producer'ы с
   ним), добавить поле-вариант (`kind`), или не маппить и оставить факт в
   `item_emitted` + `Notice`. Решается на plan-gate этого reader'а, с
   evidence из матрицы [1], а не заранее.
3. **Различие — в написании или в семантике?** Написание → D2. Семантика →
   поле или новое событие.

### D4 — порог входа в словарь: producer на двух surface'ах или живой consumer

Новое каноническое событие или поле появляется, когда его умеют произвести
хотя бы два surface'а *или* когда есть consumer, который на него реагирует.
Иначе факт живёт в `raw_code` / `item_emitted` и ждёт. Это design-minimalism,
применённый к словарю: событие с одним producer'ом и без consumer'а — это
частный тип, надевший общее имя.

### D5 — матрица до reader'а, `UNSUPPORTED_RECORD` после

Перед reader'ом для surface'а строится матрица «каноническое событие ×
surface», где в клетке не «есть / нет», а *есть и контракт держится* / *есть,
контракт другой* / *только через hook* / *нигде* — форма [1]. Клетки второго
вида и есть список решений по D3.

После reader'а детектор — `Notice(reason=UNSUPPORTED_RECORD, raw_code=<тип>)`:
reader ничего не роняет молча, форма без дома помечается. Одна и та же
`raw_code`, сыплющаяся сотнями с нового surface'а, — это тип, которому нужен
дом; единичная — schema drift, который так и должен быть виден.

### D6 — внешний эталон: t3code

Список runtime-событий t3code [2] — нормализация шести провайдеров в один
словарь. Если факт там канонический (`user-input.requested`, `turn.aborted`,
`tool.denied`, `session.exited`) — сильный prior, что и у нас он общий. Если
его там нет — повод спросить, нужен ли он вообще. Эталон, не источник: их код
не переносится, их таксономия — да.

## Следствия

- Reader для codex обязан решить вопрос про `request_user_input_async` явно,
  по D3.2: контракт `PersonAsked` он не выдерживает (матрица [1], строка 2a).
- `MODEL_SWITCHED` сегодня стоит на одном producer'е (claude), и consumer'а,
  который реагирует именно на него, нет — по D4 это кандидат на выход из
  словаря. Остаётся, потому что снять вид из `events/v1` не аддитивно;
  второй producer или consumer решит его судьбу.
- Обратный перевод — из словаря в surface — не событийный. «Ответить на
  вопрос», «разрешить вызов», «прервать ход» — это *команды*, отдельный
  контракт (у t3code — `ProviderRespondToUserInputInput`,
  `ProviderRespondToRequestInput`), транспорт которого surface-специфичен.
  Событие готовит для него адрес (`call_id`, `tool`), но сам контракт —
  HATS-1968.

## Не входит в решение

- Readers для cline, agy, opencode и codex — HATS-1968, по одному на surface;
  у трёх из четырёх запись, которую ai-hats резолвит сегодня, не годится для
  follow'а (cline переписывает файл, agy-транскрипт lossy, opencode — sqlite
  без reader'а) — [1], §«What this says», п. 8.
- Схема `events/v1` не бампится: всё, что описано, аддитивно.

## References

**[1]** — [`attachments/live-log-surface-survey.md`](attachments/live-log-surface-survey.md) — матрица «gap × surface» по корпусам пяти surface'ов, замер 2026-09-15.

**[2]** — [t3code, `packages/contracts/src/providerRuntime.ts`](https://github.com/pingdotgg/t3code/blob/d29c56a5c404cb0f58d3b2ac41762fa0d0ac28d4/packages/contracts/src/providerRuntime.ts) — канонический список runtime-событий шести провайдеров (MIT).

**[3]** — `packages/ai-hats-observe/src/ai_hats_observe/canonical/events.py`, `signals.py` — словарь и его контракты, в docstring'ах.

**[4]** — [`glossary.md`](../glossary.md) — Run lifecycle, Person asked, Whose work, Stream-only signals, Prompt origin.
