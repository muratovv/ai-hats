# How-To: примеры конфигураций ai-hats.yaml

Подборка типовых задач, с которыми сталкиваешься при подключении ai-hats к проекту: расширить роль скиллом, убрать ненужный компонент, подложить свой локальный скилл, сменить провайдера. Каждый пример — самодостаточный фрагмент `ai-hats.yaml` + команды для применения.

> Полный справочник CLI с описаниями и опциями — `ai-hats --tree` (или поддерево: `ai-hats --tree config`, `ai-hats --tree task hyp`).

> Все изменения в `ai-hats.yaml` применяются командой `ai-hats self bump` (пересобирает `CLAUDE.md` / `GEMINI.md` и `.claude/*` по конфигу). Базовые роли в `libraries/roles/` **не редактируем** — вместо этого используем `customizations` (overlay).
>
> Любую правку оверлея можно сделать двумя способами:
> 1. **CLI:** `ai-hats config customize <role> --add-skill <name> | --remove-skill <name> | --add-trait <name> | --injection-append "<text>"`. Команда сама пишет в `ai-hats.yaml`.
> 2. **Руками:** редактируешь `customizations:` в `ai-hats.yaml` (примеры ниже).
>
> Оба способа эквивалентны. Ниже — итоговый YAML, чтобы было видно, что получится.

---

## 1. Добавить новый скилл к существующей роли

**Сценарий:** в проекте используется роль `sre`, но нужен дополнительный скилл (например, `kubernetes-ops`), которого нет в базовой композиции.

```yaml
schema_version: 2
provider: claude
active_role: sre
default_role: sre
task_prefix: OPS

customizations:
  sre:
    add:
      skills:
        - kubernetes-ops
```

Эквивалент через CLI:

```bash
ai-hats config customize sre --add-skill kubernetes-ops
ai-hats self bump
```

Проверка: после `ai-hats self bump` скилл появится в секции `## AVAILABLE SKILLS` сгенерированного `CLAUDE.md`.

---

## 2. Убрать ненужный скилл из роли

**Сценарий:** базовая роль `sre` тянет `network-documentation`, но в этом проекте сети ведёт другая команда — лишний шум в промпте.

```yaml
customizations:
  sre:
    remove:
      skills:
        - network-documentation
```

Если попытаешься убрать то, чего нет в базовой роли — получишь warning `Overlay: cannot remove skill 'X' — not in base role`, но сборка не упадёт.

---

## 3. Комбинировать add + remove + проектные заметки

**Сценарий:** урезаем роль `sre` под конкретный проект и хотим зафиксировать инфраструктурные особенности прямо в инжекте.

```yaml
customizations:
  sre:
    add:
      skills:
        - kubernetes-ops
      traits: []
      rules: []
    remove:
      skills:
        - network-documentation
    injection_append: |
      ## PROJECT NOTES
      - Кластеры: prod-eu, prod-us, staging
      - Все изменения в инфре — через ArgoCD PR
      - Секреты — только в Vault, никаких .env в репо
```

`injection_append` дописывается **после** инжекта базовой роли — удобно для проектных правил без форка роли.

---

## 4. Подключить локальный (свой) скилл из `.agent/library`

**Сценарий:** скилл специфичен для проекта и не имеет смысла в общей библиотеке ai-hats.

Структура файлов:

```
my-project/
├── ai-hats.yaml
└── .agent/
    └── library/
        └── skills/
            └── deploy-pipeline/
                └── SKILL.md
```

`ai-hats.yaml`:

```yaml
schema_version: 2
provider: claude
active_role: sre

# Локальные библиотеки имеют приоритет над встроенными
library_paths:
  - .agent/library

customizations:
  sre:
    add:
      skills:
        - deploy-pipeline
```

Имя скилла должно совпадать с именем директории внутри `library_paths/skills/`.

---

## 5. Добавить trait целиком (например, dev::python)

**Сценарий:** в SRE-проекте появился Python-тулинг, и хочется получить весь Python-стек правил/скиллов одним движением.

```yaml
customizations:
  sre:
    add:
      traits:
        - dev::python
```

Синтаксис `<group>::<trait>` указывает на trait внутри подкаталога `libraries/traits/<group>/<trait>/`. Trait сам тащит свои rules + skills + injection.

---

## 6. Минимальная конфигурация для нового проекта

**Сценарий:** свежий проект, без оверлеев — просто выбираем роль и провайдера.

