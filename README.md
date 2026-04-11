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

# Из локального клона ai-hats
bash ~/dev/ai-hats/scripts/bootstrap.sh --role go-dev --provider claude

# Или склонировать и установить за один шаг
git clone git@github.com:muratovv/ai-hats.git /tmp/ai-hats && \
  bash /tmp/ai-hats/scripts/bootstrap.sh --role go-dev --provider claude
```

Скрипт создаст `.venv`, установит ai-hats через pip, сгенерирует `ai-hats.yaml` и `CLAUDE.md`.

### Ручная установка (если ai-hats уже установлен)

```bash
cd ~/dev/my-project
source .venv/bin/activate
ai-hats set -r go-dev -p claude
```

### После установки

```bash
source .venv/bin/activate
ai-hats                     # запустить сессию с текущими настройками
ai-hats --resume            # флаги передаются провайдеру (claude/gemini)
ai-hats status              # проверить состояние
ai-hats set -r <role>       # сменить роль
ai-hats set -p gemini       # сменить провайдер
ai-hats bump                # обновить prompt после изменений в библиотеке
```

### Разработка ai-hats

```bash
git clone git@github.com:muratovv/ai-hats.git && cd ai-hats
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

Доступные роли: `go-dev`, `go-dev-full`, `assistant`, `architect`, `sre`, `judge`, `test-agent`.

`go-dev` — лёгкий профиль (core Go skills + testing-extended + ci, ~28 скиллов).
`go-dev-full` — все 11 `dev::go-*` traits сразу (database, grpc, cli, observability, performance, security, di, samber ecosystem, …, ~44 скилла). Используй для полидоменных Go-проектов; для узких задач подключай applied-traits через `customize`.

## CLI

```bash
# Сессия — ai-hats без subcommand запускает провайдер
ai-hats                                    # текущие настройки
ai-hats --resume                           # флаги передаются провайдеру
ai-hats -p claude -r architect             # override провайдера и роли
ai-hats "fix the bug"                      # промпт передаётся провайдеру

# Конфигурация
ai-hats set -r <role> -p <provider>        # настроить роль и/или провайдер
ai-hats config feedback show               # текущая конфигурация feedback loop
ai-hats config feedback session-retro <policy> [--threshold turns=N,tool_calls=N] [--mode ...] [--background/--no-background]
ai-hats config feedback judge <off|manual>  # политика запуска judge
ai-hats customize <role> --add-trait X     # добавить трейт к роли
ai-hats customize <role> --remove-skill Y  # убрать скилл из роли
ai-hats customize <role> --show            # показать кастомизации
ai-hats customize <role> --reset           # сбросить кастомизации
ai-hats status                             # текущая роль, дерево, health
ai-hats bump                               # пересобрать prompt
ai-hats rollback                           # откатить к предыдущему состоянию
ai-hats clean                              # очистить .agent/
ai-hats whoami                             # диагностика

# Суб-агенты
ai-hats run <role> [--ticket <ID>] [--model <name>] [--task <desc>]

# Наблюдаемость и feedback loop
ai-hats session list [--last N] [--min-turns N] [--productive] [--all] # список сессий с метриками
ai-hats session show <session_id>                                      # детали конкретной сессии
ai-hats audit [--session <ID>]                                         # показать audit.md сессии
ai-hats retro <session_id> [--last] [--mode programmatic|llm]         # session-retro snapshot
ai-hats bundle create --sessions s1,s2 [--notes "..."]                # сгруппировать сессии для анализа
ai-hats bundle create --last N | --since YYYY-MM-DD
ai-hats bundle list | show <bundle_id>
ai-hats judge --bundle <id> [--focus "..."]                           # forensic analysis от judge-агента
ai-hats judge --last N [--focus "..."] [--interactive]                # auto-bundle + judge + обсуждение
ai-hats judge --retro <path>                                          # обсудить существующий retro
ai-hats judge-aggregate [--since ...] [--min-severity ...]            # паттерны из judge retros
ai-hats retro-validate <path>                                         # проверить файл по HATS-051 schema
ai-hats retro-migrate <path> [--dry-run]                              # миграция к latest схеме

# Задачи
ai-hats task create <title> [--id ID] [-d <desc>] [-p high|medium|low]
ai-hats task transition <ID> <state>
ai-hats task log <ID> <message>
ai-hats task list [--state <state>] [--priority <p>] [--search <regex>] [--all]
ai-hats task show <ID>
ai-hats task sync

# Обслуживание
ai-hats update
ai-hats migrate
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

Можно добавлять/убирать трейты, правила и скиллы из библиотечной роли без модификации исходного конфига. Кастомизации хранятся в `ai-hats.yaml` и переживают `ai-hats update` и `ai-hats bump`.

```bash
# Добавить трейт к роли sre
ai-hats customize sre --add-trait dev::python

# Убрать ненужный скилл
ai-hats customize sre --remove-skill network-documentation

