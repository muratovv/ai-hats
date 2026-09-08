# ADR-0001: Pipelines as typed dataflow with strict step contracts

## Status

Proposed (HATS-261, 2026-05-08)

## Context

После HATS-260 ai-hats имеет CLI-команду `execute`, которая поднимает LLM-сессию через `WrapRunner` (interactive PTY) или `SubAgentRunner` (batch). Все остальные LLM-команды (`bare ai-hats`, `agent`, `reflect all`, `reflect session`) — обёртки над этими двумя runner-ами с захардкоженными pre/post-обработками внутри flow-классов и CLI-команд:

- `reflect_all_cmd._build_handoff` — собирает HYP+PROP markdown в монолитную функцию.
- `SessionReviewRunner._render_session_evidence` / `_extract_yaml` / `_save` — хардкод pre+post внутри одного класса.
- `agent.run_subagent` — чтение метрик и форматирование JSON inline.
- judge-protocol полагается на LLM-`Write` tool для сохранения отчёта (HATS-260 trade-off A: «judge может забыть»).

Эти куски не композируются между flow, плохо тестируются изолированно (требуют моков всего рантайма), и любая попытка добавить новый сценарий («параллельно прогнать две модели и сравнить», «pre-block X для нового агента») требует правки flow-класса.

Параллельная критика дизайна mutable-envelope варианта (отвергнутого):

- Свободный mutable state не масштабируется на параллельные ветки и нестандартные потоки данных — каждое расширение требует правки центрального dataclass.
- Mutable state ломает изоляцию тестирования: side-effects накапливаются, тестирование одного step требует моков всей цепочки.

## Decision

Все LLM-flow в ai-hats строятся как **типизированный декларативный dataflow**. Каждый flow — список **steps**, передаваемых в `run_pipeline`. Ниже — полный контракт.

### 1. Step contract (strict input projection)

Step **не видит** общий state. Получает только то, что объявил в `requires`/`optional`, через kwargs. Возвращает **только дельту** — словарь с ключами из `produces`.

```python
@dataclass(frozen=True)
class StepIO:
    name: str
    requires: frozenset[str] = frozenset()    # MUST be in state — build-time validated
    optional: frozenset[str] = frozenset()    # passed if present, otherwise omitted
    produces: frozenset[str] = frozenset()    # delta keys this step may emit


FailurePolicy = Literal["halt", "continue"]


class Step(ABC):
    failure_policy: FailurePolicy = "halt"

    @property
    @abstractmethod
    def io(self) -> StepIO: ...

    @abstractmethod
    def run(self, **inputs: Any) -> dict[str, Any]:
        """Returns ONLY keys declared in io.produces. Subset is allowed."""
```

### 2. Pipeline — itself a Step (recursion / nesting)

Pipeline композирует steps и сам реализует Step interface. `Pipeline.io` рассчитывается из children: external `requires` = что children требуют и не получают от prior siblings; `produces` = всё что children produce. Это позволяет вкладывать Pipeline как step в другой Pipeline (используется для переиспользования post-handler-ов).

```python
@dataclass(frozen=True)
class Pipeline(Step):
    steps: tuple[Step, ...]
    pipeline_name: str = "pipeline"
    failure_policy: FailurePolicy = "halt"

    @property
    def io(self) -> StepIO:
        produced: set[str] = set()
        external_req: set[str] = set()
        external_opt: set[str] = set()
        for s in self.steps:
            external_req |= (s.io.requires - produced)
            external_opt |= (s.io.optional - produced - external_req)
            produced |= s.io.produces
        return StepIO(
            name=self.pipeline_name,
            requires=frozenset(external_req),
            optional=frozenset(external_opt),
            produces=frozenset(produced),
        )

    def run(self, **inputs: Any) -> dict[str, Any]:
        return _run_steps(self.steps, inputs, parent_policy=self.failure_policy)
```

### 3. Two-phase: BUILD + RUN

PHASE 1 — `build(*steps)` собирает `Pipeline` как структуру данных и валидирует self-consistency (требуемые ключи каждого шага либо в external requires, либо produced ранее). Никакого I/O.

PHASE 2 — `run(pipeline, initial)` валидирует против фактических `initial.keys()`, затем executes step-ы по очереди, threading immutable state. Каждая итерация: проектирует state → step kwargs (только requires + available optional), вызывает `step.run(**kwargs)`, валидирует delta keys ⊆ produces, мержит в state.

