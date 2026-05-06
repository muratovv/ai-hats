# ai-hats

Фреймворк для создания, сборки и управления AI-агентами через композитные роли.

## Концепция

ai-hats собирает роли из компонентов — **traits**, **rules**, **skills**, **hooks**, **MCP servers** — и инжектит их в system prompt выбранного провайдера (Gemini / Claude). Один набор ролей работает с любым провайдером.

```
roles/assistant ── trait-base + trait-agent + dev::python
                   ├── rules: git_workflow, tdd
                   ├── skills: backlog-manager, git-mastery
                   └── injection → GEMINI.md / CLAUDE.md
```

## Быстрый старт

### Подключение к новому проекту (bootstrap)

```bash
cd ~/dev/my-project

TMP=$(mktemp -d) && git clone --depth 1 git@github.com:muratovv/ai-hats.git "$TMP" && \
  bash "$TMP/scripts/bootstrap.sh" -r go-dev -p claude; rm -rf "$TMP"
```

Скрипт создаст `.venv`, установит ai-hats через pip, сгенерирует `ai-hats.yaml` и `CLAUDE.md`.

### После установки

```bash
source .venv/bin/activate
ai-hats                       # запустить сессию с текущими настройками
ai-hats --resume              # флаги передаются провайдеру (claude/gemini)
ai-hats config status         # проверить состояние
ai-hats config set -r <role>  # сменить роль
ai-hats config set -p gemini  # сменить провайдер
ai-hats self bump             # обновить prompt после изменений в библиотеке
```

### Альтернативные способы установки

<details>
<summary>Из локального клона ai-hats</summary>

```bash
cd ~/dev/my-project
bash ~/dev/ai-hats/scripts/bootstrap.sh -r go-dev -p claude
```
</details>

<details>
<summary>Ручная установка (если ai-hats уже в venv)</summary>

```bash
cd ~/dev/my-project
source .venv/bin/activate
ai-hats config set -r go-dev -p claude
```
</details>

### Разработка ai-hats

```bash
git clone git@github.com:muratovv/ai-hats.git && cd ai-hats
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

Доступные роли: `go-dev`, `go-dev-full`, `assistant`, `architect`, `sre`, `test-agent`.

`go-dev` — лёгкий профиль (core Go skills + testing-extended + ci, ~28 скиллов).
`go-dev-full` — все 11 `dev::go-*` traits сразу (database, grpc, cli, observability, performance, security, di, samber ecosystem, …, ~44 скилла). Используй для полидоменных Go-проектов; для узких задач подключай applied-traits через `customize`.

## CLI

Топ-уровневые группы: `agent`, `config`, `list`, `reflect`, `self`, `session`,
`task`, `wt`. Подробности по любой — `ai-hats <group> --help`.

```bash
# Сессия — ai-hats без subcommand запускает провайдер
ai-hats                                    # текущие настройки
ai-hats --resume                           # флаги передаются провайдеру
ai-hats -p claude -r architect             # override провайдера и роли
ai-hats "fix the bug"                      # промпт передаётся провайдеру
ai-hats --tag client=acme --tag project=X  # custom теги в metrics.json (см. ниже)

# Конфигурация (всё, что пишет в ai-hats.yaml)
ai-hats config set -r <role> -p <provider>     # настроить роль и/или провайдер
ai-hats config status                          # текущая роль, дерево, health
ai-hats config customize <role> --add-trait X  # добавить трейт к роли
ai-hats config customize <role> --remove-skill Y
ai-hats config customize <role> --show         # показать кастомизации
ai-hats config customize <role> --reset        # сбросить кастомизации
ai-hats config feedback show                   # текущая конфигурация feedback loop
ai-hats config feedback session-retro <policy> [--threshold turns=N,tool_calls=N] [--background/--no-background]

# Жизненный цикл инструмента в проекте
ai-hats self init -r <role> -p <provider>  # инициализировать ai-hats в текущем dir
ai-hats self bump                          # пересобрать prompt из библиотеки
ai-hats self clean                         # очистить .agent/
ai-hats self rollback                      # откатить к предыдущему состоянию
ai-hats self update                        # pip-переустановка из GitHub
ai-hats self migrate                       # миграция конфига к новой версии

