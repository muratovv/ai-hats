# ADR-0034: DSL библиотеки — сегодняшний под `-r` и целевой под `-re`

## Статус

Предложен (HATS-1962, 2026-09-13; ревизия 2026-09-13 по ревью супервизора:
файловый нейминг, тип `override`, `EXECUTABLE` снят, конфиг без состава).
Часть серии плоской модели: обзор и слои — ADR-0033 [1]; правила, по которым
этот DSL превращается в план, — ADR-0035 [2]; форма плана и интерфейс
харнеса — ADR-0036 [3]. Статус меняется на «Принят» вместе с ADR-0033.

Документ описывает **слой DSL** и только его: то, что пишет автор, и то, чем
он запускает. DSL ничего не вычисляет — что значит выражение, решает
процессинг [2]; что получает харнесс, решает план [3].

## Контекст: сегодняшний DSL, списанный с кода

Чтобы целевой DSL читался как замена, а не как добавка, сегодняшний записан
целиком — по схемам `src/ai_hats/libraries/models.py`,
`src/ai_hats/config/project.py`, `src/ai_hats/config/user.py`,
`src/ai_hats/config/overlay.py`, `src/ai_hats/role_spec.py` и по CLI.

### Корни и каталоги

Корень библиотеки — каталог с `roles/ traits/ rules/ skills/` (плюс
`initial_injections/`, `pipelines/`, общий payload хуков `hooks/`). Корни
выстроены в цепочку ADR-0021 [4]: `core → usage → ai-hats-dev → пакеты через
entry-point ai_hats.skills → ~/.ai-hats → library_paths → <project>/libraries`;
резолв компонента — last-wins **внутри своего вида**.

### Роль и трейт: `config.yaml`

```yaml
# ai-hats-dev/roles/maintainer/config.yaml (сокращено)
name: maintainer
priorities: [Reliability, Cleanliness, Velocity]     # читается ТОЛЬКО у корневой роли
composition:
  traits: [trait-base, trait-agent, trait-se-mindset, …, ai-hats-gates, dev::python, dev::shell]
  rules: []
  skills: []
injection: |
  # ROLE: AI-HATS MAINTAINER
  …
```

Трейт — тот же файл без `priorities`; трейт не вправе называть трейты
(`Composer._resolve_traits`). Инъекции рендерятся в порядке «трейтовые, потом
ролевая», правила — фиксированным блоком `## RULES` после всех инъекций,
приоритеты — блоком `## PRIORITIES` первым. Порядок задан типом, не записью.

### Привязка скрипта к точке: `composition.apps`

```yaml
# ai-hats-dev/traits/ai-hats-gates/config.yaml
composition:
  skills: [quality-gate]
  apps:
    rack:
      tasks:                                 # трейл ключей = объект приложения (бэклог)
        - run: quality-gate/hooks/done-gate.sh   # <скилл>/<путь>
          at: ['->done']
          on_error: refuse
    wt:
      - run: quality-gate/hooks/merge-gate.sh
        at: [pre-merge]
        on_error: refuse
```

ai-hats владеет четырьмя ключами строки — `run`, `at`, `on_error`, `consent`;
остальное — непрозрачный cargo приложения. Строка, называющая
некомпозированный скилл, — отказ.

### Консент: `apps.consent_gate`

```yaml
# core/traits/trait-agent/config.yaml
composition:
  apps:
    consent_gate:
      rack.transition:
        - at: ['plan->execute', '->done']    # одна строка — несколько точек
          consent: true
      wt.merge:
        - at: [pre-merge]
          consent: true
```

`consent` трёхзначен: отсутствие ничего не говорит, `true`/`false` —
утверждение. Разрешение по точке — last-writer-wins в порядке композиции;
роль вправе перевернуть трейтовое `true` в `false` под одну строку `WARN`.
Исполняемого у строки нет: обёртку бинаря порождает движок
(`consent_wrapper.py`), а хук `safety-guard` читает декларацию из
`role_materialization.json`. Реестр операций (`rack.transition`, `wt.merge`,
`wt.discard`) живёт в коде.

### Правило: `rules/<имя>/rule.md`

Только тело. Имя — каталог. Заголовок `### <имя>` внутри `## RULES` дописывает
рендер.

### Скилл: `skills/<имя>/SKILL.md` и его frontmatter

```yaml
---
name: safety-guard
description: PreToolUse hooks that refuse destructive commands, …
ai_hats:
  runtime_hooks:                  # точка харнесса + матчер + скрипт
    PreToolUse:
      - matcher: Bash|run_command|execute
        script: hooks/safety_gate.py
  git_hooks:                      # событие git → [пути]; без at и on_error
    pre-push: [git_hooks/pre-push-e2e.sh]
  worktree:                       # непрозрачно для библиотеки
    wt_in: hooks/provision-venv.sh
  requires: { cli: [ai-hats-rack] }
  triggers: [...]
allowed-tools: …
---
```

Три канала привязки живут во frontmatter скилла — с тремя грамматиками.
Носителей по замеру: `git_hooks` — 5 скиллов / 11 скриптов, `runtime_hooks` —
6 / 12, `worktree` — 1 / 1.

### Вне библиотеки

- **Дропины** `.githooks/<событие>.d/<файл>` — грамматики нет, привязка это сам
  файл; `run_chain` (`src/ai_hats/githooks_run.py`) подмешивает их к
  композированным гейтам.