```python
def run(pipeline: Pipeline, initial: Mapping[str, Any]) -> Mapping[str, Any]:
    validate(pipeline, frozenset(initial.keys()))
    return _run_steps(pipeline.steps, dict(initial),
                      parent_policy=pipeline.failure_policy)


def _run_steps(steps, state, *, parent_policy):
    for s in steps:
        kwargs = {k: state[k] for k in s.io.requires}
        kwargs.update({k: state[k] for k in s.io.optional if k in state})
        try:
            delta = s.run(**kwargs)
        except Exception as e:
            if s.failure_policy == "halt":
                raise
            state.setdefault("errors", {})[s.io.name] = e
            continue
        unexpected = set(delta.keys()) - s.io.produces
        if unexpected:
            raise StepError(f"{s.io.name}: emitted unexpected keys {unexpected}")
        state.update(delta)
    return state
```

### 4. Composites: Parallel, Branch

Параллелизм/ветвления выражаются через композитные steps, которые сами реализуют Step (без правки core).

- `Parallel(*children)` — children выполняются concurrently на одном snapshot state. Build-time check на disjoint produces. (Для последовательного pipeline требование слабее — см. Update HATS-1249 ниже: повторный producer легален, если его выход прочитан до перезаписи. Для `Parallel` порядка нет, поэтому «прочитан до» не определено и disjoint-требование остаётся строгим.)
- `Branch(predicate, then_, else_)` — выбор ветки по predicate(inputs). Build-time check: обе ветки имеют одинаковые produces (иначе downstream зависит от runtime value).

> **Не реализовано (HATS-1655, 2026-08-13).** Ни `Parallel`, ни `Branch` в дерево не попали: модуль `composites.py` из Phase 2 не появился, и ни одного класса с такими именами в `src/` и `packages/` нет. Решение оставлено как записано — оно объясняет, почему ядро не знает про параллелизм и ветвление; но оговорка про `Parallel` в скобках выше (добавлена апдейтом HATS-1249) рассуждает о шаге, которого нет. Строгость disjoint-produces для `Parallel` — правило для будущей реализации, не проверка, которая сегодня работает.

### 5. failure_policy per step

Каждый step имеет атрибут `failure_policy: "halt" | "continue"`. Default зависит от типа step-а:

| Тип step-а                                           | Default  | Reason                                                         |
| ---------------------------------------------------- | -------- | -------------------------------------------------------------- |
| AttachRole / Execute / ExtractMarker / Attach*Prompt | halt     | без них последующие шаги бессмысленны                          |
| AttachTags / AttachTicket / AttachSessionInfo        | continue | observability/metadata — потеря не критична для основного flow |

При raise step-а с `halt` — Pipeline останавливается, исключение propagate-ится. При `continue` — запись в `state["errors"][step.name]` и переход к следующему. `Execute` с `exit_code != 0` НЕ raise — это нормальное завершение, post-блоки решают через `Branch` или зависимостью на `optional={"session_id"}` если хотят пережить.

### 6. State conventions (open dict)

State — `dict[str, Any]`. **Нет** центральной таблицы ключей. Каждый step объявляет свои `requires`/`optional`/`produces` в собственном `StepIO` — это и есть контракт. Framework валидирует только локальные dependencies. Пользовательские расширения добавляют свои ключи в свои steps без правки общего реестра.

Часто встречающиеся ключи built-in steps (cheat sheet, не authoritative):

| Key                                       | Type                   | Producer                                   | Consumer                 |
| ----------------------------------------- | ---------------------- | ------------------------------------------ | ------------------------ |
| `project_dir`                             | `Path`                 | initial                                    | большинство              |
| `role` / `role_text`                      | `str \| None`          | AttachRole / AttachRoleText                | Execute                  |
| `provider`                                | `str \| None`          | initial                                    | Execute                  |
| `prompt`                                  | `str`                  | Attach*Prompt-семейство (append-семантика) | Execute                  |
| `tags` / `ticket` / `model` / `isolation` | …                      | initial / AttachTags                       | Execute                  |
| `session_id` / `exit_code`                | `str` / `int`          | Execute                                    | post-steps               |
| `session_info`                            | `SessionInfo`          | AttachSessionInfo                          | ExtractMarker            |
| `<artifact_key>`                          | любой                  | ExtractMarker / ExecuteToArtifact          | Python-код после run()   |
| `errors`                                  | `dict[str, Exception]` | framework (continue-policy)                | финальный return / debug |