# Sub-агенты — запуск роли в изолированном worktree
ai-hats agent <role> [--ticket <ID>] [--model <name>] [--task <desc>] [--tag k=v ...] [--json]

# Discovery — что доступно в композиции
ai-hats list roles | skills | rules | traits | providers | tokens

# Worktrees — изолированная работа
ai-hats wt create <branch>                                     # создать worktree на новой ветке
ai-hats wt list                                                # все git worktrees
ai-hats wt status                                              # tracked для текущего проекта
ai-hats wt exec -- <cmd>                                       # запустить команду в активном wt
ai-hats wt env                                                 # eval-friendly shell exports (WT, PYTHONPATH)
ai-hats wt merge [<branch>] [--squash] [--force]               # влить worktree обратно и удалить
ai-hats wt discard [<branch>] [--force]                        # выкинуть изменения worktree

# Наблюдаемость
ai-hats session list [--last N] [--min-turns N] [--productive] [--all] [--tag k=v ...] [--role <r>] [--since YYYY-MM-DD] [--json]
ai-hats session show <session_id>                              # детали сессии
ai-hats session audit [--session <ID>]                         # audit.md сессии
ai-hats session retro <session_id> [--last] [--timeout SEC] [--interactive]  # SessionRetroV1 (LLM)
ai-hats session retro-validate <path>                          # проверить retro по схеме

# Feedback loop — per-session vote + bulk-triage
ai-hats reflect session [--session <ID>] [--background]        # ручной запуск reflect-session судьи
ai-hats reflect all [--dry-run]                                # ручной триаж накопленных HYP/PROP
ai-hats reflect commit --accept PROP-X --reject PROP-Y ...     # bulk-flip статусов после триажа

# Задачи + гипотезы + предложения (всё под task — backlog artifacts)
ai-hats task create <title> [--id ID] [-d <desc>] [-p high|medium|low] \
                            [--parent-task ID] [--depends-on ID ...]
ai-hats task update <ID> [--title ...] [--description ...] [--priority ...] \
                         [--parent-task ID | --clear-parent] \
                         [--add-depends ID ...] [--remove-depends ID ...] \
                         [--add-tag T ...] [--remove-tag T ...]
ai-hats task transition <ID> <state>
ai-hats task log <ID> <message>
ai-hats task list [--state <state>] [--priority <p>] [--search <regex>] [--all]
ai-hats task show <ID>      # depends_on рендерится как «Blocked by:» со state-ами
ai-hats task sync
ai-hats task plan-sync <ID> [--from-file PATH]            # импорт .claude/plans/<NN>-*.md → plan.md
ai-hats task plan-extract <ID> [--auto] [--dry-run]       # из plan.md создать дочерние task cards

ai-hats task hyp list | show | append-verdict | migrate           # hypothesis backlog (HYP-NNN.yaml)
ai-hats task proposal list | show | create | vote | status        # proposal backlog (PROP-NNN.yaml)
```

### Как обновить ai-hats в проекте

`ai-hats self update` — единственный рекомендованный путь. Он делает
`pip install --force-reinstall --no-cache-dir ai-hats @ git+ssh://...`
в текущем интерпретаторе, показывает diff версий и сравнивает композицию до/после.

Не нужно вручную дёргать pip. Если venv проекта изолирован — запускай команду
его `ai-hats` (например, `~/dotfiles/.venv/bin/ai-hats self update`).

После `self update` прогони `ai-hats self bump` — он пересоберёт prompt
и managed-файлы (`.agent/*`, `.claude/skills/*`, `.gitignore` block) под
новую версию.

```bash
cd ~/my-project
ai-hats self update   # подтянуть свежий ai-hats из GitHub
ai-hats self bump     # пересобрать роль + .gitignore
```

## Session tags и queryable history

Кастомные `k=v` метаданные на сессиях — для оркестраторов (autosre, CI, batch),
cost attribution, pipeline tracking, A/B экспериментов. Теги попадают в
`metrics.json` под ключ `tags` и индексируются через `session list`.

