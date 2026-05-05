# How-To: цикл обратной связи (reflect-session + reflect-all)

Гайд по настройке и использованию пайплайна ретроспективы. Покрывает три флоу:

- **0. Настройка политик** — что писать в `ai-hats.yaml`, когда какая политика срабатывает.
- **1. Сессия → reflect-session agent** — авто-ретро после конкретной сессии.
- **2. `ai-hats reflect-all`** — ручной триаж накопленного бэклога гипотез и предложений.

> Команды называются `reflect-session` и `reflect-all` (не `review-*`). Полная архитектурная справка — в [`docs/reflect.md`](reflect.md). Здесь — практические рецепты.

---

## Понятийный минимум

| Сущность                | Где живёт                                           | Кто пишет                        |
| ----------------------- | --------------------------------------------------- | -------------------------------- |
| **Сессия**              | `.gitlog/session_<id>/` (audit, metrics, retro)     | runtime                          |
| **HYP** (гипотеза)      | `.agent/hypotheses/HYP-NNN.yaml`                    | человек или агент                |
| **PROP** (предложение)  | `.agent/backlog/proposals/PROP-NNN.yaml`            | reflect-session при self-problem |
| **SessionRetro**        | `.agent/retrospectives/sessions/<mode>/<id>.md`     | builder                          |
| **ReflectSession**      | `.agent/retrospectives/reflect-session/<id>.md`     | роль `reflect-session`           |
| **Reflect-all handoff** | `.agent/retrospectives/reflect-all/<ts>-handoff.md` | `ai-hats reflect-all`            |

**Гипотеза** — YAML с `success_criterion`, `observation_window`, `exit_criteria`, `freshness_rule`. Она живёт со статусом `active` до тех пор, пока не накопит достаточно вердиктов в `validation_log` для перехода в `confirmed` / `refuted` / `stalled`.

**Вердикт** — одна запись в `validation_log` гипотезы:

| verdict        | смысл                                         |
| -------------- | --------------------------------------------- |
| `confirmed`    | сессия дала свидетельство, что гипотеза верна |
| `refuted`      | свидетельство против гипотезы                 |
| `inconclusive` | данные есть, но мешанина / недостаточно       |
| `n/a`          | сессия физически не может проверить гипотезу  |

Вердикт пишется в HYP-файл атомарно через `ai-hats hyp append-verdict` (filelock-protected). `n/a` мирорится только во frontmatter ретро, в HYP-файл не пишется (чтобы не засорять observation window).

---

## Флоу 0: настройка политик в `ai-hats.yaml`

Секция `feedback` управляет всем пайплайном:

```yaml
feedback:
  session_retro:
    policy: smart           # off | always | smart | hint
    mode: llm               # programmatic | llm
    background: true        # true → запуск в detached background
    smart_threshold:
      min_turns: 5          # порог по числу ходов
      min_tool_calls: 10    # ИЛИ по числу tool-вызовов
    reminder:
      enabled: true
      max_skipped: 5        # после стольких пропущенных — баннер на старте
      window_days: 14
  judge:
    policy: manual          # off | manual
```

### Политики `session_retro.policy`

| Значение | Поведение на `session_end`                                                               |
| -------- | ---------------------------------------------------------------------------------------- |
| `off`    | ничего не происходит                                                                     |
| `always` | всегда запускается ретро                                                                 |
| `smart`  | ретро запускается, **только если** `turns ≥ min_turns` ИЛИ `tool_calls ≥ min_tool_calls` |
| `hint`   | проверяет порог, но вместо запуска показывает баннер «стоит запустить ретро вручную»     |

Условие smart-порога — **OR**, а не AND: достаточно перешагнуть один из лимитов.

### Режимы `session_retro.mode`

| Значение       | Что делает builder                                                             | reflect-session спавнится? |
| -------------- | ------------------------------------------------------------------------------ | -------------------------- |
| `programmatic` | детерминированный сборщик пишет SessionRetroV1 из метрик                       | **нет**                    |
| `llm`          | LLM-builder пишет SessionRetroV1, **затем** запускается роль `reflect-session` | **да**                     |

То есть гипотезы голосуются автоматически только при `mode: llm` (см. флоу 1). При `programmatic` reflect-session запускают руками.

### Минимальная безопасная конфигурация для нового проекта

```yaml
feedback:
  session_retro:
    policy: smart
    mode: programmatic    # сначала без LLM-расходов
    background: true
  judge:
    policy: manual
```

### Конфигурация «с гипотезами и авто-голосованием»

```yaml
feedback:
  session_retro:
    policy: smart
    mode: llm             # активирует reflect-session pipeline
    background: true
    smart_threshold:
      min_turns: 5
      min_tool_calls: 10
    reminder:
      enabled: true
      max_skipped: 5
      window_days: 14
  judge:
    policy: manual
```

После правки — `ai-hats bump`.

### Модель для feedback-loop (HATS-232)