- **Проектные правила** `<ai_hats_dir>/user-rules/*.md` — читаются все,
  без имени и селектора, рендерятся жёстко зашитым блоком `## USER RULES`.

### Конфиги

```yaml
# <project>/ai-hats.yaml — сегодня один файл на всё
provider: claude
default_role: maintainer          # выражение с операторами — отказ
active_role: maintainer
library_paths: [./libraries]
customizations:                   # словарь роль → оверлей; вилдкарда нет
  maintainer:
    add:    { traits: [ai-hats-gates], rules: [], skills: [] }
    remove: { traits: [], rules: [], skills: [] }
    injection_append: ""
feedback: …
task_prefix: HATS
venv_path: …
ai_hats_dir: .agent/ai-hats
harness: …
worktree: …
schema_version: 4
migration_step: 0
```

```yaml
# ~/.ai-hats/customizations.yaml     # тот же словарь, применяется ДО проектного
customizations: { maintainer: { add: { traits: [personal-workflow] } } }
# ~/.ai-hats/library_paths.yaml      # отдельный файл под корни
paths: [~/dev/ai-hats-custom]
```

Порядок оверлеев: пользовательский → проектный → рантайм. Оверлей,
называющий отсутствующий компонент, делает композицию lossy, и все переходы под
ролью отказывают — поэтому компонент обязан приехать раньше конфига.

### Рантайм-спек

```
spec := role (op name)*      op := '+' | '-'
```

База обязательна и обязана быть ролью; операнд — трейт, правило или скилл
(роль в операнде — отказ «is a role — only traits, rules and skills can be
mixed in»); повтор операнда — отказ; `+` и `-` хранятся раздельно
(`RoleSpec.adds` / `removes`), поэтому порядок между ними теряется;
каноникализация печатает `role + adds… - removes…`. В файл выражение не
попадает: `customize`, `config set --role`, `init --role` отказывают
(*«runtime composition ('+' / '-') cannot be persisted»*).

### Входы CLI

| Вход                                                                | Что принимает                                     |
| ------------------------------------------------------------------- | ------------------------------------------------- |
| `ai-hats -r "<spec>" [--dry-run]`                                   | сессия HITL                                       |
| `ai-hats agent <role> --task … [--dry-run]`                         | саб-агент (Automate); только имя роли             |
| `ai-hats execute --role <role>`                                     | пайплайн с ролью                                  |
| `ai-hats config show-prompt --role "<spec>" [--stats]`              | рендер промпта мимо материализации                |
| `ai-hats config set --role <role>` / `init --role`                  | запись `active_role` / `default_role`; только имя |
| `ai-hats config customize <role> --add-trait … --global`            | правка словаря `customizations`, девять флагов    |
| `ai-hats list roles\|traits\|rules\|skills\|tokens <имя> [--trait]` | каталоги по видам                                 |
| `ai-hats reflect role <role>`                                       | аудит роли по её слоистому экспорту               |

### Что в этом DSL измерено как дефект

| Дефект                                                                                                                                  | Где измерено                                |
| --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Привязка говорится **семью** способами: три канала frontmatter, `apps`, дропины, `user-rules/`, плюс `add`/`remove` против алгебры `-r` | таблица каналов в ADR-0033 [1]              |
| Порядок промпта задан типом: трейт из командной строки встаёт на 84 строки раньше голоса роли                                           | HATS-1557, замер `-r "maintainer + worker"` |
| Трейт нельзя запустить и сравнить: `show-prompt --role worker` → rc=2                                                                   | HATS-1961, RED-базлайн                      |
| Вилдкарда нет: гейты вооружают 2 роли из 18, машину состояний ходят 12                                                                  | HATS-1955                                   |
| Выражение нельзя сохранить: `customize` отказывает                                                                                      | `src/ai_hats/cli/assembly.py`               |
| Проектное доопределение живёт в гитигнорённом файле                                                                                     | HATS-1959                                   |
| Новый промпт требует каталога с конфигом: файл нельзя просто положить и дописать `+` в выражение                                        | ревью 2026-09-13                            |

## Решения

### D1 — Три правила и шесть типов

Форма целиком выводится из трёх правил:

1. **Папка — неймспейс, файл — имя.** Имя сущности — сегменты пути от корня
   слоя через `::`: папки, затем имя файла без последнего расширения
   (`rules/rule_backlog_discipline.md` → `rules::rule_backlog_discipline`,
   `rack.transition.yaml` → `rack.transition`). Папка именем не бывает.
2. **Сущность — файл, объявляющий `type` и `schema_version`.** Всё остальное
   в дереве — payload; его называет сущность относительным путём от своего
   файла (`run: done-gate.sh`), и он обязан лежать внутри того же корня.
   Корень — логическое дерево: симлинк внутри корня читается как его
   содержимое (замер: один скилл пользовательского корня — симлинк наружу).
3. **Один резолв: терм — суффикс имени по границам сегментов.** `prompt`
   подходит всякому имени, кончающемуся на `::prompt` или равному `prompt`;
   `maintainer::prompt` — тот же суффикс, длиннее. Резолв идёт по слитому
   дереву всех слоёв (last-wins между слоями — по **полному имени**). Ровно
   одно имя — резолв; ноль — отказ «неизвестное имя»; больше одного — отказ с
   перечислением кандидатов. Локальной области видимости нет: файл видит то же
   дерево, что и командная строка. Правило резолва — [2] D2; здесь — форма
   терма.