```bash
# Запись — теги при запуске (повторяемый флаг, до 20 на сессию)
ai-hats agent sre-diagnoser --task "..." \
    --tag alert_fp=abc123 \
    --tag alertname=ImmichContainerDown \
    --tag client=home-lab

# То же для интерактивной сессии
ai-hats --tag client=acme --tag project=migration-v2

# Запрос — фильтры + machine-readable JSON для pipe в jq/parallel
ai-hats session list --tag alert_fp=abc123 --json | jq .
ai-hats session list --role sre-diagnoser --since 2026-04-20 --json
ai-hats session list --tag client=acme --tag project=X --all --json
```

**Валидация (строгая, raise при нарушении):**
- Ключ: `^[a-zA-Z_][a-zA-Z0-9_.\-]*$`, max 64 chars.
- Значение: max 256 chars, непустое.
- Max 20 тегов на сессию.
- Reserved keys (shadow запрещён): `role`, `provider`, `exit_code`, `model`,
  `timed_out`, `error`, `isolation_mode`, `turns`, `tokens`, `models`,
  `tool_calls`, `session_id`, `session_dir`, `started_at`.

**JSON output** — `--json` выдаёт plain список словарей. Форма каждого
элемента — все поля `metrics.json` плюс computed `session_id`, `session_dir`,
`started_at` (ISO-8601). Consumers выбирают нужное через `jq`.

**Рецепт dedup в оркестраторе** (заменяет собой идею `--idempotency-key`):

```bash
# Перед запуском нового диагноза — проверить, есть ли уже сессия с этим fp
fp="$1"
existing=$(ai-hats session list --tag alert_fp="$fp" --since "$(date -u +%Y-%m-%d)" --all --json \
            | jq -r '.[] | select(.exit_code == 0) | .session_id' | head -n1)

if [ -n "$existing" ]; then
    echo "Already diagnosed in session $existing — skipping"
    exit 0
fi
ai-hats agent sre-diagnoser --tag alert_fp="$fp" --task "..."
```

Атомарность check-and-spawn (race между двумя параллельными вебхуками) — на
стороне оркестратора: filelock/redis/что удобнее.

## Machine-readable run + exit codes

Для fan-out через `parallel`/`xargs`/CI:

```bash
ai-hats agent <role> --task "..." --json
# → stdout: {"session_id":"...","exit_code":0,"role":"...","duration_s":12.3,"tags":{...},...}
```

Форма совпадает с элементом `session list --json` — один и тот же парсинг на
стороне оркестратора. `--json` режим **полностью подавляет** rich-summary в
stdout; человекочитаемый режим (без `--json`) оставлен как был.

**Exit codes** (стабильный контракт, пробрасываются из sub-agent):

| Код | Значение |
|---|---|
| 0 | успех (sub-agent завершился 0) |
| 1 | agent/runtime error (subprocess exit 1, generic exception в runtime) |
| 2 | CLI usage error (неверные флаги — default click) |
| 124 | timeout (sub-agent превысил wall-clock limit) — convention GNU coreutils |
| другой non-zero | форвардится от провайдера (claude/gemini exit code) |

Пример fan-out:

```bash
# N параллельных вызовов, собрать все результаты, отфильтровать успешные
cat tasks.jsonl | jq -r '.task' | parallel -j 3 \
    'ai-hats agent diagnoser --task {} --json' \
  | jq -s 'map(select(.exit_code == 0))'
```

Если надо узнать код завершения одной сессии, не парся stdout — хватит `$?`:

```bash
ai-hats agent diagnoser --task "..." --json > result.json
echo "exit=$?"   # совпадает с .exit_code в result.json
```

## Архитектура

### Компонентная модель

| Компонент | Описание | Формат |
|-----------|----------|--------|
| **Rules** | Поведенческие директивы | `rule.md` + `metadata.yaml` |
| **Skills** | Навыки с реализацией | `SKILL.md` + `metadata.yaml` + `scripts/` + `references/` |
| **Traits** | Составные компоненты | `config.yaml` (composition + injection) |
| **Roles** | Корневые конфигурации | `config.yaml` (traits + priorities + injection) |