По умолчанию ai-hats не передаёт `--model` в провайдер CLI — feedback-loop наследует ту же модель, что выбрана в Claude Code / Gemini CLI глобально. Если у тебя интерактив на Opus, ретро тоже идёт на Opus, и дешёвая телеметрия превращается в дорогую.

Два независимых поля позволяют пинить модель отдельно:

```yaml
feedback:
  session_retro:
    policy: smart
    mode: llm
    model: claude-haiku-4-5            # для LLM-builder (SessionRetroV1)
    reflect_model: claude-sonnet-4-6   # для роли reflect-session (голосование по HYP)
```

| Поле           | На что влияет                                                | Точка прокидки                                  |
| -------------- | ------------------------------------------------------------ | ----------------------------------------------- |
| `model`        | LLM-builder, который пишет SessionRetroV1 в `mode: llm`      | `claude --model <m> --print -p ...`             |
| `reflect_model`| sub-agent роли `reflect-session` (голосует по HYP, заводит PROP) | `claude --model <m> --print -p <meta-prompt>` |

Поведение:

- Если поле **не задано (`null`)** — флаг `--model` не передаётся, работает дефолтная модель CLI (бэк-совм с прежним поведением).
- Поля независимы: можно гонять builder на Haiku (быстро/дёшево), а reflect-session — на Sonnet (там выше требования к рассуждению и валидации).
- Поддерживается и для `provider: claude`, и для `provider: gemini` (флаг `--model` стандартный для обоих CLI).

Рекомендуемый дефолт для экономии без потери качества:

```yaml
feedback:
  session_retro:
    mode: llm
    model: claude-haiku-4-5            # builder = Haiku, дёшево
    reflect_model: claude-sonnet-4-6   # judge = Sonnet, качество вердиктов
```

После правки — `ai-hats bump`.

---

## Флоу 1: сессия → reflect-session

Авто-цикл, который срабатывает на завершении сессии при `policy ∈ {smart, always}` и `mode: llm`.

### Что происходит

```
session_end
  └─ runtime → auto_retro.make_decision(policy, metrics)
       │
       ├─ action=skip   → ничего
       ├─ action=hint   → баннер пользователю
       └─ action=run AND mode=llm:
             1) builder LLM пишет SessionRetroV1
                → .agent/retrospectives/sessions/llm/<id>.md
             2) спавнится роль reflect-session (detached background, claude)
                ├─ читает .agent/hypotheses/*.yaml (status=active)
                ├─ читает .agent/backlog/proposals/*.yaml (status=open)
                ├─ читает .gitlog/session_<id>/ (audit, metrics, retro)
                ├─ для КАЖДОЙ active HYP выносит вердикт:
                │     "$AH" hyp append-verdict --hyp HYP-NNN --session $SID \
                │            --verdict <kind> --evidence "<...>" \
                │            --recommendation <kind>
                ├─ при self-problem заводит PROP:
                │     "$AH" proposal create --category process --target reflect-session ...
                └─ пишет ReflectSessionV1
                   → .agent/retrospectives/reflect-session/<id>.md
             3) runtime safety net: пост-валидация артефакта
                если ReflectSessionV1 битый/отсутствует →
                автоматически создаётся meta-PROP с failed_session_id=<id>
```

### Контракт reflect-session (что обязан вернуть агент)

- `hypothesis_verdicts[]` содержит **ровно по одной записи на каждую active HYP** — пропуски запрещены.
- Если гипотезу физически нельзя проверить из этой сессии — `verdict: n/a`, и **не зовём** `append-verdict` (только мирор во frontmatter).
- Самопроблема (агент не понял HYP, не нашёл данных) → `proposal create` + `inconclusive` + ссылка в `self_problems[]`.
- При `confirmed/refuted/inconclusive` — обязан вызвать `ai-hats hyp append-verdict`.

Вся эта логика — в скилле `hypothesis-validation` (`libraries/skills/hypothesis-validation/SKILL.md`), который автоматически подключён к роли `reflect-session`.

### Как валидирует харнес

Два слоя «no-silent-failure»:

1. **In-skill (LLM-driven):** скилл явно требует один verdict на каждую active HYP, описывает enum'ы и запрещает silent `n/a`.
2. **Runtime (programmatic):** после завершения детач-процесса читает `.agent/retrospectives/reflect-session/<id>.md`, парсит как `hats-reflect-session/v1`. При любой из проблем (файл отсутствует, схема не парсится, не все active HYP покрыты) — пишет meta-PROP с `category=process`, `target=reflect-session`, `failed_session_id=<id>`. Эти PROP всплывают в reflect-all.

### Запуск вручную (foreground, для отладки)

```bash
ai-hats reflect-session --session <id>            # foreground
ai-hats reflect-session --session <id> --background   # как в авто
```

Полезно когда:

- авто-ретро упал, и хочется посмотреть стек интерактивно;
- нужен ретро на сессию, которую не накрыло порогом `smart`;
- LLM-режим только что включили, прогоняем «холодный» прогон на старой сессии.

