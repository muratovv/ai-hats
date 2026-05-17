# ADR-0003: Graded mutation policy для role-audit family

## Status

Proposed (HATS-303, 2026-05-12).

## Context

HATS-292 / HATS-302 диалог и две Pass A baseline-сессии (`ai-hats reflect role auditor-for-role`, `… judge-for-role`) выявили структурную неоднозначность в политике мутаций ролей семейства role-audit.

**Текущая (binary) модель:**

- `base-auditor` → «analysis only, no CLI mutation, single marker artifact»
- `base-judge` → «analysis + `ai-hats task create` + dialogue»

Оба base-trait запрещают редактирование source-файлов ролей/скиллов/правил/трейтов даже когда `base-judge` уже мутирует project state через CLI. Граница «task create OK, source edit NOT» проведена по типу файла, не по принципу.

**Два конкретных дефекта из baselines:**

### Дефект 1 — anti-anchoring conflated with session boundary

Протокол запрещает чинить findings в той же сессии, в которой они появились, потому что «auditor бы retrofit-ил собственные findings». Но anchoring — функция размера и свежести контекста, не лейбла «session». Свежая сессия, читающая собственный filed report, тоже может retrofit-ить; same-session agent с явным cold-reread шагом и установкой «ignore prior reasoning» — может починить без bias. Session bounce — неточный прокси для anchoring mitigation.

### Дефект 2 — mutation policy внутренне противоречива

`base-judge` §Boundaries: «All state mutations through the `ai-hats` CLI.» Но Write-инструмент для отчётного пути — это state mutation, минующая CLI (прямая запись в `.agent/**`). Carve-out живёт в role-injection (см. HATS-301, finding #5 в `2026-05-12T07-42-39Z-judge-for-role.md`), не в base trait. Линия «через CLI / не через CLI» уже сломана на report-write.

### Сопутствующие findings из baselines

| Finding                                                          | Baseline file                                      | Это ADR purviews?      |
|------------------------------------------------------------------|----------------------------------------------------|------------------------|
| `rule_backlog_discipline` мандатит CLI на роли, которой CLI запрещён | auditor #1                                         | Yes (§1, §5)           |
| Report-Write carve-out асимметричен                              | judge #5                                           | Yes (§3)               |
| `trait-base` несёт coder-семантику в analyst-роли                | auditor #4, judge #3, judge #4                     | Yes (§4)               |
| `backlog-manager` загружается целиком ради двух verbs            | judge #2                                           | Yes (§5)               |
| Marker delivery decision tree не ведёт §Step 3                   | judge #6                                           | Yes (§6)               |
| Off-purpose components в compositions                            | auditor #2, #3; judge #1                           | Yes (§5)               |
| Tool-call wording drift (≤5 vs ≤3–5)                             | auditor #6                                         | No (cosmetic — Phase 6 плана)    |

**Подробный execution-blueprint:** `.agent/backlog/tasks/HATS-303/plan.md`.

## Decision

Заменить бинарную «edit / don't edit» политику тремя уровнями, объявляемыми в composing base trait и override-имыми per-role.

### §1 Levels

| Level | Allowed writes                                          | Allowed CLI                                                      | Default for     | Activation                                |
|-------|---------------------------------------------------------|------------------------------------------------------------------|-----------------|--------------------------------------------|
| L0    | report Write to one declared path                       | none                                                             | `base-auditor`  | implicit                                   |
| L1    | L0 + ack'd backlog mutations (per role's protocol whitelist) | `ai-hats task create`, `ai-hats list …`, role-defined verb prefixes | `base-judge`    | implicit                                   |
| L2    | L1 + edit role/skill/rule/trait source files            | L1 + git ops через supervisor's standard flow                    | none (override) | explicit supervisor ack + cold-reread      |

**L2 — не отдельная роль**, а session-mode, в который judge может войти, когда supervisor явно авторизует («take it»). Anti-anchoring обрабатывается отдельно: mandatory cold-reread шаг перед L2 edits, не session-bounce alone.

Audit trail сохраняется: в L2 fix-task всё ещё filed BEFORE source edit — analysis→task→commit chain остаётся intact даже внутри одной сессии.

### §2 Anti-anchoring — cold-reread, не session-bounce

Старая формулировка («fresh session обязательна для fix») заменяется на:

> **Cold-reread requirement.** Before editing source files in L2 mode, re-read the original report from disk (not from session memory) and explicitly state «ignoring prior reasoning, applying only what's in this report». Session boundary is no longer the mechanism — freshness is achieved by structured re-read.

Reason: session boundary — proxy метрика. Real mechanism — context freshness, который достижим внутри сессии явным re-read'ом.

### §3 Report-Write carve-out — на уровне base trait