### Кастомизация ролей

Можно добавлять/убирать трейты, правила и скиллы из библиотечной роли без модификации исходного конфига. Кастомизации хранятся в `ai-hats.yaml` и переживают `ai-hats self update` и `ai-hats self bump`.

> Подборка типовых сценариев с готовыми примерами `ai-hats.yaml` — см. [docs/how-to.md](docs/how-to.md).

```bash
# Добавить трейт к роли sre
ai-hats config customize sre --add-trait dev::python

# Убрать ненужный скилл
ai-hats config customize sre --remove-skill network-documentation

# Добавить инжекцию
ai-hats config customize sre --injection-append "Always use k9s for K8s."

# Посмотреть кастомизации
ai-hats config customize sre --show

# Применить
ai-hats self bump
```

Формат в `ai-hats.yaml`:

```yaml
customizations:
  sre:
    add:
      traits: [dev::python]
      skills: [my-debug-tool]
    remove:
      skills: [network-documentation]
    injection_append: |
      Always use k9s for K8s.
```

Кастомизации применяются при каждом `config set`, `self bump` и `--role` override. Если `remove` ссылается на компонент, которого нет в базовой роли — выводится warning, ошибки не будет.

### Композиция

- Non-commutative — порядок определяет приоритет (поздний > ранний)
- Плоская — трейты не включают другие трейты (flat model)
- Дедупликация — одинаковые injection/rules не повторяются
- Пространства имён — `dev::python` → `dev/python` на FS
- Приоритеты — только из корневой роли

### Провайдеры

- **Gemini** — `GEMINI.md` + `GEMINI_CLI_PROJECT_RULES_PATH`
- **Claude** — `CLAUDE.md`

Переключение между провайдерами: `ai-hats config set -p claude`. При запуске сессии prompt автоматически пересобирается если провайдер изменился.

### Task State Machine

```
brainstorm → plan → execute → document → review → done
     ↑          ↓        ↓         ↓        ↓
     └── blocked ←────────┴─────────┘     failed
```

При переходе в `plan` — создаётся `plan.md` scaffold. Work log с session tracking. File-lock защита от race conditions.

#### Поиск задач

`--search` принимает regex (case-insensitive) и ищет по id, title, description, tags, parent_task, depends_on:

```bash
ai-hats task list --search epic              # все эпики (по тегу или title)
ai-hats task list --search HATS-092          # эпик + дети (parent_task) + блокируемые им (depends_on)
ai-hats task list --search judge             # всё связанное с judge
ai-hats task list --search "HATS-09[2-3]"   # regex: два эпика сразу
ai-hats task list --search worktree --all    # включая done/failed
```

### Feedback loop

> Пошаговый гайд по настройке и использованию (политики, reflect-session, reflect-all, гипотезы) — см. [docs/how-to-feedback-loop.md](docs/how-to-feedback-loop.md).
>
> Модель для feedback-loop пинится отдельно от интерактивной сессии — `feedback.session_retro.model` (LLM-builder для SessionRetroV1) и `feedback.session_retro.reflect_model` (reflect-session sub-agent). Если поля не заданы — используется дефолт CLI провайдера.

ai-hats замыкает feedback из реальных сессий в четыре слоя:

```
.gitlog/session_<id>/                                  layer 0: raw телеметрия
  ├── audit.md
  ├── metrics.json
  └── transcript.txt

.agent/retrospectives/sessions/<id>.md                 layer 1: SessionRetroV1
                                                          ↓ LLM-builder, факты + narrative

.agent/retrospectives/reflect-session/<id>.md          layer 2: ReflectSessionV1
                                                          ↑ per-session sub-agent судит
                                                            активные HYP, голосует за PROP
.agent/hypotheses/HYP-NNN.yaml                            ← side effect: append-verdict
.agent/backlog/proposals/PROP-NNN.yaml                    ← side effect: create / vote

manual triage:                                          layer 3: bulk-triage
  ai-hats reflect all     → handoff с накопленным
  ai-hats reflect commit  → bulk-flip PROP статусов
```

