# ai-hats

Фреймворк для создания, сборки и управления AI-агентами через композитные роли.

## Концепция

ai-hats собирает роли из компонентов — **traits**, **rules**, **skills**, **hooks** — и инжектит их в system prompt выбранного провайдера (Gemini / Claude). Один набор ролей работает с любым провайдером.

```
roles/assistant ── trait-base + trait-agent + dev::python
                   ├── rules: git_workflow, tdd
                   ├── skills: backlog-manager, git-mastery
                   └── injection → GEMINI.md / CLAUDE.md
```

## Быстрый старт

Архитектура (HATS-333): bash launcher в `~/.local/bin/ai-hats` (one-time на хост) → per-project venv в `<ai_hats_dir>/.venv/`. **Одна команда `ai-hats self update`** делает install + heal + update.

### 1. Установить launcher (один раз на хост)

```bash
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash
```

Ставит ~30-строчный bash launcher в `~/.local/bin/ai-hats`. Если `~/.local/bin/` не в `$PATH` — installer подскажет добавить.

> ⚠️ Пока репо приватный, anonymous `curl` получит HTML-страницу 404 — используй «Альтернатива: bash bootstrap из клона» ниже либо склонируй репо и запусти `bash scripts/install-launcher.sh` локально.

### 2. Подключить к проекту

```bash
cd ~/dev/my-project
ai-hats self update                       # создаёт venv в .agent/ai-hats/.venv + installs ai-hats
ai-hats self init -r go-dev -p claude          # генерирует ai-hats.yaml + CLAUDE.md
```

### 3. Использование

```bash
ai-hats                       # запустить сессию с текущими настройками
ai-hats --resume              # флаги передаются провайдеру (claude/gemini)
ai-hats config status         # проверить состояние
ai-hats config set -r <role>  # сменить роль
ai-hats config set -p gemini  # сменить провайдер
ai-hats self bump             # обновить prompt после изменений в библиотеке
ai-hats self update           # обновить ai-hats + auto-bump
```

`ai-hats self update` self-healing: если venv сломан после системного python upgrade — пересоздаётся автоматически (только default; override venv user-owned).

### Альтернатива: bash bootstrap из клона

```bash
TMP=$(mktemp -d) && git clone --depth 1 git@github.com:muratovv/ai-hats.git "$TMP" && \
  bash "$TMP/scripts/bootstrap.sh" -r go-dev -p claude; rm -rf "$TMP"
```

Bootstrap.sh = installer launcher → `ai-hats self update` → `ai-hats self init` в одной команде. Полезен для CI / pre-PR setup.

### Override venv (advanced)

Если хочешь поставить ai-hats в свой существующий venv (например, проектный) — добавь в `ai-hats.yaml`:

```yaml
venv_path: .venv      # relative от project root
# или
venv_path: /opt/shared/ai-hats-venv   # absolute (CI shared cache, system venv)
```

Launcher читает это поле через grep и использует указанный путь. Override-venv user-owned: ai-hats не пересоздаёт его при heal — сам делай `python -m venv <path>` и `<path>/bin/pip install ai-hats`. См. `docs/how-to.md#configurable-venv_path`.

### Recovery после миграции с pipx

Существующий проект на global `pipx install ai-hats` → см. `docs/migration-333.md`.

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

> **Полный справочник команд с описаниями и опциями — `ai-hats --tree`**
> (работает также как `ai-hats --help --tree`). Дерево рендерится из живого
> click-графа, поэтому всегда соответствует установленной версии.
>
> Поддеревья: `ai-hats --tree <group>` (например, `ai-hats --tree wt`)
> или вглубь: `ai-hats --tree task hyp`.

Восемь top-level групп:

| Группа     | Что делает                                                              |
| ---------- | ----------------------------------------------------------------------- |
| `agent`    | Запуск роли как sub-агента в изолированном worktree                     |
| `config`   | Чтение/правка `ai-hats.yaml` (provider, role, customizations, feedback) |
| `list`     | Discovery: roles / skills / rules / traits / providers / tokens         |
| `reflect`  | Feedback loop — per-session vote и bulk-triage HYP/PROP                 |
| `self`     | Жизненный цикл инструмента: init / bump / update / clean / rollback |
| `session`  | Наблюдаемость: list / show / audit / retro по сессиям                   |
| `task`     | Backlog: task / hyp / proposal cards со state-машиной                   |
| `wt`       | git worktrees: create / merge / discard / exec / env                    |

Quickstart:

```bash
# Интерактивная сессия (без subcommand → провайдерский CLI с инжектом роли)
ai-hats                                    # текущие настройки
ai-hats -p claude -r architect             # override провайдера и роли
ai-hats "fix the bug"                      # промпт передаётся провайдеру
ai-hats --tag client=acme                  # custom теги в metrics.json

# Sub-агент в изолированном worktree
ai-hats agent sre --task "investigate alert XYZ"

# Конфигурация и жизненный цикл
ai-hats config set -r <role> -p <provider>
ai-hats config status                      # health-check композиции
ai-hats self init -r <role> -p <provider>  # bootstrap в новом проекте
ai-hats self update && ai-hats self bump   # обновить ai-hats и пересобрать prompt

# Worktrees — изолированная работа на ветке
ai-hats wt create feat/new-thing
ai-hats wt merge

# Наблюдаемость
ai-hats session list --productive --last 10
ai-hats session retro <session_id>          # ручной retro

# Feedback loop
ai-hats reflect session --session <id>      # ручной reflect-session
ai-hats reflect all --dry-run               # триаж накопленного бэклога

# Backlog
ai-hats task create "<title>" -p high
ai-hats task transition <ID> execute
ai-hats task list --state execute --all
```

Все команды и флаги — `ai-hats --tree`.

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

## Orchestration (fan-out, JSON, exit codes)

When running ai-hats as part of a pipeline (parallel/xargs/CI/webhooks), use custom tags, `--json` output, and stable exit codes. См. **[docs/how-to-orchestration.md](docs/how-to-orchestration.md)**.

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
ai-hats task list --search docs              # всё с упоминанием docs (id/title/desc/tags)
ai-hats task list --search "HATS-09[2-3]"   # regex: два эпика сразу
ai-hats task list --search worktree --all    # включая done/failed
```

### Reflection loop

Каждая сессия становится structured retrospective: pure-Python factual layer (метрики, файлы, коммиты, закрытые задачи) + LLM narrative с вердиктами по активным HYP и голосами по PROP. Auto-retro триггерится `session_end` хуком по политике `off | always | smart | hint`.

Полный гайд (политики, session-reviewer, manual triage, hypothesis workflow) — см. **[docs/how-to-feedback-loop.md](docs/how-to-feedback-loop.md)**.

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
    sessions/<id>.md                   # SessionReviewV1 (facts + narrative + HYP verdicts + PROP actions)
<ai_hats_dir>/sessions/runs/
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
  roles/          assistant, test-agent, architect, sre, session-reviewer, go-dev, go-dev-full
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