```yaml
schema_version: 2
provider: claude
active_role: assistant
default_role: assistant
task_prefix: PROJ
library_paths: []

feedback:
  session_retro:
    policy: smart
```

Это эквивалент того, что генерирует `bootstrap.sh` при первой установке.

---

## 7. Сменить провайдера без потери настроек

```bash
ai-hats config set -p gemini   # переключиться на Gemini
ai-hats self bump            # пересобрать GEMINI.md
```

В `ai-hats.yaml` поменяется только `provider: gemini`. Композиция роли остаётся той же — оба провайдера читают одни и те же библиотеки.

---

## 8. Применение изменений: чек-лист

После любой правки `ai-hats.yaml`:

```bash
ai-hats self bump          # пересобрать промпт
ai-hats config status # убедиться, что всё подхватилось
```

Если правил много, и ты хочешь увидеть только diff:

```bash
git diff CLAUDE.md ai-hats.yaml
```

---

## Структура overlay (справка)

```yaml
customizations:
  <role-name>:
    add:
      traits: [...]    # добавить trait целиком
      rules:  [...]    # добавить отдельные rules
      skills: [...]    # добавить отдельные skills
    remove:
      traits: [...]    # убрать trait из базовой композиции
      rules:  [...]
      skills: [...]
    injection_append: |
      ## ...           # текст, который дописывается ПОСЛЕ injection роли
```

Пустые секции можно опускать. Если `customizations.<role>` целиком пустой — overlay не применяется.

---

## 9. Configurable venv_path

По умолчанию ai-hats живёт в **dedicated** venv по пути `<ai_hats_dir>/.venv/` (default `.agent/ai-hats/.venv/`). Venv создаётся автоматически через `ai-hats self update` или `bash bootstrap.sh`. Bash launcher (`~/.local/bin/ai-hats`) определяет venv по precedence:

1. `AI_HATS_VENV` env var (absolute path, для тестов / sandbox)
2. `venv_path` поле в `ai-hats.yaml` (relative или absolute)
3. Default `<ai_hats_dir>/.venv`

### Use case: shared system venv

```yaml
# ai-hats.yaml
venv_path: /opt/shared/ai-hats-venv
```

```bash
# One-time setup (user-owned!)
python3 -m venv /opt/shared/ai-hats-venv
/opt/shared/ai-hats-venv/bin/pip install "ai-hats @ git+ssh://git@github.com/muratovv/ai-hats.git"

# Дальше launcher автоматически использует override
cd ~/dev/my-project
ai-hats config status   # uses /opt/shared/ai-hats-venv
```

### Use case: проектный venv (re-use existing)

```yaml
# ai-hats.yaml
venv_path: .venv          # уже существующий venv проекта в корне
```

⚠️ В этом случае ai-hats и зависимости проекта живут в одном venv — конфликты версий зависимостей возможны. Это **осознанный** trade-off (override = user-owned).

### Ownership invariant

- **Default venv** (`<ai_hats_dir>/.venv/`) — managed фреймворком. `ai-hats self update` может пересоздавать целиком (например, после системного python upgrade).
- **Override venv** (`venv_path:` в yaml) — user-owned. Ai-hats никогда не удаляет / не пересоздаёт автоматически; только `pip install -U` внутрь.

---

## 10. Recovery scenarios

| Симптом | Команда |
|---|---|
| `ai-hats: command not found` (свежий хост) | `curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh \| bash` (репо приватный → клонируй и запусти `bash scripts/install-launcher.sh`) |
| `ai-hats: venv missing at ...` (нет venv) | `ai-hats self update` |
| `ai-hats: venv exists but ai-hats binary is missing` | `ai-hats self update` |
| Системный python upgrade (proxmox case) | `ai-hats self update` — launcher auto-recreates default venv |
| Import error / corrupted site-packages | `rm -rf .agent/ai-hats/.venv && ai-hats self update` |
| Override venv сломан | `python3 -m venv <override-path> && <override-path>/bin/pip install 'ai-hats @ git+ssh://...'` (user-managed) |
| Полный wipe project (потеря data!) | `rm -rf .agent/ai-hats/ && ai-hats self update && ai-hats self init -r <role> -p <provider>` |

Подробный migration guide для проектов с pipx → launcher: `docs/migration.md`.

---

## См. также

- [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — настройка и использование цикла reflect-session / reflect-all (политики, гипотезы, валидация харнесом).
- [`docs/migration.md`](migration.md) — migration guide на venv-first launcher.
- [`docs/reflect.md`](reflect.md) — архитектура retrospective pipeline.