**Layer 1 — Session retro.** Снимок одной сессии: метрики, изменённые файлы, коммиты, закрытые задачи + LLM-narrative. Это всегда LLM-режим (~30+ секунд через провайдер).

```bash
ai-hats session retro 20260406-050419-1   # явный session_id
ai-hats session retro --last              # для последней сессии
```

**Auto session-retro.** После завершения сессии хук `session_end_auto-retro.sh` автоматически решает, нужно ли генерировать retro и сразу спавнить reflect-session судью. Поведение — через `ai-hats.yaml` → `feedback.session_retro`:

```bash
# Настройка через CLI
ai-hats config feedback session-retro smart --threshold turns=3,tool_calls=10 --background
ai-hats config feedback session-retro off          # отключить
ai-hats config feedback session-retro hint         # только подсказка
ai-hats config feedback show                       # текущие настройки
```

| Параметр | Значения | Default | Описание |
|----------|----------|---------|----------|
| `policy` | `off`, `always`, `smart`, `hint` | `smart` | Когда генерировать retro |
| `smart_threshold.min_turns` | int | 5 | Порог по числу ходов |
| `smart_threshold.min_tool_calls` | int | 10 | Порог по числу tool calls |
| `background` | bool | `true` | Запускать в фоне (не блокирует терминал) |
| `model` | str \| null | null | LLM для SessionRetroV1 (null → провайдер default) |
| `reflect_model` | str \| null | null | LLM для reflect-session sub-agent |

Политики:
- **off** — никогда не генерировать автоматически
- **always** — генерировать после каждой сессии
- **smart** — генерировать если `turns >= min_turns` ИЛИ `tool_calls >= min_tool_calls`
- **hint** — как smart, но вместо генерации печатает подсказку с командой

**Layer 2 — Reflect-session.** Сразу после SessionRetroV1 спавнится sub-agent роли `reflect-session`: читает все active HYP и open PROP, для каждой active HYP выносит вердикт (`confirmed` / `refuted` / `inconclusive` / `n/a`), цитирует evidence из audit/metrics, голосует за похожие PROP или создаёт новые. Side effects идут только через CLI:

```bash
# Что reflect-session делает за тебя (этот sub-agent работает автоматически):
ai-hats task hyp append-verdict --hyp HYP-008 --session $SID \
    --verdict confirmed --evidence "metrics.json:bash_anti_count=0" \
    --recommendation keep
ai-hats task proposal vote --prop PROP-042 --session $SID --reasoning "..."
ai-hats task proposal create --category rule --target dev_rule_X --title ... \
    --description ... --rationale ... --session $SID
```

Ручной запуск (для отладки или если auto-режим выключен):

```bash
ai-hats reflect session --session <id>              # foreground
ai-hats reflect session --session <id> --background # как в авто
```

**Layer 3 — Manual triage.** Когда HYP/PROP накопилось — пройтись по бэклогу руками:

```bash
ai-hats reflect all                                # pre-flight handoff + интерактив
ai-hats reflect all --dry-run                      # только собрать handoff

# После обсуждения с агентом — bulk-flip статусов:
ai-hats reflect commit \
    --accept PROP-3 --accept PROP-7 \
    --reject PROP-12 --defer PROP-15 --duplicate PROP-9
```

Внутри интерактивной сессии полезно:

```bash
ai-hats task hyp show HYP-NNN
ai-hats task proposal show PROP-NNN
ai-hats task proposal status PROP-NNN <accepted|rejected|deferred|duplicate>
ai-hats task create ...                            # завести задачу из принятого PROP
```

**Просмотр сессий** — отбор интересного перед reflect-all или ручным retro:

```bash
ai-hats session list --min-turns 10              # только сессии с ≥10 ходами
ai-hats session list --productive                # только продуктивные (turns>0, tools>0)
ai-hats session show 20260408-192417-1           # детальные метрики
```

**Hypothesis workflow.** Замкнутый цикл улучшений (см. `hypothesis-workflow` skill):