Типов шесть: четыре базовые сущности, композит над ними и override:

| `type`      | Что несёт                                                                 | Файл             | Терм? |
| ----------- | ------------------------------------------------------------------------- | ---------------- | ----- |
| `composite` | именованное выражение над сущностями и композитами; метки; строка витрины | `<имя>.yaml`     | да    |
| `prompt`    | текст, **блок**, в который он вставляется, и заголовок внутри блока       | `<имя>.md`       | да    |
| `hook`      | **что и когда** — вид, адрес точки, payload или операция, правило отказа  | `<имя>.yaml`     | да    |
| `operation` | **что охраняется** — операция консента: бинари и адаптер чтения из argv   | `<имя>.yaml`     | нет   |
| `skill`     | процедура и её тело; frontmatter — часть сущности                         | `<имя>/SKILL.md` | да    |
| `override`  | **кому** — выражение, дописываемое каждому композиту по селектору меток   | `<имя>.yaml`     | нет   |

«Терм?» — свойство типа, не правило: `operation` называет только хук
консента, `override` привязывается сам. `role`, `trait`, `rule`, `priorities`
исчезают как типы: роль и трейт — композиты, правило и приоритеты — промпты со
своим блоком. **Роль — это любой композит, из которого процессинг получает
валидный план** [3]: композит из одного хука без промпта — роль; сегодняшний
трейт — роль. Слово остаётся в CLI и документации как имя того, что человек
выбирает, и это **метка** `role` на композите (**D3**, **D5**).

`EXECUTABLE` предыдущей редакции снят: с правилом 2 скрипт — payload хука, а
не сущность. Один скрипт на две точки — два хука с одним относительным путём.

### D2 — Одна грамматика выражения

```
expr := [op] term (op term)*
op   := '+' | '-'                  # отдельный токен, отделён пробелами
term := name                       # суффикс имени; `::` — граница сегмента
```

- Оператор — отдельный токен: `-` внутри имени (`trait-base`) — символ имени,
  `- trait-base` — вычитание. `+` в имени не бывает.
- Ведущий знак законен и значит «дописать к тому, что ниже»: в `override` —
  к выбранному композиту, в CLI и `active` — к предыдущему непустому
  безымянному выражению (**D4**). Без ведущего знака выражение заменяет
  предыдущее. Что значит ведущий знак, под которым ничего нет, — решает
  процессинг [2] D3.
- Повтор операнда законен — `A + B - A` парсится и значит то, что говорит
  свёртка [2].
- Порядок между `+` и `-` сохраняется и разбором, и каноникализацией.
- Вид операнда не ограничен: `maintainer + sre` — законное выражение, пусть и
  странное; `maintainer + rack.transition` — отказ, потому что `operation` не
  терм (**D1**).

Это **та же** грамматика, что у рантайм-спека, с пятью правками контракта
парсера: база необязательна; повтор разрешён; порядок хранится; каноникализация
его держит; операнд любого вида. Четыре из пяти отменяют сегодняшнее
поведение, а не расширяют его.

Одна грамматика — три места записи, различаются оформлением, не смыслом:

| Где                              | Как                                                     |
| -------------------------------- | ------------------------------------------------------- |
| файл `composite` в корне         | `expr: <выражение>` — присвоение имени файла            |
| файл `override` в корне          | `expr: <выражение>` — дописывается каждому выбранному   |
| CLI и `active` машинного конфига | `-re "<выражение>"`, `agent -re`, `active:` — безымянно |

Вложенно-словарная запись `add`/`remove` ретайрится вместе с запретом
выражения в файле; конфиг состава не несёт (**D4**).

### D3 — Типы

Каждый тип — четырьмя частями: что пишется, как лежит, примеры, чего не
выражает из сегодняшнего корпуса.

#### `composite`

```yaml
# maintainer.yaml
schema_version: 1
type: composite
expr: >
  maintainer::prompt + maintainer::priorities
  + trait-base + trait-agent + ai-hats-gates + dev::python + dev::shell
tags: [role, hitl]                   # необязательно; метки для override и каталога
summary: primary development assistant for the ai-hats codebase   # необязательно; витрина
```

`expr` обязателен и непуст. **Метки** композита — его полное имя плюс `tags`;
по ним выбирает `override` и строится каталог: `role` — то, что предлагают
человеку (`list roles`), `hitl` / `automate` — для кого композит написан:
для человека в сессии или для пайплайна. Это метка каталога, не ограничение
запуска: `agent -re` принимает любой композит, и `sre` сегодня ходит обоими
путями. Поля `name` нет: имя — файл, расходиться нечему.

*Как лежит.* Файл — где угодно в корне; папка рядом с ним — соглашение для
его собственных промптов, не правило: `maintainer.yaml` и
`maintainer/{prompt,priorities}.md` связаны только тем, что выражение называет
`maintainer::prompt`. Одноимённый файл в верхнем слое — **переопределение**:
last-wins по полному имени, целиком, включая `tags` и `summary`; `expr`
вправе называть переопределяемое имя — правая часть смотрит вниз по слоям [2].

*Примеры.*

```yaml
# test-maintainer.yaml — вариант под тег, ничего своего не несёт
type: composite
expr: maintainer
tags: [role, hitl, TEST]
```

