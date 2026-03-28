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

```bash
# Установка
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Инициализация проекта
ai-hats init --provider gemini

# Применить роль
ai-hats set assistant

# Запустить обёрнутую сессию
ai-hats wrap gemini
ai-hats wrap claude
```

Или одной командой (bootstrap):

```bash
curl -sSL <url>/scripts/bootstrap.sh | sh -s -- --role assistant
```

## CLI

```
ai-hats init [--role <name>] [--provider gemini|claude]
ai-hats set <role> [--provider gemini|claude]
ai-hats status
ai-hats bump
ai-hats rollback
ai-hats clean
ai-hats whoami

ai-hats wrap gemini [--role <name>]
ai-hats wrap claude [--role <name>]

ai-hats run <role> [--ticket <ID>] [--model <name>]
ai-hats judge [--session <ID>] [--last N]
ai-hats retro [--session <ID>]
ai-hats audit [--session <ID>]

ai-hats task create [ID] <title> [-d <desc>] [-p high|medium|low]
ai-hats task transition <ID> <state>
ai-hats task log <ID> <message>
ai-hats task list [--state <state>]
ai-hats task show <ID>
ai-hats task sync

ai-hats self-update
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

Переключение между провайдерами: `ai-hats set <role> --provider claude`. Wrap автоматически пересобирает prompt при смене провайдера.

### Task State Machine

```
brainstorm → plan → execute → review → done
     ↑          ↓        ↑       ↓
     └── blocked ←────────┘   failed
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
libraries/
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

## Разработка

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
