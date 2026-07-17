# K6 baseline: старый `ai-hats task` CLI на sandbox-копии (HATS-1026, фаза A)

Дата замера: 2026-07-17. CLI: `ai-hats 0.13.3.dev78+g6c614965a` (живой venv проекта).
Фаза B повторяет этот же протокол на новом CLI `rack` и строит сравнительные таблицы.

## 1. Методика

### 1.1 Sandbox

- Путь: `/tmp/rack-sandbox/` — минимальный корень, который старый CLI признаёт проектом:
  резолюция корня = walk-up от CWD до ближайшего предка с `.agent/`
  (`src/ai_hats/cli/_helpers.py:_project_dir`), затем `ai-hats.yaml` задаёт
  `ai_hats_dir` и `task_prefix`.
- Состав корня: `ai-hats.yaml` (`task_prefix: SBX` — детерминированные id новых задач,
  независимые от роста живого бэклога), `.gitignore` (`.agent/`), `README.md`;
  git-репозиторий (branch `master`, 1 initial commit) — без git не срабатывает
  worktree-автоматика, существенная часть стоимости флоу.
- Скопировано из живого бэклога (СВЕЖИЕ копии, только чтение источника):
  - эпик с детьми: `HATS-1014` (+ 5 attachments-дизайндоков) и все дети
    `HATS-1020…HATS-1026`;
  - задача с depends_on: `HATS-1026` (depends_on: 1021–1024, в составе семьи эпика);
  - одиночная задача с attachments: `HATS-841` (без parent_task, done, 64K attachments).
- Pristine-снапшот ДО прогона флоу: `/tmp/rack-sandbox-pristine.tar.gz` — фаза B
  разворачивает его и получает идентичное стартовое состояние. Текущий (мутированный
  флоу) sandbox оставлен на месте.

### 1.2 Окружение запуска

Каждый вызов: `cd /tmp/rack-sandbox && env -u AI_HATS_DIR -u AI_HATS_PROJECT_DIR
AI_HATS_SESSION_ID=sbx-baseline-1 timeout 60 <команда>`.
`AI_HATS_DIR/PROJECT_DIR` снимаются (пины харнесса указывают на живой проект; pair-scope
HATS-897 их и так игнорирует, но снятие убирает warn-шум). Session identity выделенная —
ownership single-slot per session. Consumer-хуки (dispatcher.sh) в sandbox НЕ
материализованы: baseline меряет ядро CLI; фаза B должна гоняться в тех же условиях.

### 1.3 Метрики

- Сырой лог: `/tmp/rack-sandbox-metrics/baseline/calls.tsv` + полные выводы в
  `/tmp/rack-sandbox-metrics/baseline/out/NNN-<flow>.txt`; раннер —
  `/tmp/rack-sandbox-metrics/measure.sh`.
- **CLI-вызов** — каждый запуск `ai-hats`, включая неудачные и help.
- **Ошибка** — exit code ≠ 0. Классы: «гейт по дизайну» (плановый отказ) vs
  «спотыкание агента» (неверный флаг/глагол/порядок).
- **Ретрай** — повтор вызова с той же целью после ошибки/обходной вызов.
- **Объём вывода** — символы stdout+stderr (прокси токенов, ≈4 симв./токен).
- Не-CLI действия (запись plan.md, прямая запись файлов, git-коммиты «работы»)
  считаются отдельно и не входят в число CLI-вызовов.

## 2. Флоу 1 — одиночная задача: create → … → done (гейт-отказ включён)