```yaml
# <project>/libraries/experimental/maintainer.yaml — проект владеет именем и доопределяет на месте
type: composite
expr: maintainer + ai-hats-gates       # `maintainer` справа — ядровой
tags: [role, hitl]                     # переопределение — целиком; теги повторить
```

```yaml
# log-bash.yaml — роль без промпта: композит из одного хука
type: composite
expr: log-bash-pretooluse
```

*Чего не выражает.* Приоритетов как поля — это промпт с `block: PRIORITIES`.
Меток чужому композиту: тег живёт на определении, и добавить его, не владея
именем, нельзя — поэтому режимные теги сеет поставляемая библиотека (**D6**).
Запрета вкладывать композиты — снят намеренно: глубина не ограничена, цикл —
отказ [2].

#### `override`

```yaml
# gates.yaml
schema_version: 1
type: override
apply: "^agent$"                         # regex; fullmatch по каждой метке композита; хотя бы одна
expr: "+ done-gate + rack.transition::done"   # грамматика D2; ведущий знак — знак применения
```

Оба поля обязательны. Смысл — правило [2] D3: для каждого композита слитого
дерева, чья метка подходит под `apply`, `N = N <expr>` в точке порядка
`(слой файла, путь файла)`; два override'а на один композит — в этом порядке.
Override — не терм и не цель другого override'а; его имя живёт в трассе
(`brought_by: gates`), в `list overrides` и в диагностике. Снять принесённое
им — вычесть терм позднее: `-re "test-maintainer - test_framework"`.

*Как лежит.* Файл — где угодно в корне; папка `overrides/` — соглашение.
Имя файла участвует в резолве наравне с остальными (правило 3): override
`overrides/maintainer.yaml` сделал бы голое `maintainer` двусмысленным
(`maintainer`, `overrides::maintainer`) — отказ с обоими кандидатами.
Называйте override по эффекту: `maintainer-shell.yaml`.

*Примеры.* Пользовательский слой одного из авторов сегодня — 16 строф
`customizations.yaml`, каждой роли `personal-workflow`, семи из них
`command-timeout`, четыре точечных добавки. Под `-re` — шесть файлов в
версионируемой пользовательской библиотеке:

```yaml
# overrides/personal.yaml — 16 строф add.traits: [personal-workflow]
type: override
apply: "^hitl$"
expr: "+ personal-workflow"
```

```yaml
# overrides/timeout.yaml — 7 строф; тега нет — список свой, но одной строкой
type: override
apply: "^(assistant|dev-web|dots-hat|maintainer|role-curator|sre|tech-writer)$"
expr: "+ command-timeout"
```

```yaml
# overrides/sre-fleet.yaml — add.traits и add.skills одной строкой: вид терма не важен
type: override
apply: "^sre$"
expr: "+ fleet-shared-state + secure-exec"
```

```yaml
# core/experimental/test_wrapper.yaml — вариант под тег: `-re test-maintainer` → maintainer + test_framework
type: override
apply: "^TEST$"
expr: "+ test_framework"
```

```yaml
# <project>/libraries/experimental/investor-no-gate.yaml — проект снимает ядровое
type: override
apply: "^investor-hat$"
expr: "- done-gate"
```

Override, а не переопределение, когда именем не владеешь: `maintainer.yaml`
в пользовательском слое с `expr: maintainer + dev::shell` — last-wins
целиком, и без `tags` в нём `maintainer` теряет `hitl`, после чего
`personal.yaml` его не видит. Переопределяешь имя — владеешь определением,
метки включительно; не владеешь — override.

*Чего не выражает.* Негативный селектор: при двух метках на композите
`^(?!TEST$).*` пропускает всё; исключение — позитивный тег или `-` в позднем
слое. Селектор по составу («все, кто несёт `trait-agent`») — нет; это тег.
Безымянное CLI-выражение меток не имеет — override достаёт его только через
имена внутри. Цели — только композиты; промпт или хук через `apply` не
доопределяется. Override, не выбравший ни одного композита (сегодняшняя
строфа `go-dev-full` для несуществующей роли), — предупреждение [2] D8, не
молчание.

#### `prompt`

```yaml
---
schema_version: 1
type: prompt
block: RULES                           # обязательно; `body` — единственное зарезервированное имя
heading: rule_backlog_discipline       # необязательно; `### <heading>` перед текстом
---
# Rule: Backlog Discipline
…
```

Frontmatter — три поля; всё после него — текст как есть. Блок объявляется
всегда, явно, без умолчания; `body` рендерится без заголовка блока, любой
другой — под `## <ИМЯ>` [2] D5. Порядок текста в промпте — порядок термов
выражения, не тип.

*Как лежит.* `<имя>.md` где угодно; соглашения миграции — `<композит>/prompt.md`
(`block: body`), `<композит>/priorities.md` (`block: PRIORITIES`),
`rules/<имя>.md` (`block: RULES`, `heading: <имя>`). Голое `prompt` в
выражении двусмысленно по построению — у каждого композита свой; пишется
`maintainer::prompt`. Имя правила остаётся голым:
`rules::rule_backlog_discipline` единственно, значит `rule_backlog_discipline`
резолвится. Композит с пустой инъекцией (замер: три трейта) промпт-файла не
получает и `::prompt` в `expr` не называет.

*Примеры.*

```yaml
# maintainer/prompt.md
---
type: prompt
block: body
---
# ROLE: AI-HATS MAINTAINER
…
```

