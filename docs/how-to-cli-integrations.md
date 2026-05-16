# Интеграция внешних сервисов через CLI-скиллы

Внешние сервисы (Google Workspace, GitHub, BigQuery, etc.) подключаются к роли как **обычный skill**, документирующий CLI-инструмент. ai-hats остаётся секрет-агностичным: auth, токены и ключи — забота CLI и пользователя.

## Принцип

```
внешний сервис = CLI-инструмент в $PATH + skill, который его документирует
```

Агент вызывает CLI через `Bash`, как и любую другую команду. Никаких MCP-серверов, дополнительных протоколов или встроенной инфраструктуры в ai-hats не требуется.

## Структура integration-skill-а

`<library>/skills/<tool-name>-cli/SKILL.md`:

```markdown
---
name: <tool-name>-cli
description: <одна строка о том, какой сервис покрывает skill>
triggers:
  - "<когда агент должен загружать skill>"
skip:
  - "<когда skill можно пропустить>"
tags: [cli, integration]
---

# <Tool Name> CLI

## Installation

```bash
brew install <tool>     # или npm/curl/pip
```

## Auth (one-time)

```bash
<tool> auth login       # OAuth/токен setup, делается руками
```

Auth state хранится в `~/.config/<tool>/` (или эквивалент). ai-hats этим не управляет.

## Common operations

- `<tool> <command> ...` — что делает + пример.
- ...

## Notes

- Permission allowlist: если хочется auto-approve — добавить `Bash(<tool>:*)` в `.claude/settings.json`.
- Если CLI не установлен — Bash вернёт `command not found`, попросите пользователя установить.
```

## Что НЕ должно быть в skill-е

- **Секреты** в любом виде. Никаких токенов/ключей/паролей в `SKILL.md`, `metadata.yaml` или примерах.
- **Привязка к конкретному пути** установки. CLI должен резолвиться через `$PATH`.
- **Wrapper-скрипты с hardcoded путями**. Если для auth нужен wrapper (читает Keychain, например) — это user-side артефакт (живёт в `~/.local/bin/`), а не часть skill-а.

## Подключение к роли

В `composition` trait/role:

```yaml
composition:
  skills:
    - gworkspace-cli
    - github-cli
```

После `ai-hats bump` skill становится виден агенту через стандартный механизм skill-инжекции.

## Примеры

- `gworkspace-cli` — Google Workspace через https://github.com/googleworkspace/cli (см. эпик HATS-341).
- Дополнительные CLI-интеграции добавляются как child-задачи под HATS-341.