1. Заметил повторяющийся паттерн в reflect-session ретро (≥3 сессии).
2. Завести гипотезу как `.agent/hypotheses/HYP-NNN-<slug>.yaml` (см. `_schema.yaml` рядом — поля `statement`, `baseline`, `target`, `window`, `success_criterion`).
3. Применить изменение (rule/skill/code) на task-ветке.
4. Каждая сессия → reflect-session добавляет verdict в `validation_log` через `ai-hats task hyp append-verdict --hyp HYP-NNN --session ... --verdict ... --evidence ...`.
5. По истечении window — финальный `append-verdict` с `--recommendation close_confirmed` или `close_refuted` закрывает гипотезу.

**Валидация артефактов.** Retro-файл можно проверить против схемы:

```bash
ai-hats session retro-validate .agent/retrospectives/sessions/20260406-050419-1.md
```

## Структура проекта

```
.agent/                                # Активные компоненты (генерируется)
  rules/                               # Физические копии правил из роли
  skills/                              # Физические копии навыков
  hooks/                               # Hook-скрипты
  backlog/
    tasks/<ID>/                        # Task card + plan.md + retrospective.md
    proposals/PROP-NNN.yaml            # Improvement proposals (см. task proposal)
  STATE.md                             # Табличный индекс + текущее состояние задач
  hypotheses/HYP-NNN.yaml              # Hypothesis backlog (см. task hyp)
  retrospectives/
    sessions/<id>.md                   # SessionRetroV1 (LLM-narrative + facts)
    reflect-session/<id>.md            # ReflectSessionV1 (per-session sub-agent verdicts)
.gitlog/
  session_<ID>/                        # trace.log, audit.md, metrics.json, transcript.txt
ai-hats.yaml                           # Конфиг проекта + роль + feedback
GEMINI.md / CLAUDE.md                  # System prompt
```

## Библиотека

```
src/ai_hats/libraries/
  rules/          global_rule_*, dev_rule_*, env_rule_*
  skills/         62 скилла (29 нативных + 33 vendored golang-* из samber/cc-skills-golang)
  traits/         trait-base, trait-agent, trait-se-mindset, skill-engineer, dev::go-*, dev::python, dev::shell, env::*
  roles/          assistant, test-agent, architect, sre, reflect-session, go-dev, go-dev-full
```

Vendored golang-* skills хранят upstream commit SHA, LICENSE и atribution в `metadata.yaml.upstream.*` — фундамент для будущей плагинной системы (см. HATS-050).

### Шаблон скилла

Каждый скилл следует каноническому фор��ату (см. `skill-template`):

```markdown
# Skill Name
One-line purpose.

## When to Use         ← триггеры активации
## <Main Section>      ← Procedure | Checklist | Workflow | Conventions
## Completion          ← критерии завершения
## Anti-Patterns       ← типичные ошибки
```

Паттерны: `protocol`, `checklist`, `orchestrator`, `reference`, `template`.
Метаданные: `metadata.yaml` (name, description, author, tags, pattern).

Скилл может опционально декларировать **git-хуки**, которые автоматически
устанавливаются в `.githooks/` при сборке роли (HATS-088):

```yaml
# <skill>/metadata.yaml
git_hooks:
  pre-commit:
    - git_hooks/check.sh   # путь относительно директории скилла
```

Сборщик копирует скрипты в `.githooks/<event>.d/<skill>-<basename>`,
генерирует диспетчер `.githooks/<event>` и выставляет
`core.hooksPath = .githooks` идемпотентно. Если у пользователя уже
настроен `core.hooksPath` или существует свой dispatcher без нашего
маркера — они не трогаются, выводится предупреждение с инструкцией.

### Пример config.yaml роли

```yaml
name: assistant
priorities:
  - Reliability
  - Cleanliness
  - Velocity
composition:
  traits:
    - trait-base
    - trait-agent
    - dev::python
  rules:
    - dev_rule_git_workflow
  skills:
    - backlog-manager
    - git-mastery
injection: |
  # ROLE: PRIMARY AUTOMATION ASSISTANT
  ...
```
