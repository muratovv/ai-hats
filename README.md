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

Bash launcher в `~/.local/bin/ai-hats` (один раз на хост) → per-project venv в `<ai_hats_dir>/.venv/`. **Одна команда `ai-hats self update`** делает install + heal + update.

### 1. Установить launcher (один раз на хост)

```bash
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash
```

Ставит ~30-строчный bash launcher в `~/.local/bin/ai-hats`. Если `~/.local/bin/` не в `$PATH` — installer подскажет добавить.

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

Альтернативные сценарии установки (bootstrap из клона, override venv, миграция с pipx, разработка ai-hats) — см. **[docs/how-to.md](docs/how-to.md)** и **[docs/migration-333.md](docs/migration-333.md)**.

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

Часто используемые сценарии:

```bash
# Интерактивная сессия с инжектом роли
ai-hats                                    # текущие настройки
ai-hats -p claude -r architect             # override провайдера и роли
ai-hats --tag client=acme                  # custom теги в metrics.json

# Sub-агент в изолированном worktree
ai-hats agent sre --task "investigate alert XYZ"

# Жизненный цикл
ai-hats self init -r <role> -p <provider>  # bootstrap в новом проекте
ai-hats self update && ai-hats self bump   # обновить ai-hats и пересобрать prompt
ai-hats config status                      # health-check композиции
```

Полный справочник — `ai-hats --tree`.

## Orchestration (fan-out, JSON, exit codes)

When running ai-hats as part of a pipeline (parallel/xargs/CI/webhooks), use custom tags, `--json` output, and stable exit codes. См. **[docs/how-to-orchestration.md](docs/how-to-orchestration.md)**.

## Архитектура

Композиция ролей из traits + rules + skills, плоская модель, state-машина задач, multi-provider injection. Полный обзор внутреннего устройства, схемы директорий, формата скиллов и примера `config.yaml` — см. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