### 7. Lifecycle (не блок)

`init_audit`, `log_trace`, `hooks SESSION_START/SESSION_END`, finalize в `WrapRunner`/`SubAgentRunner` остаются inside runtime. Они — invariant сессии, не контентная обработка. `Execute` step просто их использует через runner.

### 8. Persistence (запись на диск) — НЕ Step на этом этапе

Final state после `run(...)` содержит artifact-ы как Python-объекты. Persistence — обычный `Path.write_text(final["judge_report"])` после `run()`. `SaveArtifact`-как-Step может быть добавлен позже, если/когда появится `ai-hats pipeline preproc ... execute ... postproc ...` bash-команда (требуется декларативная запись изнутри chain) или specific failure_policy на сохранение. На текущем этапе — over-engineering.

### 9. CLI surface — два уровня доступа

**Уровень 1 (Phase 1-3)**: thin CLI wrappers вокруг pure-функций примитивов:

- `ai-hats reflect handoff` — печатает HYP+PROP markdown в stdout
- `ai-hats session info <sid> [--json]`
- `ai-hats session evidence <sid>`
- `ai-hats session extract <sid> --markers BEGIN END [--schema X]`
- `ai-hats role show <name>`
- `ai-hats execute --role X --prompt FILE|name|-`

Композиция через shell-pipe.

**Уровень 2 (Phase 5+)**: `ai-hats pipeline preproc ... execute ... postproc ...` — bash-команда, собирающая chain step-имён в один Pipeline. Внутри — буквальное `build(...) → run(...)`. Без YAML, без сериализации.

YAML-манифесты — **отвергнуты**: добавляют слой сериализации без value на текущей стадии. Если когда-то понадобится shareable/version-controlled pipeline — отдельное решение.

## Alternatives considered

### A. Mutable envelope (отвергнут)

Single `SessionEnvelope` dataclass со всеми полями, mutable, передаваемый между steps. Отвергнут:

- Любая новая ветвление/параллельность требует правки core dataclass.
- Mutable state ломает тестирование: для одного step нужны моки всех остальных.
- Centralized schema конфликтует с пользовательскими расширениями (backward compat).

### B. Stdout-only pre-blocks + bash pipes (отвергнут как primary path)

Pre-blocks как stdout-команды, post-blocks как stdin-команды, всё композируется через bash. Отвергнут как primary:

- Не покрывает случаи, когда pre-block должен пробросить metadata (тэги, выбор модели), а не только content.
- Pipeline-as-Step (recursive composition) сложно выразить через shell-pipe.
- Тесты на shell-pipe требуют CLI-моков; на Python-функции — pure dict in/out.

Сохранён как **уровень 1 CLI surface** для пользовательских ad-hoc сценариев, но built-in flow используют Python-pipeline.

### C. YAML/манифестовая сериализация Pipeline

`pipelines/reflect-all.yaml` + `ai-hats pipeline run <file>`. Отвергнуто на текущей стадии: добавляет слой сериализации (YAML парсер, custom tags, валидация) без value пока pipeline-ы определяются внутри codebase. Если появится use-case для shareable user-defined pipelines — пересмотрим.

### D. Истинный reactive (Rx-streams, observers)

Стримы событий, observers, backpressure. Отвергнуто: scope — sequential dataflow с типизированными dependencies. Если позже понадобится потоковая обработка длинных сессий (e.g. live tail на retry-loop) — отдельная итерация.

## Consequences

### Положительные

- **Композируемость**: добавление нового pre/post step — добавление одного класса. Параллелизм/ветвления через композитные steps без правки core.
- **Тестируемость**: каждый step тестируется вызовом `step.run(**inputs)` с явными значениями. Никаких моков рантайма. Pipeline отдельно тестируется с моковыми steps.
- **Build-time валидация**: missing inputs ловятся до запуска LLM.
- **Расширяемость**: пользовательские steps добавляют свои ключи в свой `StepIO` — no central registry to update.
- **Закрывает HATS-260 trade-off A**: judge пишет отчёт между маркерами, post-step `ExtractMarker` извлекает из transcript. Не зависит от LLM-`Write` tool.
- **Pipeline-as-Step nesting**: общие post-handler-ы (e.g. `post_judge_extract`) переиспользуются как steps в других pipeline-ах.