Carve-out `.agent/retrospectives/role-coherence/**` (и аналогичные пути для других analyst-ролей) поднимается из per-role injection в `base-judge` §Boundaries:

> Each judge variant may declare ONE additional `.agent/**` write path in its role injection (typically the retrospective output). This is the L0 baseline carve-out that every L1+ role inherits.

Аналогично для `base-auditor`. Это закрывает HATS-301 поглощением — отдельная задача для lift'а carve-out больше не нужна.

### §4 New `trait-analyst-base` для analyst-ролей

`trait-base` (general-purpose) несёт несколько bullets, которые контрадиктят analyst-ролям (auditor #4: «Ask clarifying questions» vs «No mid-run dialogue»; judge #3: «Pessimistic verification (lint, test, check)» на роли без code-мутаций; judge #4: «Show don't tell — provide code» на роли с прозовым отчётом).

Альтернатива «дописать override-секции в `base-auditor`/`base-judge`» отвергнута — контрадикт остаётся в системном промпте, мы его поверх замазываем прозой.

**Решение:** Создать `trait-analyst-base` с только теми bullets, что релевантны analyst-ролям (Safety priority, Research→Findings→Report, Brevity, Be concise, Lead with finding, Least Astonishment, + два global rules: `resource_hygiene` + `destructive_actions`; **без** `session_end_auto-retro.sh` hook — report IS the retro). Подробная таблица — `plan.md` §C.

Composition меняется в analyst-ролях:
- `auditor-for-role.composition.traits`: `[trait-analyst-base, base-auditor]`
- `judge-for-role.composition.traits`: `[trait-analyst-base, base-judge]`

`trait-base` не трогается — он остаётся для coder-ролей (primary agent).

### §5 Composition trimming + narrow skill `backlog-create`

Снять с analyst-ролей компоненты, чья поверхность не активируется:

| Role               | Drop                                                                                                |
|--------------------|------------------------------------------------------------------------------------------------------|
| `auditor-for-role` | `rule_backlog_discipline` (presumes CLI ops; auditor has none), `tool-evaluation-protocol` (no adoption decisions), `trait-researcher-mindset` (implicit via §4 composition change) |
| `judge-for-role`   | `tool-evaluation-protocol`, `trait-researcher-mindset` (implicit via §4)                            |

`backlog-manager` остаётся as-is для `judge` и primary agent (его полное lifecycle-содержимое корректно). Для `judge-for-role`, который использует только `ai-hats task create`, создаётся новый узкий skill `backlog-create` (~30 строк): invocation + venv hint + xref на `backlog-manager`. Composition: `judge-for-role.composition.skills` — `backlog-manager` → `backlog-create`.

**Follow-up:** отдельная задача (filed в Phase 9 плана) на сплит `backlog-manager` в `backlog-tasks` / `backlog-hyp` / `backlog-proposal`. После landing'а сплита `backlog-create` deprecate в пользу `backlog-tasks`. Out of scope этого ADR.

### §6 Delivery-path decision tree в role-coherence-protocol

`role-coherence-protocol` §Step 3 переписывается так, чтобы вести с двух-строчного дерева:

```
Composed with `base-auditor`? → emit between BEGIN_REFLECT / END_REFLECT.
Composed with `base-judge`?   → Write tool to declared report path. No markers.
```

Существующая проза уезжает под соответствующую ветку. Closes judge #6 (тройная редупликация marker-policy без явного branching).

`role-coherence-protocol` §Scope теперь defers to base-trait level: «Mutation policy is defined by your composing base trait — `base-auditor` (L0) / `base-judge` (L1 with optional L2 activation per **judge-role-protocol**).» Removes scope-clause redundancy между skill и trait.

## Consequences

**Положительные:**

- Mutation policy получает явный single source of truth — composing base trait. Per-role injection только декларирует L0 report path; больше нет асимметрии «запрет в rule, override в injection».
- Anti-anchoring отделён от session boundary — supervisor может авторизовать L2 в той же сессии без потери защиты от retrofit-bias (cold-reread + filed task BEFORE edit).
- Analyst-роли получают чистую composition: один `trait-analyst-base` без коntradicting bullets, без override-by-prose.
- HATS-301 (lift carve-out) поглощается — отдельный тикет закрывается duplicate.
- Family extensible: будущие L0/L1 роли (HATS-299 `auditor-for-session`, HATS-300 `judge-for-hyp-prop`) композят те же base-traits и наследуют уровень автоматически.

**Отрицательные:**

- Прибавляется два новых компонента в library (`trait-analyst-base`, `backlog-create`). `backlog-create` — временный shim до сплита `backlog-manager`.
- L2-activation handshake требует явной supervisor-фразы и cold-reread шага — это disciplinarian load на judge во время interactive-сессии. Trade-off: автоматическая L2 без handshake опаснее.
- `trait-analyst-base` дублирует часть bullets из `trait-base` (Safety priority, Least Astonishment, Brevity). ~10 строк дублирования — приемлемо, чтобы избежать включения коntradicting bullets.
- HATS-299 / HATS-300 миграции должны пересмотреть свою scope под новую модель (cross-link добавлен в их карточки).

## Alternatives considered

**Сохранить binary edit / no-edit + session-bounce как anti-anchoring — отвергнуто.** Дефекты 1 и 2 в §Context показывают, что binary модель уже сломана на report-write carve-out, и session-bounce — неточный прокси. Сохранение требует accept'ить оба structural defect-а.

**Override-by-prose в `base-auditor` / `base-judge` (без `trait-analyst-base`) — отвергнуто.** Контрадикт остаётся в системном промпте, мы его поверх замазываем. Reader следующий rule chain top-down (rule → trait → role) сначала встречает противоречащую инструкцию, потом её override. Тот же defect-pattern, что и binary policy.

**Редактировать `trait-base` напрямую — отвергнуто.** Blast radius — каждая роль в библиотеке, не только analyst-семейство. Primary agent и future coder-роли легитимно нуждаются в pessimistic verification / show-don't-tell / clarifying-questions. Сохраняем `trait-base` как general-purpose default; specialization уезжает в `trait-analyst-base`.

**D.1 — paragraph про `ai-hats task create` внутри `judge-role-protocol` вместо отдельного skill'а — отвергнуто.** «If's» внутри скилла; нарушает single-purpose принцип skill engineering. Узкий skill `backlog-create` композабельнее.

**Full split `backlog-manager` сейчас — отложено.** Blast radius шире одного ADR (4 composition change-а + миграция документации). Отдельная follow-up задача.

**Editor-level access (L2) как отдельная роль — отвергнуто.** Доступ к source edit — атрибут авторизации, не композиции. Создавать клонированную роль ради одного capability flag добавило бы дублирование без выигрыша. L2 — session-mode toggle поверх существующего judge-варианта.

## Implementation roadmap

Полный execution-blueprint: `.agent/backlog/tasks/HATS-303/plan.md`. Краткое отображение фаз → секции ADR:

| Phase   | ADR sections covered             |
|---------|----------------------------------|
| 2 ADR   | this document                    |
| 3 New components | §4 (`trait-analyst-base`), §5 (`backlog-create`) |
| 4 Trait edits    | §1, §2 (anti-anchoring), §3 (carve-out) |
| 5 Skill edits    | §1 (deferral), §6 (decision tree) |
| 6 Rule edit      | cosmetic (not in this ADR)       |
| 7 Role edits     | §4 (composition swap), §5 (drops + skill swap) |
| 8 Pass B         | acceptance gate                  |
| 9 Close-out      | follow-up task, HATS-301 close   |

**Acceptance gate (Phase 8):** Pass B reports show zero findings of classes
- «mutation policy inconsistency» (§1, §3)
- «trait-base / role mismatch» (§4)
- «off-purpose component» (§5)
- «oversized bundle» (§5)
- «delivery-path ambiguity» (§6)
- «report carve-out asymmetry» (§3)

и **no new findings of unrelated classes** introduced.

## References

- **Pass A baselines (evidence base):**
  - `.agent/retrospectives/role-coherence/2026-05-12T07-42-39Z-judge-for-role.md` (6 findings)
  - `.agent/retrospectives/role-coherence/2026-05-12T11-37-31Z-auditor-for-role.md` (6 findings)
- **Plan:** `.agent/backlog/tasks/HATS-303/plan.md` — file map, 9 phases, 17 steps
- **Related tickets:**
  - HATS-298 (epic parent)
  - HATS-301 (carve-out lift — absorbed by §3)
  - HATS-302 (cleanup bundle — independent; finding #5 overlap)
  - HATS-299 / HATS-300 (post-migration Pass C deferred to those tickets)
- **Touched components (post-ADR; paths reflect post-HATS-363 layout):**
  - NEW `library/core/traits/trait-analyst-base/config.yaml`
  - NEW `library/core/skills/backlog-create/SKILL.md` + `metadata.yaml`
  - `library/core/traits/base-auditor/config.yaml`
  - `library/core/traits/base-judge/config.yaml`
  - `library/core/skills/role-coherence-protocol/SKILL.md`
  - `library/core/skills/judge-role-protocol/SKILL.md`
  - `library/core/roles/auditor-for-role/config.yaml`
  - `library/core/roles/judge-for-role/config.yaml`
- **Source dialogue:** baseline finding #5 в `2026-05-12T07-42-39Z-judge-for-role.md` для carve-out asymmetry; baseline finding #1 в `2026-05-12T11-37-31Z-auditor-for-role.md` для rule_backlog_discipline mismatch.