---

## Флоу 2: `ai-hats reflect-all` — ручной триаж бэклога

Когда HYP'ов и PROP'ов накопилось много — пора руками пройтись по бэклогу и закрыть/принять/отклонить пачкой.

### Жизненный цикл команды

```
1. ai-hats reflect-all
   ├─ Pre-flight (Python):
   │   собирает active HYP + open PROP
   │   пишет .agent/retrospectives/reflect-all/<ts>-handoff.md
   │   handoff содержит указатели на:
   │     - HYP-NNN (с краткой выжимкой validation_log)
   │     - PROP-NNN (с rationale)
   │     - подсказки по командам ai-hats hyp/proposal
   └─ os.execvp claude <pointer-prompt>
        ↓ переходишь в интерактивный чат
        в чате используешь:
          ai-hats hyp show HYP-NNN
          ai-hats hyp append-verdict ...
          ai-hats proposal show PROP-NNN
          ai-hats proposal status PROP-NNN <accepted|rejected|deferred|duplicate>
          ai-hats task create ...   # если нужно завести задачу
2. Когда чат закончен — bulk-flip:
   ai-hats reflect-all commit \
     --accept PROP-3 --accept PROP-7 \
     --reject PROP-12 \
     --defer PROP-15 \
     --duplicate PROP-9
```

### `--dry-run`

```bash
ai-hats reflect-all --dry-run
```

Только собирает handoff, не зовёт claude. Удобно:

- посмотреть что вообще накопилось;
- скопировать handoff в любой редактор / в другой инструмент;
- проверить что pre-flight отрабатывает в CI.

### Когда запускать reflect-all

- 5+ open PROP в `.agent/backlog/proposals/` → reflect-session начал генерить шум, надо разгрести.
- Ретро-ремайндер на старте сессии: «X дней без reflect-all, Y скипов» — запускать.
- Перед мержем большого изменения в роли/скилле — пройтись по active HYP и зафиксировать состояние.
- Ручной приём «раз в неделю» — рутинная гигиена.

### Что не делает reflect-all

- **Не голосует за гипотезы автоматически** — это работа reflect-session на конкретной сессии. reflect-all только показывает накопленное и помогает принять решения по PROP / закрыть HYP.
- **Не создаёт новые HYP** — для этого см. `hypothesis-workflow` skill (отдельный флоу через `ai-hats task create --tag hypothesis`).

---

## Как гипотеза доходит до закрытия

Сводный путь от создания до `confirmed`/`refuted`:

```
1. Создание (вручную или из judge-aggregate):
   .agent/hypotheses/HYP-042.yaml  status: active
       success_criterion: "..."
       observation_window: "10 sessions"
       exit_criteria.confirm / refute / stalled

2. Накопление вердиктов:
   каждая сессия (mode=llm) → reflect-session →
     ai-hats hyp append-verdict --hyp HYP-042 --verdict ... --recommendation ...
   validation_log растёт.

3. Триаж в reflect-all:
   pre-flight handoff показывает счётчики
   (например, "8 confirmed, 1 inconclusive, 0 refuted").
   Сравниваешь с exit_criteria.confirm.
   В чате — закрываешь:
     ai-hats hyp ... # перевод status=confirmed/refuted (см. ai-hats hyp --help)
   ИЛИ продлеваешь (recommendation=extend_window).

4. Закрытие:
   HYP-NNN.yaml: status: confirmed | refuted | stalled
                 closed: 2026-05-05
   из active-списка пропадает, reflect-session перестаёт за неё голосовать.
```

---

## Чек-лист: «у меня сломалось»

| Симптом                                   | Куда смотреть                                                                                                                                                             |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| reflect-session не запускается            | `feedback.session_retro.policy` ≠ `off`, `mode: llm`, порог `smart_threshold` достигнут                                                                                   |
| validation_log пустой после сессии        | session_retro отработал в `programmatic`, не `llm`. Поменяй `mode: llm` и `ai-hats bump`                                                                                  |
| meta-PROP `failed_session_id=...`         | runtime safety net поймал битый артефакт. Открой `.agent/retrospectives/reflect-session/<id>.md`, прогони `ai-hats reflect-session --session <id>` foreground для повтора |
| reflect-all падает с «claude not in PATH» | установи Claude Code или используй `--dry-run` и работай с handoff в редакторе                                                                                            |
| `Overlay: cannot remove ...`              | не относится к feedback loop — см. [how-to.md](how-to.md)                                                                                                                 |

---

## Связанные документы

- [`docs/reflect.md`](reflect.md) — архитектура pipeline, schema-таблица, follow-up tasks.
- [`docs/how-to.md`](how-to.md) — общие how-to по `ai-hats.yaml` (роли, оверлеи, библиотеки).
- `libraries/skills/hypothesis-workflow/SKILL.md` — как заводить новые HYP из judge-агрегаций.
- `libraries/skills/hypothesis-validation/SKILL.md` — контракт reflect-session при голосовании.