| #   | Команда                                                                             | rc | Симв. | Заметка                                                                                  |
| --- | ----------------------------------------------------------------------------------- | -- | ----- | ---------------------------------------------------------------------------------------- |
| 001 | `task create "Fix flaky retry loop in sync watcher" -d "…" -p medium --tag sandbox` | 0  | 68    | → SBX-001                                                                                |
| 002 | `task transition SBX-001 plan`                                                      | 0  | 128   | печатает путь scaffold                                                                   |
| 003 | `task transition SBX-001 execute`                                                   | 2  | 301   | **гейт-отказ (план пуст)**: перечислены пустые секции + путь + рецепт                    |
| —   | заполнение plan.md (Write, не CLI)                                                  | —  | —     | требуются 4 секции из 5 (Approach & counter гейтом не проверяется)                       |
| 004 | `task transition SBX-001 execute`                                                   | 0  | 242   | ретрай; worktree + branch, печатает `cd <путь>`                                          |
| —   | git-коммит работы в worktree (не CLI)                                               | —  | —     |                                                                                          |
| 005 | `task log SBX-001 "…"` (cwd = worktree)                                             | 0  | 85    | работает изнутри worktree (hop по gitlink)                                               |
| 006 | `task transition SBX-001 document` (cwd = worktree)                                 | 0  | 35    | тоже работает изнутри                                                                    |
| 007 | `task transition SBX-001 review`                                                    | 0  | 33    |                                                                                          |
| 008 | `task transition SBX-001 done` (cwd = worktree)                                     | 1  | 314   | **guard**: teardown изнутри worktree запрещён; внятный рецепт (`cd main`, `wt exec/env`) |
| 009 | `task transition SBX-001 done` (cwd = main)                                         | 1  | 514   | **merge-ack гейт**: нужен `AI_HATS_MERGE_ACK=1 wt merge`; рецепт напечатан дословно      |
| 010 | `AI_HATS_MERGE_ACK=1 wt merge task/sbx-001`                                         | 0  | 176   | + шумный drift-warning (offline fetch origin)                                            |
| 011 | `task transition SBX-001 done`                                                      | 0  | 49    | «Worktree merged»                                                                        |

**Итого F1:** 11 CLI-вызовов (min happy-path ≈ 8), 3 ошибки (все — гейты по дизайну),
3 ретрая, 1945 симв. вывода, 2 не-CLI действия (plan.md, git-коммит).
Путь execute→done стоит минимум 5 вызовов (document, review, merge-ack merge, done)
и один HITL-ack.

## 3. Флоу 2 — эпик: 2 ребёнка, автоматика activate/advance