```yaml
# maintainer/priorities.md
---
type: prompt
block: PRIORITIES
---
1. Reliability
2. Cleanliness
3. Velocity
```

```yaml
# <project>/libraries/experimental/rules/no-force-push.md — бывший user-rules/*.md
---
type: prompt
block: RULES
heading: no-force-push
---
…
```

плюс `overrides/user-rules.yaml` с `apply: ".*"` и `expr: "+ no-force-push"` —
сегодняшний `## USER RULES` без имени и селектора становится именем и
селектором.

*Чего не выражает.* Начальную инъекцию — первое сообщение сессии, не блок
промпта (**D7**). Секции, зависящие от харнесса, — следующий ADR. Ранг
приоритетов: номер в блоке — порядок появления [2] D5.

#### `hook`

```yaml
# hooks/quality-gate/done-gate.yaml — workflow: точка машины состояний приложения
schema_version: 1
type: hook
kind: workflow
app: rack
object: tasks                        # объект внутри приложения — бэклог; входит в тождество
at: '->done'
run: ../../skills/quality-gate/hooks/done-gate.sh   # payload: путь от этого файла, внутри корня
on_error: refuse
```

Хук — вид, адрес точки, эффект, правило отказа; интерпретатор payload — по
shebang. Тождество для join — `(kind, адрес, run)`, где адрес — колонка
таблицы ниже целиком (`on` для `wt_out` входит), а `run` — путь payload от
корня слоя [2] D6.

| `kind`     | Адрес точки                                                               | Эффект          | `on_error`       |
| ---------- | ------------------------------------------------------------------------- | --------------- | ---------------- |
| `git`      | `at: <git-событие>`                                                       | `run`           | нет              |
| `runtime`  | `at: <точка харнесса>`, `matcher`                                         | `run`           | нет              |
| `workflow` | `app`, `object`, `at: <точка FSM>`                                        | `run`           | `refuse \| warn` |
| `worktree` | `at: wt_in \| wt_out`; для `wt_out` — `on: [merge \| discard \| cleanup]` | `run`           | нет              |
| `consent`  | `operation`, `at: <точка операции>`                                       | порождается [3] | нет              |

*Как лежит.* `<имя>.yaml` где угодно; payload — рядом или глубже, по
относительному пути; путь наружу корня — отказ. Соглашения миграции — одно
для всех хуков, чей скрипт лежит в скилле, то есть для строк
`composition.apps` и для трёх каналов frontmatter:
`hooks/<скилл>/<стем скрипта>.yaml` (payload остаётся в папке скилла,
`run: ../../skills/<скилл>/hooks/<файл>` — скилл вправе звать тот же скрипт
из своего текста; имя по событию не годится: у `git-mastery` четыре скрипта
на `pre-commit`, у `safety-guard` четыре на `PreToolUse`);
`hooks/consent/<операция>/<точка>.yaml` для строк `consent_gate`, где `->`
в точке пишется `-`, а ведущий `-` опускается (`->done` → `done.yaml`,
`plan->execute` → `plan-execute.yaml`).

*Примеры* — по одному на оставшиеся виды:

```yaml
# hooks/safety-guard/safety_gate.yaml — runtime
type: hook
kind: runtime
at: PreToolUse
matcher: "Bash|run_command|execute"
run: ../../skills/safety-guard/hooks/safety_gate.py
```

```yaml
# hooks/quality-gate/pre-push-e2e.yaml — git
type: hook
kind: git
at: pre-push
run: ../../skills/quality-gate/git_hooks/pre-push-e2e.sh
```

```yaml
# hooks/worktree-venv/provision-venv.yaml — worktree
type: hook
kind: worktree
at: wt_in
run: ../../skills/worktree-venv/hooks/provision-venv.sh
```

```yaml
# hooks/consent/rack.transition/done.yaml — consent: один хук — одна точка
type: hook
kind: consent
operation: rack.transition           # имя OPERATION
at: '->done'
```

*Чего не выражает.* Адресата — «кому» говорит композит или override. Строку на
несколько точек (`at: [a, b]`) — по хуку на точку, и это цена гранулярности
[2] D7 (замер: одна такая строка в корпусе). Cargo приложения (сегодняшние
ключи строки `apps` сверх четырёх) — непрозрачным остаётся, форма его
переносится как есть; замер: cargo в корпусе нет. Дропин
`.githooks/<событие>.d/` — становится хуком той библиотеки, где лежит его
скрипт; дропин, дублирующий канал frontmatter скилла (замер: оба дропина
проекта зовут скрипты `commit-style` из пользовательского корня), схлопывается
в этот хук плюс override. Порядка между дропинами у сегодняшней формы нет, и
хук его не наследует — порядок задаёт выражение. Что дропин имел сверх этого —
запуск без роли — не форма, а харнесс [3].

#### `operation`

```yaml
# operations/rack.transition.yaml
schema_version: 1
type: operation
surface: rack                        # бинарь, который обёртка подменяет на PATH; `none` — держит только хук
reader: rack_transition              # адаптер (код): чтение argv, грамматика селектора, допуск
binds_ticket: true                   # предмет операции привязывает одноразовый грант (ADR-0028)
```

Поля списаны с сегодняшнего `OperationSpec`: `surface` — один бинарь или
`none`, и тогда обязателен `hook_only_because: <почему>` — как в коде, где
`None` без причины отказывает; `reader` — имя адаптера; `binds_ticket` —
флаг. Грамматика селектора `at` и допуск — у адаптера, и это код.