### Отрицательные

- **Больше кода** на этапе внедрения: ~5 phases миграции существующих flow.
- **Step boilerplate**: каждый step — отдельный класс с `io`/`run`. Acceptable для type safety + тестируемости.
- **Retry-loop остаётся в Python** для `SessionReviewRunner` — correction-prompt построение нелинейно, не выражается через декларативный chain без специального LoopWithCorrection composite (overkill для одного use case).
- **Pipeline.io вычисляется лениво** на каждый доступ — overhead малый, но при глубокой вложенности можно хотеть кэш. Pas сейчас not needed.

## Phase plan (value-driven)

Конкретные HATS-NNN не заводятся до старта Phase 1.

### Phase 1 — Base + Execute (минимум работающий рантайм)

**Цель**: `ai-hats execute` стоит на ногах как самостоятельная команда без Pipeline-машинерии. Закрывает дыры из HATS-260.

Работа: `--system-prompt FILE|-` flag, `LATEST_SID` маркер-файл, `--ticket`/`--tag` → env vars, `ai-hats role show <name>`.

### Phase 2 — Pipeline core + первый pre-block

Работа: `pipeline/{step,pipeline,composites}.py`, trivial steps, `Execute` step + рефакторинг `execute_cmd` body на однотрезный Pipeline, `preproc.handoff.render_reflect_all_context` + `AttachReflectHandoff` + `ai-hats reflect handoff` CLI + `--prompt -` stdin.

Acceptance: `ai-hats reflect handoff | ai-hats execute --role X --batch --prompt -` end-to-end.

### Phase 3 — Postprocessing

Работа: `postproc.session_info.describe_session` + `AttachSessionInfo` + `ai-hats session info <sid>` CLI; `postproc.extract.extract_marker_block` + `ExtractMarker` + `ai-hats session extract <sid>` CLI.

### Phase 4 — Композиция pre + post (первая end-to-end value)

Работа: `judge-protocol` skill инструктирует писать между `BEGIN_JUDGE_REPORT`/`END_JUDGE_REPORT`; `reflect_all_cmd` → Pipeline (включает `ExtractMarker` для отчёта); persistence отчёта — `Path.write_text(final["judge_report"])` после `run()`.

Acceptance: trade-off A из HATS-260 закрыт. `<ai_hats_dir>/sessions/retros/judge/<ts>-report.md` появляется автоматически.

### Phase 5 — Перевод остальных flow на Pipeline

Работа: `_launch_session`, `agent.run_subagent`, `SessionReviewRunner` (retry-loop поверх Pipeline). Дополнительные pre-blocks для session-reviewer.

Acceptance: все 5 LLM-команд внутренне работают через Pipeline. Внешний CLI неизменен. 658 существующих тестов + новые проходят.

## All flows as Pipeline literals

Полные Pipeline-литералы для всех LLM-команд приведены в плане (`<ai_hats_dir>/tracker/backlog/tasks/HATS-261/plan.md`, секции 3.1-3.6). Включают: bare `ai-hats`, `ai-hats execute`, `ai-hats agent`, `ai-hats reflect all`, `ai-hats reflect session` (foreground + `--background`), и иллюстрацию parallel models comparison.

Per-flag CLI mapping (Command → Pipeline reference) — там же.

## Update — HATS-1249 (2026-07-27) отказ от потерянной перезаписи

§3 валидировал только «требуемый ключ кем-то производится». Дубль **producer**-а не проверялся: BUILD делал `produced |= s.io.produces` (union), RUN — `state.update(delta)` (перезапись). Два `provider`-шага объявляют одни и те же `session_id` / `session_dir` / `transcript_path` / `exit_code`, так что второй launch затирал первый — молча, без ошибки и предупреждения.

Ключевое наблюдение: **две формы ведут себя по-разному**, и дефектна только одна.

- `launch → consumer → launch → consumer` (чередование) — **корректен**: каждый post-шаг читает свою сессию до перезаписи. Last-writer-wins здесь — правильная семантика.
- `launch → launch → consumer` (fan-out) — **теряет** первую сессию: её значения никто не прочитал, а второй launch их затёр.

