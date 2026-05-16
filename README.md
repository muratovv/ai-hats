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

Композиция ролей из traits + rules + skills, плоская модель, state-машина задач, multi-provider injection. Полный обзор внутреннего устройства, схемы директорий, формата скиллов и примера `config.yaml` — см. **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