# Добавить инжекцию
ai-hats customize sre --injection-append "Always use k9s for K8s."

# Посмотреть кастомизации
ai-hats customize sre --show

# Применить
ai-hats bump
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

Кастомизации применяются при каждом `set`, `bump` и `--role` override. Если `remove` ссылается на компонент, которого нет в базовой роли — выводится warning, ошибки не будет.

### Композиция

- Non-commutative — порядок определяет приоритет (поздний > ранний)
- Плоская — трейты не включают другие трейты (flat model)
- Дедупликация — одинаковые injection/rules не повторяются
- Пространства имён — `dev::python` → `dev/python` на FS
- Приоритеты — только из корневой роли

### Провайдеры

- **Gemini** — `GEMINI.md` + `GEMINI_CLI_PROJECT_RULES_PATH`
- **Claude** — `CLAUDE.md`

Переключение между провайдерами: `ai-hats set -p claude`. При запуске сессии prompt автоматически пересобирается если провайдер изменился.

### Task State Machine

```
brainstorm → plan → execute → document → review → done
     ↑          ↓        ↓         ↓        ↓
     └── blocked ←────────┴─────────┘     failed
```

При переходе в `plan` — создаётся `plan.md` scaffold. Work log с session tracking. File-lock защита от race conditions.

#### Поиск задач

`--search` принимает regex (case-insensitive) и ищет по id, title, description, tags, parent_task:

```bash
ai-hats task list --search epic              # все эпики (по тегу или title)
ai-hats task list --search HATS-092          # эпик + все его дети (по parent_task)
ai-hats task list --search judge             # всё связанное с judge
ai-hats task list --search "HATS-09[2-3]"   # regex: два эпика сразу
ai-hats task list --search worktree --all    # включая done/failed
```

### Feedback loop

ai-hats строит feedback из реальных сессий через три слоя артефактов (схемы зафиксированы в HATS-051, runtime — в HATS-001):

```
.gitlog/session_<id>/                       layer 0: raw телеметрия
  ├── audit.md                              после ⟶
  ├── metrics.json
  └── transcript.txt

.agent/retrospectives/sessions/<mode>/<id>.md   layer 1: SessionRetroV1 (factual snapshot)
  ├── programmatic/                             ← быстрый, из парсера, для хука
  └── llm/                                      ← narrative summary через LLM, для глубокого ревью

.agent/retrospectives/bundles/BUNDLE-...yaml    layer 2: bundle (lens-agnostic pointer)
.agent/retrospectives/judge/<date>-judge-NNN.md layer 3: JudgeRetroV1 (analytical findings)
```

**Layer 1 — Session retro.** Снимок одной сессии: метрики, изменённые файлы, коммиты, закрытые задачи. Два режима:
- `programmatic` (default) — быстро, без LLM, для авто-генерации хуком
- `llm` — narrative summary + observations через провайдер; занимает 30+ секунд

```bash
ai-hats retro 20260406-050419-1                  # programmatic, мгновенно
ai-hats retro 20260406-050419-1 --mode llm       # narrative от LLM
ai-hats retro --last                             # для последней сессии
```

Каждый mode пишет в свою подпапку — два режима не затирают друг друга.

**Auto session-retro.** После завершения сессии хук `session_end_auto-retro.sh` автоматически решает, нужно ли генерировать retro. Поведение настраивается через `ai-hats.yaml` → `feedback.session_retro`:

```bash
# Настройка через CLI
ai-hats config feedback session-retro smart --threshold turns=3 --mode llm --background
ai-hats config feedback session-retro off          # отключить
ai-hats config feedback session-retro hint         # только подсказка
ai-hats config feedback show                       # текущие настройки
```

| Параметр | Значения | Default | Описание |
|----------|----------|---------|----------|
| `policy` | `off`, `always`, `smart`, `hint` | `smart` | Когда генерировать retro |
| `smart_threshold.min_turns` | int | 5 | Порог по числу ходов |
| `smart_threshold.min_tool_calls` | int | 10 | Порог по числу tool calls |
| `mode` | `programmatic`, `hybrid`, `llm` | `programmatic` | Режим генерации |
| `background` | bool | `true` | Запускать в фоне (не блокирует терминал) |

Политики:
- **off** — никогда не генерировать автоматически
- **always** — генерировать после каждой сессии
- **smart** — генерировать если `turns >= min_turns` ИЛИ `tool_calls >= min_tool_calls`
- **hint** — как smart, но вместо генерации печатает подсказку с командой

**Layer 2 — Bundle.** Группа сессий для совместного судейского анализа. Bundles **lens-agnostic**: один и тот же набор сессий можно судить много раз с разными `--focus` линзами. Идемпотентны по `sorted(session_ids)` — повторный create с теми же сессиями вернёт тот же bundle.

```bash
ai-hats bundle create --sessions 20260406-050419-1,20260408-111835-1 --notes "training run"
ai-hats bundle create --last 5
ai-hats bundle create --since 2026-04-01
ai-hats bundle list
ai-hats bundle show BUNDLE-2026-04-08-001
```