*Как лежит.* `<имя>.yaml`; точка в имени — символ имени. Соглашение —
`operations/`. Не терм: единственная ссылка — `operation:` хука консента.

*Пример.* Засев из сегодняшнего реестра — три файла: `rack.transition`
(`surface: rack`), `wt.merge` и `wt.discard` (`surface: ai-hats`).

*Чего не выражает.* Сам адаптер — код; библиотека называет его, не несёт.
Новую операцию без кода объявить нельзя, и это названо: реестр — данные,
адаптер — код, как в ADR-0029.

#### `skill`

```yaml
---
name: safety-guard
description: PreToolUse hooks that refuse destructive commands, …
ai_hats:
  schema_version: 1
  type: skill
  requires: { cli: [ai-hats-rack] }
  triggers: [...]
allowed-tools: …
---
```

*Как лежит.* `<имя>/SKILL.md` — папка есть сущность, имя — имя папки, дерево
внутри непрозрачно: обход корня в папку скилла не спускается,
`references/*.md` — не промпты. Верхний уровень frontmatter принадлежит спеку
Agent Skills; наше — под `ai_hats:`. Это единственное отступление от правил
1–2, и его источник внешний (**D8**).

*Пример.* `skills/quality-gate/SKILL.md` — как сегодня, минус три канала
привязки во frontmatter: `git_hooks`, `runtime_hooks`, `worktree` уходят в
файлы `hook`, а композит, звавший скилл с хуками, называет скилл **и** его
хуки: `safety-guard + safety_gate + backlog_write_gate + …` — голым именем,
пока оно единственно, иначе `hooks::safety-guard::safety_gate`.

*Чего не выражает.* Привязку к точке — это хук. Скилл теряет исполняемое
молча и без переопределения имени: на скрипт ссылались каналы привязки, не
потребители скилла.

### D4 — Конфиги: состав живёт в библиотеке, конфиг называет корни

Как собирается роль, целиком: `maintainer` — файл `maintainer.yaml` в одном из
корней; верхний слой вправе переопределить его тем же именем; после всех
присвоений override'ы всех корней дописывают ему своё по меткам в порядке
`(слой, путь)`; версионируемый конфиг называет `library.default` — выражение
свежего клона; машинный — `active`, выражение этой машины; CLI — последнее
слово, а `--library <dir>` — корень поверх проектного на один запуск, как
сегодня [2] D3. **Конфиг несёт
выбор, не состав**: `customizations` и `apply` предыдущей редакции — это
файлы `composite` и `override` в соответствующем корне, проектные — в
трекаемом `libraries/` (HATS-1959 закрывается по построению).

**Версионируемая часть** — то, чего требует репозиторий:

```yaml
# <project>/ai-hats.yaml
provider: claude
library:                                         # всё, что о библиотеке, — под одним ключом
  paths: [./libraries]
  default: maintainer                            # выражение; сегодняшний default_role
task_prefix: HATS
feedback: …
harness: …
worktree: …
schema_version: 5
```

`ai_hats_dir`, `migration_step`, `manage_gitignore` — без изменений, в
примере опущены. Файл расширяем: верхнеуровневый ключ, которого ai-hats не
знает, — чужой, о нём сообщается **предупреждением с именем ключа**, не
отказом. Внутри ключей, которыми ai-hats владеет (`library:` и остальные из
схемы), незнакомый ключ — отказ, как в любом файле сущности [2] D1.

**Машинная часть** — закрытый набор ключей, ничего из того, что проходит
ревью:

```yaml
# <ai_hats_dir>/local.yaml
active: maintainer + worker                      # выражение; перекрывает library.default, -re перекрывает его
venv: .venv                                      # сегодняшний venv_path
```

**Пользовательский слой** — один файл вместо сегодняшних `customizations.yaml`
и `library_paths.yaml`:

```yaml
# ~/.ai-hats/config.yaml
library:
  paths: [~/dev/ai-hats-custom]
```

`~/.ai-hats/experimental/` остаётся корнем цепочки; что версионировать —
кладётся в корень из `library.paths`.

### D5 — Входы CLI под `-re`

`-re` — **корневой** флаг: он выбирает DSL и процессинг, а не команду. Под ним
живут все входы, и они одни и те же для сессии, саб-агента и просмотра:

| Вход                                                                         | Что делает                                                  |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `ai-hats -re "<expr>"`                                                       | сессия HITL из выражения                                    |
| `ai-hats agent -re "<expr>" --task …`                                        | саб-агент (Automate) тем же процессингом                    |
| `ai-hats -re show-prompt "<expr>"`                                           | проекция плана: запись промпта [3]                          |
| `ai-hats -re dry-run "<expr>" [--json]`                                      | проекция плана: план целиком и параметры запуска [3]        |
| `ai-hats -re list composites\|overrides\|prompts\|hooks\|operations\|skills` | каталоги по типам; `list roles` — композиты с меткой `role` |
| `ai-hats -re list tokens "<expr>"`                                           | бюджет промпта по членам `plan.prompt.members` [3]          |
| `ai-hats -re reflect role "<expr>"`                                          | аудит по плану, а не по слоистому экспорту [3]              |

`--library <dir>` остаётся у всех входов: корень поверх проектного на один
запуск (**D4**).