Поэтому запрет ставится не на «дубль producer-а», а на **потерянную перезапись**: producer, чей выход перезаписан раньше, чем его кто-либо прочитал. Отличить одно от другого можно только зная порядок шагов — и он есть: пре-флайт `_execute_pipeline` уже обходит шаги по порядку. Проверка `_check_overwrites` вызывается из `build()` (раньше всего, ловит и dry-run инспектор `python -m ai_hats.pipeline.loader`) и из пре-флайта (для `Pipeline`, собранного в обход `build`). Судит по ключу, а не по шагу: producer может отдавать несколько ключей, часть из них прочитана, а непрочитанный всё равно теряется.

Ноль ложных срабатываний на всех 12 shipped-pipeline и обоих `presets`.

Рассмотрено и отвергнуто:

- **numbering** (`k#1`, `k#2` + голой `k` как алиас на последнего писателя, потребитель привязан к ближайшему предшествующему producer-у) — было реализовано и снято. Оно делало fan-out «выживающим, но по-прежнему неверным»: все потребители всё равно привязывались к последнему producer-у, `exit_code` в финальном состоянии оставался от последнего запуска (упавший первый рапортовался как успех), а прочитать `k#1` из YAML было нечем. Ценой были 127 строк в ядре, два новых латентных дефекта (коллизия ключей при вложенном `Pipeline`; перехват слота кастомным шагом, объявившим ключ с `#` в имени — `StepIO` не валидирует имена) и незадокументированная смена поведения. Документированное лекарство от fan-out — «переставь шаги» — это ровно то, к чему принуждает отказ, но за 25 строк вместо 127;
- **list-valued session keys** и **namespacing по step id** — меняют опубликованный контракт шага и `keys.py`-конвенцию «литерал ЕСТЬ объявление контракта».

Реализация — `_check_overwrites` в `src/ai_hats/pipeline/pipeline.py`.

## Update — HATS-1892 (2026-09-07) сигнатура `run` против объявления

§1 задаёт проекцию: шаг получает только объявленные ключи. Что `run` физически
можно вызвать этой проекцией — не проверялось нигде. `quorum_autoclose` объявлял
`requires={"layout"}`, а `run` требовал kw-only `project_dir`: проекция такой
kwarg собрать не может, и шаг падал `TypeError` на **каждом** запуске.

Дефект оказался невидим по двум причинам сразу. `failure_policy = "continue"`
(§5) ловил исключение и держал сессию зелёной, так что в exit code поломка не
проявлялась вообще. А тест шага звал `run(project_dir=...)` напрямую — ровно тот
вызов, который рантайм сделать не способен, — и поэтому был зелёным всё время,
пока автоклоуз в `finalize-hitl` не отработал ни разу.

Проверка строгая — против `requires`, а не `requires | optional`: `optional`
разрешает ключу отсутствовать, и обязательный параметр на таком ключе — тот же
самый `TypeError`, только на другом пути. Вызывается оттуда же, откуда
`_check_overwrites`: из `build()` (ловит и dry-run инспектор) и из пре-флайта
`_execute_pipeline`. Цена ошибки поднялась осознанно: раньше рассинхрон молча
терял один шаг, теперь падает сборка всего pipeline — но `BuildError` называет
шаг и ключ, а `wrap_runner` и так оборачивает finalize в свой `try/except`, так
что сессия пользователя не задета.

Ноль ложных срабатываний на всех 23 зарегистрированных step-id (22 класса) и
всех 12 shipped-pipeline.

Почему guard, а не только тест: `pipeline_steps/` и entry-point-группа
`ai_hats.steps` — открытые швы, и шаг из чужого пакета не виден ни одному тесту
этого репозитория. Тест поверх реестра оставлен как второй контур — он
покрывает id, которые ни один shipped-pipeline не подключает.

Реализация — `_check_run_signature` в `src/ai_hats/pipeline/pipeline.py`.

## Out of scope

- `ai-hats pipeline preproc ... execute ... postproc ...` bash-команда — Phase 5+ deliverable, не входит в HATS-261.
- YAML/манифестовая сериализация Pipeline — отвергнута (см. Alternative C).
- Перенос lifecycle (init_audit, hooks) в steps.
- Истинный reactive (Rx-streams, observers).
