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

Доступные роли: `go-dev`, `assistant`, `architect`, `sre`, `judge`, `test-agent`.

## CLI

```bash
# Сессия — ai-hats без subcommand запускает провайдер
ai-hats                                    # текущие настройки
ai-hats --resume                           # флаги передаются провайдеру
ai-hats -p claude -r architect             # override провайдера и роли
ai-hats "fix the bug"                      # промпт передаётся провайдеру

# Конфигурация
ai-hats set -r <role> -p <provider>        # настроить роль и/или провайдер
ai-hats status                             # текущая роль, дерево, health
ai-hats bump                               # пересобрать prompt
ai-hats rollback                           # откатить к предыдущему состоянию
ai-hats clean                              # очистить .agent/
ai-hats whoami                             # диагностика

# Суб-агенты
ai-hats run <role> [--ticket <ID>] [--model <name>] [--task <desc>]

# Наблюдаемость
ai-hats judge [--session <ID>] [--last N]
ai-hats retro [--session <ID>]
ai-hats audit [--session <ID>]

# Задачи
ai-hats task create [ID] <title> [-d <desc>] [-p high|medium|low]
ai-hats task transition <ID> <state>
ai-hats task log <ID> <message>
ai-hats task list [--state <state>]
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

## Структура проекта

```
.agent/                     # Активные компоненты (генерируется)
  rules/                    # Физические копии правил из роли
  skills/                   # Физические копии навыков
  hooks/                    # Hook-скрипты
  backlog/
    tasks/<ID>/             # Task card + plan.md + retro.md
  backlog.md                # Табличный индекс
  STATE.md                  # Текущее состояние задач
.gitlog/
  session_<ID>/             # trace.log, audit.md, metrics.json
ai-hats.yaml                # Конфиг проекта
profile.json                # Активная роль
GEMINI.md / CLAUDE.md       # System prompt
```

## Библиотека

```
src/ai_hats/libraries/
  rules/          global_rule_*, dev_rule_*, env_rule_*
  skills/         24 скилла (backlog-manager, git-mastery, skill-template, ...)
  traits/         trait-base, trait-agent, trait-se-mindset, skill-engineer, dev::*
  roles/          assistant, test-agent, architect, sre, judge, go-dev
```

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