Что уходит: `config customize` с девятью флагами (его заменяет файл
`override`), `config set --role` / `init --role` (их заменяют `active` в
машинном конфиге и `library.default` в версионируемом — выражением, не только
именем), `--dry-run` как флаг сессии (его заменяет `dry-run` как проекция),
`library_paths.yaml` и `customizations.yaml` (их заменяет
`~/.ai-hats/config.yaml` и корень библиотеки), оверлей на отсутствующий
компонент как законная lossy-композиция (теперь отказ на резолве [2] D2).
Безымянное выражение имени не получает: его идентичность — каноническая
строка выражения, и она едет в артефакты сессии как сегодняшний
`role_expression`.

Флаг спроектирован **на удаление**: после переезда (ADR-0033 [1], этап 3)
`-r` принимает выражение, `-re` исчезает.

### D6 — `experimental/`: полная копия корня в новой форме

Новая форма живёт в каталоге `experimental/` **внутри каждого корня** цепочки:
у поставляемой библиотеки — `experimental/{core,usage,ai-hats-dev}`, у
пользовательской — `~/.ai-hats/experimental/`, у проектной —
`libraries/experimental/`. Копию создаёт **скрипт миграции**, не рука;
кандидат `-re` читает `experimental/` каждого корня, эталон `-r` — сам корень,
и ни один не мешает другому. Условие готовности формата буквально:

```bash
rm -rf <корень>/{roles,traits,rules,skills}; mv <корень>/experimental/* <корень>/
```

— и всё работает. Из старых файлов на лету ничего не синтезируется: второй
читатель старой формы был бы вторым процессингом старого DSL, и сравнивать
пришлось бы его, а не модель.

Соответствие, по которому скрипт переводит корпус:

| Сегодня                                               | Становится                                                                                                                                      |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `roles/<имя>/config.yaml`, `traits/<имя>/config.yaml` | `<имя>.yaml` `type: composite`; `expr` **по конвенции**: `<имя>::prompt + <имя>::priorities + трейты…`; роль — `tags: [role, hitl \| automate]` |
| `injection:` роли или трейта                          | `<имя>/prompt.md`, `block: body`                                                                                                                |
| `priorities:` роли                                    | `<имя>/priorities.md`, `block: PRIORITIES`                                                                                                      |
| `rules/<имя>/rule.md`                                 | `rules/<имя>.md`, `block: RULES`, `heading: <имя>`                                                                                              |
| `skills/<имя>/SKILL.md`                               | `skills/<имя>/SKILL.md`; `ai_hats.type: skill`; `git_hooks` / `runtime_hooks` / `worktree` из frontmatter уходят                                |
| скрипт в канале frontmatter скилла                    | `hooks/<скилл>/<стем>.yaml`, `run:` в папку скилла; скрипт на месте; `wt_out` получает явный `on:`                                              |
| композит, называвший скилл с хуками                   | называет скилл **и** его хуки явно: `safety-guard + safety_gate + …`                                                                            |
| `composition.apps.rack.*` / `apps.wt.*` строка        | `hooks/<скилл>/<стем>.yaml`, `kind: workflow`; `run: <скилл>/<путь>` → относительный путь; строка на N точек — N файлов                         |
| `apps.consent_gate.<op>` строка на N точек            | N файлов `hooks/consent/<op>/<точка>.yaml`, по одному на точку                                                                                  |
| реестр операций консента в коде                       | `operations/<имя>.yaml`, засев из реестра                                                                                                       |
| `user-rules/*.md` проекта                             | `libraries/experimental/rules/<имя>.md`, `block: RULES`, плюс `overrides/user-rules.yaml` с `apply: ".*"`                                       |
| `customizations` проектный и пользовательский         | файлы `override` в соответствующем корне; строфа на несуществующую роль — предъявляется                                                         |
| `library_paths.yaml` пользователя                     | `library.paths` в `~/.ai-hats/config.yaml`                                                                                                      |
| `default_role` / `active_role`                        | `library.default` в `ai-hats.yaml` / `active` в `<ai_hats_dir>/local.yaml`                                                                      |
| `venv_path`                                           | `venv` в `<ai_hats_dir>/local.yaml`                                                                                                             |
| дропин `.githooks/<событие>.d/<файл>`                 | хук в корне, где лежит его скрипт, плюс override; дубликат канала скилла — схлопывается                                                         |

Скрипт обязан: писать композиты по конвенции, а не дословным переводом, и без
`::prompt` там, где инъекция пуста; сеять метки `role` и `hitl` / `automate`
(режим — решение человека при миграции, не эвристика: из шести ролей, которые
зовут пайплайны, три запускаются HITL); писать полные имена там, где голое
двусмысленно по правилу 3; предъявить поимённо каждое место, где
`- <композит>` после разворачивания уносит член, объявленный ещё кем-то
(замер: `maintainer - trait-agent` сегодня сохраняет `safety-guard`, завтра —
нет).

### D7 — Что остаётся вне базовых типов на время PoC

Начальные инъекции (первое сообщение сессии, не блок промпта: три из пяти —
формат-строки с плейсхолдерами), пайплайны, общий payload хуков в корне
библиотеки (`experimental/hooks/` на той же глубине от скриптов, что сегодня:
22 скрипта ходят в него относительным путём, там же реестр операций
консента), sidecar-провенанс вендоренных скиллов (`metadata.yaml`, `LICENSE`,
`NOTICE` — движок их не читает). Их дом на время PoC — `experimental/` рядом с
новыми файлами, в сегодняшней форме; постоянный выбирается на переезде
(ADR-0033 [1], этап 3).

