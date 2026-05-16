<p align="center">
  <img src="assets/logo-256.png" alt="ai-hats" width="180" />
</p>

<h1 align="center">ai-hats</h1>

<p align="center">
  <strong>Do. Reflect. Repeat.</strong>
</p>

<p align="center">
  <em>Композирует AI-агентов из переиспользуемых traits + rules + skills и автоматически рефлексирует над каждой сессией.</em><br>
  <em>Один набор ролей — Claude и Gemini.</em>
</p>

<p align="center">
  <a href="../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue.svg">
  <img alt="Status: Beta" src="https://img.shields.io/badge/status-beta-orange.svg">
  <a href="https://github.com/muratovv/ai-hats/commits/master"><img alt="Last commit" src="https://img.shields.io/github/last-commit/muratovv/ai-hats"></a>
  <a href="https://github.com/muratovv/ai-hats/issues"><img alt="Open issues" src="https://img.shields.io/github/issues/muratovv/ai-hats"></a>
</p>

<p align="center">
  <img src="assets/demo.gif" alt="ai-hats — composition + real sessions + active hypotheses" width="900" />
</p>

<p align="center">
  <a href="../README.md">English</a> · <strong>Русский</strong>
</p>

## Концепция

Бывало ли так — один и тот же AI-агент в разных проектах наступает на одни и те же грабли? Забывает соглашения, пропускает шаг плана, начинает с того же анти-паттерна. `CLAUDE.md` копи-паста не масштабируется: правки расползаются между проектами, а исправление в одном не доходит до других.

ai-hats решает это двумя вещами:

- **Роли как композиция переиспользуемых компонентов** — `traits`, `rules`, `skills`, `hooks` собираются в роль один раз и инжектятся в system prompt любого провайдера (Gemini / Claude). Исправление компонента доходит до всех ролей, где он подключён, через `ai-hats self bump`.
- **Глубокая рефлексия после каждой сессии** — structured retrospective с фактическим слоем (метрики, файлы, коммиты) и LLM-narrative с вердиктами по активным гипотезам и голосами за предложения улучшений. Закономерности из 3–5 сессий превращаются в новые правила и скиллы, и петля замыкается.

```
roles/assistant ── trait-base + trait-agent + dev::python
                   ├── rules: git_workflow, tdd
                   ├── skills: backlog-manager, git-mastery
                   └── injection → GEMINI.md / CLAUDE.md
```

## Быстрый старт

Bash launcher в `~/.local/bin/ai-hats` (один раз на хост) → per-project venv в `<ai_hats_dir>/.venv/`. Подсказка по любой команде — `ai-hats --help`. Полное дерево CLI — `ai-hats --tree`.

### 1. Установить launcher (один раз на хост)

```bash
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash
```

Ставит ~30-строчный bash launcher в `~/.local/bin/ai-hats`. Если `~/.local/bin/` не в `$PATH` — installer подскажет добавить.

### 2. Подключить к проекту

```bash
cd ~/dev/my-project
ai-hats self update                            # создаёт venv в .agent/ai-hats/.venv + installs ai-hats
ai-hats config set -r go-dev -p claude         # выбрать роль и провайдера (auto-init проекта)
```

`config set` создаёт `ai-hats.yaml` + `CLAUDE.md`/`GEMINI.md` под выбранную композицию.

### 3. Использование

```bash
ai-hats                       # запустить сессию с текущими настройками
ai-hats --resume              # флаги передаются провайдеру (claude/gemini)
ai-hats config status         # проверить состояние
ai-hats self bump             # пересобрать prompt после изменений в библиотеке
ai-hats self update           # обновить ai-hats + auto-bump
```

`ai-hats self update` self-healing: если venv сломан после системного python upgrade — пересоздаётся автоматически (только default; override venv user-owned).

Альтернативные сценарии установки (bootstrap из клона, override venv, миграция с pipx, разработка ai-hats) — см. **[how-to.md](how-to.md)** и **[migration.md](migration.md)**.

## CLI

> **Полный справочник команд с описаниями и опциями — `ai-hats --tree`**
> (работает также как `ai-hats --help --tree`).
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
| `self`     | Жизненный цикл инструмента: init / bump / update / clean / rollback     |
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
ai-hats config set -r <role> -p <provider> # выбрать роль и провайдера (auto-init)
ai-hats self update && ai-hats self bump   # обновить ai-hats и пересобрать prompt
ai-hats config status                      # health-check композиции
```

Полный справочник — `ai-hats --tree`.

## Архитектура

Композиция ролей из traits + rules + skills, плоская модель, state-машина задач, multi-provider injection. Полный обзор внутреннего устройства, схемы директорий, формата скиллов и примера `config.yaml` — см. **[ARCHITECTURE.md](ARCHITECTURE.md)**.