| #   | Команда                                            | rc | Симв. | Заметка                                                                                                                                                              |
| --- | -------------------------------------------------- | -- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 012 | `task create "Epic: sandbox telemetry pipeline" …` | 0  | 64    | → SBX-002 (эпиком станет по факту детей)                                                                                                                             |
| 013 | `task create "…emitter" … --parent SBX-002`        | 2  | 167   | **спотыкание**: флага `--parent` нет; click подсказывает `--parent-task`                                                                                             |
| 014 | `task create "…emitter" … --parent-task SBX-002`   | 0  | 58    | ретрай → SBX-003                                                                                                                                                     |
| 015 | `task create "…exporter" … --parent-task SBX-002`  | 0  | 51    | → SBX-004                                                                                                                                                            |
| 016 | `task show SBX-002 --short`                        | 0  | 265   | **на карточке эпика нет ни признака эпика, ни списка детей**                                                                                                         |
| 017 | `task transition SBX-003 plan`                     | 0  | 128   |                                                                                                                                                                      |
| —   | заполнение plan.md SBX-003 (не CLI)                | —  | —     |                                                                                                                                                                      |
| 018 | `task transition SBX-003 execute`                  | 0  | 341   | + напечатан делта-автопереход: `SBX-002 brainstorm → execute (activated)`                                                                                            |
| 019 | `task transition SBX-004 plan`                     | 1  | 2368  | **спотыкание+дизайн**: `OwnershipRefused: session still holds ['SBX-003']` — сырой traceback на 2.4K симв.; параллельная работа над детьми в одной сессии невозможна |
| 020 | `task transition SBX-003 done`                     | 1  | 111   | **самодокументирующийся отказ FSM**: печатает легальные рёбра `['execute','document','blocked','failed','cancelled']`                                                |
| 021 | `task transition SBX-003 document`                 | 0  | 35    | recovery по подсказке                                                                                                                                                |
| 022 | `task transition SBX-003 review`                   | 0  | 33    |                                                                                                                                                                      |
| 023 | `task transition SBX-003 done`                     | 1  | 514   | merge-ack гейт (как F1)                                                                                                                                              |
| 024 | `AI_HATS_MERGE_ACK=1 wt merge task/sbx-003`        | 0  | 176   |                                                                                                                                                                      |
| 025 | `task transition SBX-003 done`                     | 0  | 49    | эпик не двигается (второй ребёнок ещё brainstorm) — корректный no-op                                                                                                 |
| 026 | `task transition SBX-004 plan`                     | 0  | 128   | ретрай 019 — теперь hold свободен                                                                                                                                    |
| —   | заполнение plan.md SBX-004 (не CLI)                | —  | —     |                                                                                                                                                                      |
| 027 | `task transition SBX-004 execute`                  | 0  | 242   |                                                                                                                                                                      |
| 028 | `task transition SBX-004 document`                 | 0  | 35    | выученный порядок: merge ДО done                                                                                                                                     |
| 029 | `task transition SBX-004 review`                   | 0  | 33    |                                                                                                                                                                      |
| 030 | `AI_HATS_MERGE_ACK=1 wt merge task/sbx-004`        | 0  | 176   |                                                                                                                                                                      |
| 031 | `task transition SBX-004 done`                     | 0  | 136   | + делта: `SBX-002 execute → review (all children resolved)`                                                                                                          |
| 032 | `task show SBX-002 --short`                        | 0  | 750   | автоматика видна и в work_log эпика (продублирована raw-dict'ом и форматированной секцией)                                                                           |

**Итого F2:** 21 CLI-вызов, 4 ошибки (2 спотыкания: флаг, ownership; 1 неверный
переход с внятной подсказкой; 1 гейт merge-ack), 3 ретрая + 1 recovery другим глаголом,
5860 симв., 2 не-CLI plan-заполнения + 2 git-коммита.
Кривая обучения видна: ребёнок №1 = 9 вызовов, ребёнок №2 = 6 (naive→learned −33%).
Автоматика эпика печатается инлайн-делтами и дублируется в work_log — наблюдаемость
хорошая; discovery структуры эпика (список детей) — только через `task list --search`.

## 4. Флоу 3 — вложения + прямая запись файла

| #   | Команда                                                           | rc | Симв. | Заметка                                                                                                                                 |
| --- | ----------------------------------------------------------------- | -- | ----- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 033 | `task attach --help`                                              | 0  | 446   | discovery глаголов: add/list/remove/show/verify; `verify` помечен «Internal:»                                                           |
| 034 | `task attach add SBX-001 /tmp/…/evidence-watcher.log`             | 0  | 66    | печатает digest                                                                                                                         |
| 035 | `task attach list SBX-001`                                        | 0  | 784   | rich-таблица                                                                                                                            |
| 036 | `task attach show SBX-001 evidence-watcher.log`                   | 0  | 195   | контент в stdout, без пути                                                                                                              |
| —   | прямая запись `tasks/SBX-001/design-note.md` (не CLI)             | —  | —     | файл в корень задачи                                                                                                                    |
| —   | прямая запись `tasks/SBX-001/attachments/stray-blob.txt` (не CLI) | —  | —     | блоб мимо манифеста                                                                                                                     |
| 037 | `task attach list SBX-001`                                        | 0  | 784   | **оба невидимы**                                                                                                                        |
| 038 | `task attach verify SBX-001`                                      | 1  | 17    | видит только stray в `attachments/`: вывод `+ stray-blob.txt` (криптично); `design-note.md` в корне задачи не видит НИКАКАЯ поверхность |
| 039 | `task show SBX-001 --short`                                       | 0  | 1213  | attachments печатаются raw-dict'ом (имя+digest, БЕЗ пути); design-note.md отсутствует                                                   |

**Итого F3:** 7 CLI-вызовов, 1 «ошибка» (verify rc=1 — детекция расхождения, по
дизайну), 0 ретраев, 3505 симв., 2 прямые записи.
Ответ на вопрос протокола: прямо записанный файл в `tasks/<ID>/` **не видит ни одна
поверхность CLI**; блоб в `attachments/` — только «internal» `verify` (терсый плюсик).
Это и есть дыра, которую закрывает rev4 fs-as-truth store (K2).

## 5. Флоу 4 — сбор полного контекста задачи со связями (HATS-1026)

Цель: восстановить всё о задаче — карточка, связи (parent/depends_on), план,
дизайн-вложения родителя.

| #   | Команда                                             | rc | Симв.  | Заметка                                                                                                                      |
| --- | --------------------------------------------------- | -- | ------ | ---------------------------------------------------------------------------------------------------------------------------- |
| 040 | `task show HATS-1026`                               | 0  | 15 272 | карточка + Linked context: тела parent + plan.md родителя + тела 4 depends_on (контент-инжекция, ~3.8K токенов за один show) |
| 041 | `task attach list HATS-1026`                        | 0  | 17     | пусто                                                                                                                        |
| 042 | `task attach list HATS-1014`                        | 0  | 2 193  | **имена в таблице обрезаны** (`hats-1014-epic-v2…`) — нецитируемы для `attach show`                                          |
| 043 | `task list --search HATS-1026`                      | 0  | 1 283  | реверс-связи; вернул parent+self, тип связи не помечен                                                                       |
| 044 | `task show HATS-1014 --short`                       | 0  | 6 720  | обходной вызов: полные имена вложений достаются из raw-dict                                                                  |
| 045 | `task attach show HATS-1014 hats-1014-epic-v2.md`   | 0  | 26 959 |                                                                                                                              |
| 046 | `task attach show HATS-1014 hats-1014-flows.md`     | 0  | 17 703 |                                                                                                                              |
| 047 | `task attach show HATS-1014 hats-1014-fsm.md`       | 0  | 18 982 |                                                                                                                              |
| 048 | `task attach show HATS-1014 hats-1014-incidents.md` | 0  | 51 742 |                                                                                                                              |
| 049 | `task attach show HATS-1014 hats-1014-hyps.md`      | 0  | 59 276 |                                                                                                                              |
| 050 | чтение `tasks/HATS-1026/plan.md` (файл, не CLI)     | 0  | 9 704  | собственный plan.md задачи show НЕ печатает; путь известен только по конвенции                                               |

**Итого F4:** 10 CLI-вызовов + 1 чтение файла, 0 ошибок, 1 обходной вызов (044),
**209 851 симв. ≈ 52K токенов** на полное восстановление контекста, из них
174 662 (83%) — контент 5 вложений, влитый в stdout без опции «дай пути».
Baseline для HYP-028/029/030: это стоимость контент-модели; discovery-модель
(пути+свежесть) нового CLI меряется в фазе B на этом же кейсе, плюс эксперименты
turns-to-first-Edit / staleness-инциденты (hyps-док §4).

## 6. `--json`: полнота покрытия

Пробы (все — click parse error до мутации, безопасно): `show`, `list`, `attach list`,
`transition`, `log`, `create`. **Поддержка `--json`: 0/6.** Единственный
структурированный вывод — raw-dict в `show` (Python-repr, не JSON).

## 7. Качественные находки (где спотыкается агент)

1. **`OwnershipRefused` = сырой traceback** (~2.4K симв.) — и при параллельных детях
   эпика (seq 019), и при наследовании session id субагентом (живой бэклог,
   pre-flow). Single-slot ownership на сессию — структурный запрет параллельной
   работы; сообщение не рендерится как дружелюбный отказ и не предлагает выход.
2. **`--json` нет нигде (0/6)**; rich-таблицы враждебны к цитированию: имена вложений
   и заголовки обрезаются (`hats-1014-epic-v2…`) — агент не может скопировать имя в
   следующий вызов; обход — raw-dict из `show --short` или прямой `ls` по конвенции.
3. **Стоимость контекста — 52K токенов**, 83% из которых — принудительная
   контент-инжекция вложений (`attach show` без варианта «путь»); `show` инжектит
   тела связей (15K симв.) без опции отказаться от контента, кроме `--short`
   (который теряет ВСЁ, включая список детей эпика — его нет вообще).
4. **Дорога к done — три гейта подряд** (invalid-transition → document/review;
   merge-ack; in-worktree guard): каждый отказ печатает точный рецепт (хорошо!), но
   happy-path агент проходит done только со второй-третьей попытки; выученный порядок
   (merge до done) снижает стоимость ребёнка эпика с 9 до 6 вызовов.
5. **Файл, записанный напрямую в `tasks/<ID>/` — невидимка**: ни show, ни attach list;
   блоб в `attachments/` ловит только «Internal» `verify` терсым `+ name` (rc=1).
   Собственный plan.md задачи тоже не отдаётся ни одной командой (только parent plan
   внутри Linked context) — путь угадывается по конвенции.
6. Мелочи: `--parent` vs `--parent-task` (спасает подсказка click); work_log и
   attachments в `show` печатаются дважды (raw-dict + форматированная секция);
   `wt merge` в offline-репо шумит drift-warning'ом; карточка эпика не знает, что она
   эпик.

## 8. Чек-лист повторимости для фазы B

1. Развернуть pristine: `tar xzf /tmp/rack-sandbox-pristine.tar.gz -C /tmp` (поверх
   удалённого текущего) ЛИБО замерять на текущем мутированном sandbox только флоу 4
   (read-only). Для флоу 1–3 нужен pristine (SBX-00X уже существуют).
2. Окружение: `cd /tmp/rack-sandbox`, `env -u AI_HATS_DIR -u AI_HATS_PROJECT_DIR`,
   выделенный `AI_HATS_SESSION_ID`, `timeout 60`.
3. Прогнать те же ЦЕЛИ (не буквальные команды — глаголы нового CLI могут отличаться):
   - F1: create → plan → execute с пустым планом (зафиксировать отказ) → заполнить →
     execute → log из worktree → document → review → done (зафиксировать все гейты);
   - F2: эпик + 2 ребёнка; попытка второго ребёнка при held-первом; довести обоих;
     зафиксировать каждую автоматику (activate/advance) и её наблюдаемость;
   - F3: attach add/list/show/verify + прямая запись в `tasks/<ID>/` и в
     `attachments/` — что видят поверхности (ожидание rev4: видят всё);
   - F4: полный контекст HATS-1026 — сколько вызовов/симв. до «агент знает всё»;
     отдельно замерить discovery-режим (пути) vs контент; эксперименты HYP-030
     (turns-to-first-Edit с контекстом/без) и HYP-028/029 (staleness: перед замером
     обновить живые карточки → протухли ли скопированные hint'ы).
4. Считать той же методикой (§1.3): все вызовы включая help и неудачные; ошибки с
   классификацией гейт/спотыкание; ретраи; символы stdout+stderr; для F4 — суммарные
   символы восстановления контекста.
5. Пробы `--json` на каждом использованном глаголе нового CLI (метрика полноты).
6. Сравнительная таблица per-flow: вызовы / ошибки / ретраи / символы / гейты,
   old vs new + качественные дельты; референс-числа baseline — §9.

## 9. Сводка baseline

| Флоу                   |  CLI-вызовов | Ошибок (гейт/спотыкание) | Ретраев | Симв. вывода |
| ---------------------- | -----------: | -----------------------: | ------: | -----------: |
| F1 одиночная задача    |           11 |                  3 (3/0) |       3 |        1 945 |
| F2 эпик + автоматика   |           21 |                  4 (2/2) |     3+1 |        5 860 |
| F3 вложения            |            7 |                  1 (1/0) |       0 |        3 505 |
| F4 контекст со связями | 10 (+1 файл) |                        0 | 1 обход |      209 851 |
| `--json` пробы         |            6 |                        6 |       — |         ~900 |
| **Всего (флоу)**       |       **49** |                    **8** | **7+1** |  **221 161** |

Артефакты: sandbox `/tmp/rack-sandbox/`; pristine `/tmp/rack-sandbox-pristine.tar.gz`;
сырые логи `/tmp/rack-sandbox-metrics/baseline/{calls.tsv,out/}`; раннер
`/tmp/rack-sandbox-metrics/measure.sh`; решения — `/tmp/hats-1014-questions.md`
§ HATS-1026.
