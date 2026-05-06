# How-To: примеры конфигураций ai-hats.yaml

Подборка типовых задач, с которыми сталкиваешься при подключении ai-hats к проекту: расширить роль скиллом, убрать ненужный компонент, подложить свой локальный скилл, сменить провайдера. Каждый пример — самодостаточный фрагмент `ai-hats.yaml` + команды для применения.

> Полный справочник CLI с описаниями и опциями — `ai-hats --tree`.

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

## См. также

- [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — настройка и использование цикла reflect-session / reflect-all (политики, гипотезы, валидация харнесом).
- [`docs/reflect.md`](reflect.md) — архитектура retrospective pipeline.