### D8 — Что выбивается из трёх правил

Проверено после ревизии: что описывается явно, потому что не следует из
**D1**.

1. **`skill`** — папка есть сущность, имя — папки, `type` под `ai_hats:`,
   обход внутрь не идёт. Источник — спек Agent Skills, который владеет и
   именем файла `SKILL.md`, и верхним уровнем frontmatter. Не наш.
2. **`block: body`** — зарезервированное имя, рендер без `##`. Альтернатива
   — отсутствие ключа как умолчание — отвергнута: блок объявляется всегда.

Ради этого списка снято: `bundle.yaml` (папка как имя), локальная область
видимости, `catalog: {visible, summary}` (→ метки и `summary`), производные
имена `prompt-<композит>`, ключи `composites:` / `apply:` в конфиге, поле
`except:` у override, тип `EXECUTABLE`.

## Сегодня → под `-re`, одной таблицей

| Конструкция      | Сегодня (`-r`)                                                | Под `-re`                                                                                  |
| ---------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| единицы          | role, trait, rule, skill                                      | composite, prompt, hook, operation, skill; override                                        |
| имя              | каталог типа + имя; `::` — вложенность внутри типа            | путь файла от корня; суффикс — сахар; `::` — единственный разделитель                      |
| что запускается  | только роль                                                   | любой композит и любое выражение                                                           |
| текст            | `injection:` (порядок по типу), `rule.md`, `priorities:`      | `prompt` с `block` и `heading`; порядок — порядок термов                                   |
| привязка скрипта | семь каналов                                                  | файл `hook` + payload по относительному пути; адресат — композит или override              |
| консент          | `apps.consent_gate`, строка на N точек, `consent: bool`       | `hook` `kind: consent`, один на точку; `operation`                                         |
| доопределение    | `customizations: {role: {add, remove}}`                       | `override` по меткам; переопределение — одноимённый файл выше                              |
| «всем ролям»     | нет                                                           | `apply: ".*"`, `apply: "^hitl$"`                                                           |
| рантайм          | `-r "role + t - s"`, база — роль, без повторов                | `-re "<expr>"`, любой терм, повторы и порядок значимы                                      |
| конфиги          | `ai-hats.yaml` + `customizations.yaml` + `library_paths.yaml` | `library.{paths,default}` + машинный `active` + `~/.ai-hats/config.yaml`; выбор, не состав |
| просмотр         | `config show-prompt --role`, `--dry-run`                      | `-re show-prompt`, `-re dry-run` — проекции плана [3]                                      |
| саб-агент        | `agent <role>`                                                | `agent -re "<expr>"`                                                                       |
| каталог          | каталог `roles/`                                              | метка `role` на композите                                                                  |

## Последствия

- Одна грамматика вместо семи способов сказать одно и то же; три
  правила формы вместо каталога на тип.
- Новый промпт — один файл и `+` в выражении; каталог с конфигом не нужен.
- Скилл теряет исполняемое молча и без переопределения имени: на скрипт
  ссылались каналы привязки, не потребители скилла; композиты, называвшие
  скилл с хуками, получают хуки явными термами от скрипта миграции.
- Конфиг не несёт состава: проектное доопределение трекается вместе с
  библиотекой; ревью версионируемого конфига из ADR-0035 [2] D3 становится
  ревью `libraries/`.
- Суффиксный резолв кусается на именах файлов: одноимённый лист в любом типе
  делает голое имя двусмысленным. Отказ называет кандидатов; цена — гигиена
  имён override'ов и хуков.
- Метки режима (`hitl` / `automate`) — зависимость от поставляемой
  библиотеки: без них пользовательский override выбирает по именам.
- Переопределение — целиком: одноимённый файл без `tags` роняет метки, и
  override'ы по ним перестают видеть композит. Руководство названо в **D3**.
- Мажорное ломающее обновление для потребителей: CLI (`customize`, `config set
  --role`, форма `-r`), конфиги, схема библиотеки — ADR-0033 [1], этап 3.
- Стадии CI, читающие `roles/ traits/ rules/`, переезжают вместе с формой —
  список в ADR-0033 [1].

## Не входит в решение

- Семантика выражения, порядок слоёв, свёртка, сборка промпта, join хуков,
  порядок override'ов — ADR-0035 [2].
- Форма плана, интерфейс харнеса, сравнение планов — ADR-0036 [3].
- Секции промпта, зависящие от харнесса, — следующий ADR; здесь только
  `block` и `heading` как свойства промпта.
- Постоянный дом для контента из **D7**.
- `except:` у override — поле приедет с потребителем; пока исключение — тег
  или поздний слой.

## References

**[1]** — [`ADR-0033`](0033-named-expressions-replace-roles-and-traits.md) — обзор плоской модели: слои, этапы, отношение к принятым ADR.

**[2]** — [`ADR-0035`](0035-processing-from-dsl-to-plan.md) — процессинг: правила от DSL к плану.

**[3]** — [`ADR-0036`](0036-materialization-plan-harness-interface.md) — план материализации: интерфейс харнеса и его получение из обоих DSL.

**[4]** — [`ADR-0021`](0021-surface-materialization.md) — цепочка библиотечных слоёв и таблица переопределений.
