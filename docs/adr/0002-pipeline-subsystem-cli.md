# ADR-0002: Pipeline subsystem с собственной CLI и step inventory

## Status

Proposed (HATS-273, 2026-05-09). Дополняет ADR-0001 (не отменяет его).

> **HATS-535 update (2026-05-26).** The `launch_provider` step described
> below is **renamed** to `provider` (`LaunchProvider` retained as a
> deprecated class alias). The audit-derivation + SESSION_END hook
> responsibilities are **extracted** out of that megastep into two new
> steps `make_audit` and `run_session_end`, composed via two new
> sub-pipelines `finalize-hitl` (`make_audit + run_session_end`) and
> `finalize-subagent` (`make_audit` only). The sub-pipelines are
> invoked by `WrapRunner.run` / `_finalize_sub_agent` from their
> `finally` blocks — `claude_session_id` and `hooks_env` flow via
> `pipeline.run(..., initial=...)` rather than through the main funnel.
> The §Step inventory tables and YAML examples below describe the
> pre-HATS-535 shape; the post-refactor canonical reference lives in
> [glossary.md §Pipeline steps & sub-pipelines](../glossary.md#pipeline-steps--sub-pipelines).
> Decision rationale and forks (A1 vs A2, two-pipelines vs single
> finalize) are recorded in `<ai_hats_dir>/tracker/backlog/tasks/HATS-535/plan.md`.

## Context

ADR-0001 (HATS-261) утвердил **контракт** typed-dataflow pipeline: `StepIO` (frozen), `Step` ABC (`run(**inputs) → dict`), `Pipeline` (build/run с projection-based threading + `failure_policy`). Phase 1 эпика HATS-259 (HATS-265, commit `e92ae6b`) реализовал ядро + `LaunchProvider` (обёртка над `_do_execute`) + лог-стабы; `ai-hats execute` после HATS-265 идёт через `execute_pipeline`.

После Phase 1 пользователь решил переориентировать стратегию:

> Сейчас мержим задачку as is, но функционал (и adr) переписываем так, чтобы была новая команда `ai-hats pipeline`, через которую мы будем собирать и запускать пайплайны. Я не хочу сейчас смешивать старый и новый код.

Это означает:
- **Расцепление.** `ai-hats execute` возвращается на прямой `_do_execute` (как было до HATS-265). Pipeline становится **отдельной subsystem** под собственной командой `ai-hats pipeline`.
- **Сосуществование.** Существующие CLI-команды (`bare ai-hats`, `execute`, `reflect all`, `reflect session`) **не пайплайнятся** на этом этапе. Старый и новый код не смешиваются. Финальная миграция CLI-команд на pipeline отложена до HATS-269 / Phase 5 эпика — после стабилизации subsystem'а.
- **ADR-0001 не отменяется.** Контракт `StepIO`/`Step`/`Pipeline` валиден целиком. Этот ADR-0002 дополняет его тремя ортогональными слоями: harness contract, step inventory, CLI subsystem.

Брейншторм с альтернативами и discussion'ом 9 открытых вопросов: `<ai_hats_dir>/tracker/backlog/tasks/HATS-273/brainstorm.md`. Полный blueprint с детальными step specs и YAML-композициями: `<ai_hats_dir>/tracker/backlog/tasks/HATS-273/plan.md`.

## Decision

### §1 Harness contract

Pipeline-core видит только `Path` и плоские значения. **Harness** (CLI-обёртка `ai-hats pipeline run` или Python wrapper) обязан:

1. **Материализация ввода в файлы.** Любой пользовательский ввод (raw text, short-name типа `reflect-all`, любой path, интерактивный аргумент типа `reflect focus "..."`) → нормализуется в **один файл на диске** под детерминированным namespace'ом. Path попадает в initial state pipeline'а.
2. **Идемпотентность.** Запуск pipeline'а должен быть идемпотентным относительно файловых артефактов harness'а: если предыдущий запуск по какой-то причине не удалил свои файлы, новый запуск чистит namespace перед стартом.
3. **Pipeline-core не знает про harness.** Получает `prompt_path: Path` и плоские значения в initial state. Это позволяет тестировать pipeline без harness — initial state может быть synthetic dict с tmp-файлом.

### §2 Step inventory (10 шагов)

| Категория | Step | requires | optional | produces | Params |
|---|---|---|---|---|---|
| pre | `compose_role` | `role` | — | `system_prompt` | — |
| pre | `resolve_prompt` | — | `prompt_path` | `prompt_text` | `default_text: str = ""` |
| pre | `build_handoff` | `project_dir` | — | `handoff_path` | — |
| pre | `pre_log` | — | (`params.keys`) | — | `keys: list[str]` |
| execute | `launch_provider` | `system_prompt`, `interactive` | `prompt_text`, `provider`, `model`, `isolation`, `ticket`, `tags`, `extra_args`, `role` | `session_id`, `session_dir`, `transcript_path`, `exit_code` | — |
| post | `spawn_session_review` | `session_id`, `project_dir` | — | `review_pid` | `max_retries: int = 1` |
| post | `extract_marker` | `transcript_path` | — | `<params.out_key>` | `start: str`, `end: str`, `out_key: str` |
| post | `save_artifact` | `<params.key>` | — | `saved_path` | `key: str`, `out_path_template: str` |
| post | `post_log` | — | (`params.keys`) | — | `keys: list[str]` |
| specialized | `run_session_review` | `session_id`, `project_dir` | — | `review_path` | `max_retries: int = 1` |

**Принципы decomposition'а:**

- **`launch_provider` produces плоские ключи** (`session_id`, `session_dir`, `transcript_path`, `exit_code`), а не `Session`-объект. Post-step'ы requires только конкретные пути → projection-принцип ADR-0001 соблюдён.
- **`allocate_session` НЕ выделен в отдельный step** — это harness setup без бизнес-смысла; всегда непосредственно перед launch, реалистичных перестановок нет. Сливается в `launch_provider`.
- **`compose_role` produces одну строку `system_prompt`** (YAGNI). `CompositionResult` отдельно от `merged_injection` сейчас никем не используется — расширение IO до `{composition, system_prompt, ...}` отложено до первого реального потребителя.
- **Параметризация.** Бизнес-step'ы (`compose_role`, `launch_provider`, `build_handoff`, `spawn_session_review`) — без `params`, всё runtime через initial state. `resolve_prompt` имеет один param (`default_text`). Структурные step'ы (`extract_marker`, `save_artifact`, `pre_log`/`post_log`) обязаны декларировать схему params, потому что эти параметры описывают форму конкретного pipeline'а, а не runtime-ввод.
- **`reflect-session` остаётся blackbox-step'ом** (`run_session_review`) на этом этапе. Декомпозиция на 5+ под-step'ов (compute_facts / build_prompt / launch / extract / validate / save / harness_check) — отдельная работа Phase 3 эпика (HATS-267).

Детальные IO-контракты, failure_policy, edge cases для каждого step'а — `<ai_hats_dir>/tracker/backlog/tasks/HATS-273/plan.md` §"Step specifications (детально)".

### §3 Built-in pipelines (4 эмуляции CLI-команд)

`library/core/pipelines/`:

**`bare.yaml`** — bare `ai-hats`
```yaml
name: bare
steps:
  - id: compose_role
  - id: pre_log
    params: {keys: [role, system_prompt]}
  - id: launch_provider
  - id: spawn_session_review
  - id: post_log
    params: {keys: [session_id, exit_code, review_pid]}
```

**`execute.yaml`** — `ai-hats execute` (interactive + batch, с/без prompt)
```yaml
name: execute
steps:
  - id: compose_role
  - id: resolve_prompt
    params: {default_text: ""}
  - id: pre_log
    params: {keys: [role, system_prompt, prompt_text]}
  - id: launch_provider
  - id: spawn_session_review
  - id: post_log
    params: {keys: [session_id, exit_code, review_pid]}
```

**`reflect-all.yaml`** — `ai-hats reflect all` (judge triage)
```yaml
name: reflect-all
steps:
  - id: build_handoff
  - id: compose_role
  - id: resolve_prompt
  - id: pre_log
    params: {keys: [system_prompt, handoff_path, prompt_text]}
  - id: launch_provider
  - id: extract_marker
    params: {start: BEGIN_JUDGE, end: END_JUDGE, out_key: judge_report}
  - id: save_artifact
    params:
      key: judge_report
      out_path_template: "<ai_hats_dir>/sessions/retros/judge/{ts}-report.md"
  - id: spawn_session_review
  - id: post_log
    params: {keys: [session_id, exit_code, review_pid, saved_path]}
```

Особенность: harness склеивает preamble (`reflect-all.md`) + handoff в один файл `prompt_path`. `build_handoff` step здесь нужен только для записи handoff на диск как трассировки. Pipeline в дизайне не занимается склейкой.

**`reflect-session.yaml`** — `ai-hats reflect session` (single-step blackbox)
```yaml
name: reflect-session
steps:
  - id: run_session_review
    params: {max_retries: 1}
```

Полные YAML с initial state и threading — в plan.md §"YAML compositions".

### §4 CLI shape (high-level skeleton)

```bash
ai-hats pipeline build <name> --step <id> [--step <id> ...]
    → пишет <project>/.agent/pipelines/<name>.yaml

ai-hats pipeline run <name|path> [--in K=V ...] [--in-file <json>]
    → загружает YAML, строит initial state, run

ai-hats pipeline list           → built-in + project-local
ai-hats pipeline show <name>    → cat YAML
```

**Storage resolution:**
1. `<name>` ищется в `<project>/.agent/pipelines/<name>.yaml` (override).
2. Затем в `library/core/pipelines/<name>.yaml` (built-in).
3. Если `<arg>` — путь к `.yaml` файлу — load напрямую без registry.

**Detail decisions откладываются на CLI execute-тикет:** синтаксис `--in` (типы / `--in-file` / префиксы), `--with-trace` flag, параметризация шагов в `build`. Pipeline-семантика на этом ADR-этапе работает только с `Path` и плоскими значениями — на уровне initial state сложных типов нет.

### §5 Decoupling principle

`cli/execute.py` возвращается на прямой `_do_execute` (как было до HATS-265). Никаких import'ов `ai_hats.pipeline` из CLI-команд этого ADR-этапа.

Существующие CLI-команды (`bare`, `execute`, `reflect all`, `reflect session`) и pipeline-subsystem **не имеют общего mutable state**. Единственная общая точка — `pipeline.steps.launch.LaunchProvider` сегодня вызывает `_do_execute` внутри. Эта зависимость уйдёт после декомпозиции `_do_execute` (Phase 2 эпика, HATS-266) — но это работа после approve этого ADR'а, не до.

## Consequences

**Положительные:**
- Pipeline-subsystem стабильно тестируется без затрагивания CLI-команд (ноль regression-риска для существующих flow на этом этапе).
- Built-in pipeline'ы (`bare/execute/reflect-all/reflect-session`) — отгружаемые reference-композиции; пользователь может склонировать и модифицировать.
- Harness contract (file-paths only) делает pipeline-state предсказуемым и trace-able: каждый prompt оставляет файл на диске, который можно diff'ать и повторять.
- `extract_marker` + `save_artifact` решают проблему «judge забыл написать отчёт» (HATS-260 trade-off A) на уровне pipeline-кода, не LLM-инструкции.

**Отрицательные:**
- Дублирование путей: `bare-execute` (без prompt'а) проходит через `bare.yaml`, не `execute.yaml`. Harness/CLI-диспетчинг выбирает какой pipeline запускать на основе наличия `--prompt`.
- HATS-269 (Phase 5 эпика — финальная миграция CLI-команд на pipeline) откладывается до стабилизации subsystem'а.
- HATS-266..269 нуждаются в ревизии тел тикетов после approve этого ADR (фокус сдвигается с «миграция CLI на pipeline» на «extract step'ов для standalone subsystem»).
- На этом ADR-этапе появляется **два пути** проиграть execute-сценарий: прямой CLI (`ai-hats execute`) и pipeline (`ai-hats pipeline run execute`). Это намеренное trade-off на время стабилизации pipeline-subsystem — до Phase 5.

## Alternatives considered

**Rewrite ADR-0001 — отвергнуто.** ADR-конвенция (как в этом репо) — летопись: ранее принятые решения сохраняются, дополнения приходят новыми ADR. Контракт `StepIO`/`Step`/`Pipeline` валиден целиком и не требует переписки.

**Pipeline видит `CompositionResult` / `Session` целиком (rich-объекты в state) — отвергнуто.** Это превращает Step'ы в потребителей God-объектов и ломает projection-принцип ADR-0001. Плоские ключи симметричны (как для `system_prompt`, так и для post-launch путей) и тестируются проще.

**Раздельные `execute.yaml` и `execute-no-prompt.yaml` — отвергнуто.** `resolve_prompt` с `params.default_text` покрывает оба сценария одним pipeline'ом. Цена — один YAML-параметр.

**Полная декомпозиция `reflect-session` на 5+ step'ов сейчас — отложено.** Внутренняя логика `SessionReviewRunner` (compute_facts / build_prompt / launch / extract / validate / harness_check / save / file_meta_proposal) сцеплена через retries+validation цикл — декомпозиция требует отдельной работы. На этом этапе остаётся blackbox-step'ом; декомпозиция запланирована в HATS-267 (Phase 3).

**`allocate_session` как отдельный pre-step — отвергнуто.** Это harness setup без бизнес-смысла; реалистичных сценариев перестановки порядка allocate vs другие pre-step'ы не нашлось. Слилось в `launch_provider`.

**Step'ы получают параметры через CLI flag'и (`--step pre_log:keys=role,system_prompt`) — отложено на CLI execute-тикет.** На уровне YAML параметры через `params: {...}`. CLI-параметризация (если потребуется) — отдельный design.

## Implementation roadmap (отдельные тикеты после approve)

1. **Decoupling-тикет** (минимальный, blocking-free): откатить `cli/execute.py:execute_cmd` к прямому `_do_execute` (как до HATS-265). Verification: `ai-hats execute --batch --role assistant --prompt ping` отрабатывает без import `ai_hats.pipeline`.
2. **Step-implementation тикет(ы)**: реализация 10 step'ов из §2 + step-registry + YAML-loader + 4 built-in pipeline'а из §3. Каждый step с unit-тестом; integration test `bare.yaml` end-to-end.
3. **Harness-implementation тикет**: CLI `ai-hats pipeline {build, run, list, show}` + namespace cleanup + idempotency-test.
4. **Ревизия HATS-266..269**: после approve этого ADR пересмотреть тела этих тикетов под новую архитектуру (фокус — extract'ить step'ы для subsystem'а, не мигрировать CLI). Сами тикеты помечаются blocked-by HATS-273.

## References

- ADR-0001: `docs/adr/0001-pipelines-as-typed-dataflow.md` — контракт StepIO/Step/Pipeline (валиден целиком).
- Brainstorm: `<ai_hats_dir>/tracker/backlog/tasks/HATS-273/brainstorm.md` — 9 открытых вопросов и discussion.
- Plan (детальный blueprint): `<ai_hats_dir>/tracker/backlog/tasks/HATS-273/plan.md` — step specs, YAML compositions, threading, edge cases.
- Pipeline Phase 1: commits `90d4f3c` (ADR-0001 landing), `e92ae6b` (core+stubs+preset).
- Эпик-план: `.claude/plans/moonlit-sprouting-tower.md` — 5-фазный roadmap миграции (Phase 2-5 нуждаются в ревизии после landing'а ADR-0002).
- Существующий relevant runtime:
  - `src/ai_hats/cli/execute.py:60` (`_do_execute`), `:35` (`_resolve_prompt`)
  - `src/ai_hats/cli/reflect.py:79` (`_spawn_detached`), `:187` (`_build_handoff`)
  - `src/ai_hats/composer.py:52` (`Composer.compose` → `CompositionResult.merged_injection`)
  - `src/ai_hats/observe.py:34` (`SessionManager.create_session`)
  - `src/ai_hats/retro/session_review_runner.py:73` (`SessionReviewRunner.run`), `:36-37` (`REVIEW_DELIM_*`)
  - `src/ai_hats/runtime.py` (`WrapRunner`, `SubAgentRunner`)