**Layer 3 — Judge retro.** Forensic анализ bundle через спавн `judge` роли как sub-agent. Judge печатает результат в stdout между `BEGIN_JUDGE_RETRO`/`END_JUDGE_RETRO` маркерами; родительский CLI извлекает, валидирует через HATS-051 loader, делает один retry с correction prompt при ошибке схемы, сохраняет на диск.

```bash
# Существующий bundle с дефолтным анализом
ai-hats judge --bundle BUNDLE-2026-04-08-001

# Тот же bundle с разной линзой → разные findings
ai-hats judge --bundle BUNDLE-2026-04-08-001 --focus "tool-call efficiency and retry loops"
ai-hats judge --bundle BUNDLE-2026-04-08-001 --focus "git workflow — commit granularity"

# Auto-bundle из последних N сессий (создаёт bundle и сразу судит)
ai-hats judge --last 3 --focus "decision-making patterns"

# Auto-bundle из конкретных сессий
ai-hats judge --sessions s1,s2,s3 --focus "..."
```

Judge sub-session запускается в worktree (`discard` mode), может занять минуты — CLI показывает spinner. Перед запуском показывает какие сессии и фокус будет анализировать. Timeout по умолчанию 600s.

Output JudgeRetroV1 содержит findings (с обязательным evidence + session_id), patterns_to_keep, опциональный meta_critique. Каждая finding классифицирована по category/severity, может содержать proposed_fix с expected_impact для longitudinal validation.

**Interactive mode.** `--interactive` / `-i` после сохранения judge retro открывает полноценную ai-hats сессию с ролью `judge`. Агент видит findings, имеет доступ к бэклогу, может создавать задачи-гипотезы. Для обсуждения существующего retro без пересоздания — `--retro <path>`.

```bash
# Полный цикл: judge + обсуждение
ai-hats judge --last 5 --focus "tool-call efficiency" --interactive

# Обсудить существующий retro
ai-hats judge --retro .agent/retrospectives/judge/2026-04-10-judge-001.md
```

**Layer 4 — Aggregation.** Кластеризация findings из нескольких judge retros для выявления повторяющихся паттернов. Группирует по (category, target), fuzzy-matching root_cause, вычисляет частоту появления.

```bash
ai-hats judge-aggregate                              # все judge retros
ai-hats judge-aggregate --since 2026-04-01           # только свежие
ai-hats judge-aggregate --min-severity medium         # только medium+
```

Output: `.agent/retrospectives/aggregated/AGG-YYYY-MM-DD-NNN.md` — AggregationV1 schema с кластерами. Каждый кластер содержит frequency, rate (% retros), source findings, proposed fix.

**Просмотр сессий.** Для выбора интересных сессий перед созданием бандла:

```bash
ai-hats session list --min-turns 10              # только сессии с ≥10 ходами
ai-hats session list --productive                # только продуктивные (turns>0, tools>0)
ai-hats session show 20260408-192417-1           # детальные метрики
```

**Hypothesis workflow.** Замкнутый цикл улучшений через задачи-гипотезы (см. `hypothesis-workflow` skill):

1. `ai-hats judge-aggregate` — найти повторяющиеся паттерны
2. `ai-hats judge --last N --interactive` — обсудить findings с judge
3. `ai-hats task create "hypothesis: ..." --tag hypothesis` — зафиксировать гипотезу
4. Реализовать изменение, накопить N новых сессий
5. `ai-hats judge-aggregate --since <date>` — проверить, снизилась ли частота
6. Закрыть задачу-гипотезу с результатом

**Валидация артефактов.** Любой retro-файл (session / bundle / judge / aggregation) можно проверить против схемы:

```bash
ai-hats retro-validate .agent/retrospectives/judge/2026-04-08-judge-001.md
ai-hats retro-migrate <path> [--dry-run]
```

## Структура проекта

```
.agent/                                # Активные компоненты (генерируется)
  rules/                               # Физические копии правил из роли
  skills/                              # Физические копии навыков
  hooks/                               # Hook-скрипты
  backlog/
    tasks/<ID>/                        # Task card + plan.md + retro.md
  backlog.md                           # Табличный индекс
  STATE.md                             # Текущее состояние задач
  retrospectives/                      # Feedback loop (HATS-001 / HATS-051 / HATS-052)
    sessions/
      programmatic/<id>.md             # SessionRetroV1, factual snapshot
      llm/<id>.md                      # SessionRetroV1, narrative summary
    bundles/BUNDLE-YYYY-MM-DD-NNN.yaml # BundleV1, lens-agnostic pointer
    judge/YYYY-MM-DD-judge-NNN.md      # JudgeRetroV1, forensic analysis
    aggregated/AGG-YYYY-MM-DD-NNN.md   # AggregationV1, cross-session patterns
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
  roles/          assistant, test-agent, architect, sre, judge, go-dev, go-dev-full
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

