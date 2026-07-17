# K6 comparison: старый `ai-hats task` vs новый `rack` (HATS-1026, фаза B)

Дата замера: 2026-07-17. Новый стек: `ai-hats-rack` 0.1.x + интеграторная проводка
`src/ai_hats/rack_wiring.py` / `rack_consumers.py` (K1–K5, K7 влиты в master).
Baseline (фаза A) — `baseline-report.md`; методика та же (§1.3 baseline), протокол —
чек-лист повторимости §8 baseline. Сырые логи фазы B:
`/tmp/rack-sandbox-metrics/phase-b/{calls.tsv,out/}`; раннер `measure-b.sh`.

## 1. Методика и отличия от baseline

- **Sandbox**: свежая распаковка pristine-снапшота в `/tmp/rack-sandbox-b/`
  (фаза A в `/tmp/rack-sandbox/` не тронута). Идентичное стартовое состояние:
  9 скопированных карточек HATS-*, git-репо, `task_prefix: SBX`.
- **Окружение**: то же, что §1.2 baseline — `cd /tmp/rack-sandbox-b`,
  `env -u AI_HATS_DIR -u AI_HATS_PROJECT_DIR`, выделенный
  `AI_HATS_SESSION_ID=sbx-phase-b-1`, `timeout 60`. Consumer-хуки не
  материализованы (паритет с baseline, решение №4 фазы A).
- **КЛЮЧЕВОЕ ОТЛИЧИЕ (и находка №1)**: голый `rack` CLI собирает ЧИСТОЕ ядро —
  `cli.py:_kernel` не передаёт ни одного подписчика. Ни plan-gate, ни scaffold,
  ни ownership/worktree/epic-automation через `rack transition` НЕ работают;
  полная сборка (`build_rack_kernel` + `consumer_subscribers`) существует только
  как библиотечная фабрика, вызываемая тестами. Для сопоставимости F1/F2 гонялись
  через минимальный driver `rack_driver.py` (~70 строк, в доме документов задачи):
  та же резолюция корня, тот же actor, те же error-поверхности, что у `rack
  transition`, — заменена только сборка ядра (bare → wired). Вызовы driver'а
  посчитаны как CLI-вызовы. Read-поверхности (show/doc/tree/context/ls/audit) и
  create/log — настоящий `rack` CLI (для этих глаголов сборка не влияет на
  семантику измеряемых флоу).
- Сравнение — по стоимости ЦЕЛИ (verification protocol плана): глаголы
  отличаются, цели фиксированы.

## 2. Флоу 1 — одиночная задача: create → … → done

| #   | Команда (цель)                                    | rc | Симв. | Заметка                                                                                              |
| --- | ------------------------------------------------- | -- | ----- | ---------------------------------------------------------------------------------------------------- |
| 001 | `rack create --help` (discovery нового CLI)       | 0  | 588   | все опции + `--json` видны сразу                                                                     |
| 002 | `rack create "Fix flaky retry loop…" …`           | 0  | 67    | → SBX-001                                                                                            |
| 003 | driver `transition SBX-001 plan`                  | 0  | 42    | scaffold пишет plan.md (путь не печатается)                                                          |
| 004 | driver `transition SBX-001 execute`               | 1  | 264   | **гейт-отказ**: типизированный, 1 строка, называет ВСЕ пустые секции + абсолютный путь плана         |
| —   | заполнение plan.md (Write, не CLI)                | —  | —     | те же 4 секции из 5                                                                                  |
| 005 | driver `transition SBX-001 execute`               | 0  | 39    | ретрай; **worktree создан, но путь НЕ напечатан** (delta теряется)                                   |
| 006 | `rack show SBX-001`                               | 0  | 594   | обходной вызов: путь worktree достаётся из work_log                                                  |
| —   | git-коммит работы в worktree (не CLI)             | —  | —     |                                                                                                      |
| 007 | `rack log SBX-001 "…"` (cwd = worktree)           | 1  | 32    | **спотыкание**: «Task 'SBX-001' not found» — резолвер взял worktree за корень, gitlink-hop'а нет     |
| 008 | `rack log SBX-001 "…"` (cwd = main)               | 0  | 84    | ретрай из основного чекаута                                                                          |
| 009 | driver `transition SBX-001 document`              | 0  | 43    |                                                                                                      |
| 010 | driver `transition SBX-001 review`                | 0  | 42    |                                                                                                      |
| 011 | driver `transition SBX-001 done` (cwd = worktree) | 1  | 32    | **спотыкание**: снова «not found» — HATS-788-guard недостижим через cwd-резолюцию                    |
| 012 | driver `transition SBX-001 done` (cwd = main)     | 1  | 2276  | **merge-ack гейт = СЫРОЙ traceback** `WorktreeMergeConsentError` (рецепт только в тексте исключения) |
| 013 | `AI_HATS_MERGE_ACK=1` driver `… done`             | 0  | 193   | merge + done ОДНИМ вызовом (в старом — 2: `wt merge` + `done`); drift-warning тот же                 |

**Итого F1:** 13 вызовов (baseline 11), 4 ошибки (2 гейта по дизайну / 2 спотыкания;
baseline 3/0), 3 ретрая, 4296 симв. (baseline 1945; 53% объёма — один consent-traceback).
Happy-path: 7 вызовов против 8 у старого (merge впитан в done), +1 обходной show,
пока delta с путём worktree не печатается.

## 3. Флоу 2 — эпик: 2 ребёнка, автоматика activate/advance

| #   | Команда (цель)                                | rc | Симв. | Заметка                                                                                           |
| --- | --------------------------------------------- | -- | ----- | ------------------------------------------------------------------------------------------------- |
| 014 | `rack create "Epic: sandbox telemetry…" …`    | 0  | 63    | → SBX-002                                                                                         |
| 015 | `rack create "…emitter" … --parent SBX-002`   | 0  | 57    | **`--parent` работает** (флаг, о который споткнулся baseline seq 013)                             |
| 016 | `rack create "…exporter" … --parent SBX-002`  | 0  | 50    | → SBX-004                                                                                         |
| 017 | `rack tree SBX-002`                           | 0  | 178   | **структура эпика одним вызовом** (baseline: только `list --search`, эпик «не знал» детей)        |
| 018 | driver `transition SBX-003 plan`              | 0  | 42    |                                                                                                   |
| —   | заполнение plan.md SBX-003 (не CLI)           | —  | —     |                                                                                                   |
| 019 | driver `transition SBX-003 execute`           | 0  | 39    | **авто-активация эпика НЕ напечатана** (baseline печатал делту инлайн)                            |
| 020 | `rack show SBX-002`                           | 0  | 457   | обходной вызов: активация видна в work_log с actor `rack:epic-automation` + reason                |
| 021 | driver `transition SBX-004 plan` (держа 003)  | 1  | 213   | **single-slot: типизированный отказ 213 симв.** (baseline: сырой traceback 2368 симв.)            |
| 022 | driver `transition SBX-003 done`              | 1  | 131   | самодокументирующийся FSM-отказ: легальные рёбра перечислены (паритет)                            |
| 023 | driver `transition SBX-003 document`          | 0  | 43    | recovery по подсказке                                                                             |
| 024 | driver `transition SBX-003 review`            | 0  | 42    |                                                                                                   |
| 025 | `AI_HATS_MERGE_ACK=1` driver `… SBX-003 done` | 0  | 38    | выученный порядок: ack сразу — 1 вызов; эпик корректно не двигается (второй ребёнок в brainstorm) |
| 026 | driver `transition SBX-004 plan`              | 0  | 42    | ретрай 021 — hold свободен                                                                        |
| —   | заполнение plan.md SBX-004 (не CLI)           | —  | —     |                                                                                                   |
| 027 | driver `transition SBX-004 execute`           | 0  | 39    |                                                                                                   |
| 028 | driver `transition SBX-004 document`          | 0  | 43    |                                                                                                   |
| 029 | driver `transition SBX-004 review`            | 0  | 42    |                                                                                                   |
| 030 | `AI_HATS_MERGE_ACK=1` driver `… SBX-004 done` | 0  | 38    | advance эпика тоже не напечатан                                                                   |
| 031 | `rack tree SBX-002`                           | 0  | 162   | верификация: эпик `review`, оба ребёнка `done`                                                    |
| 032 | `rack show SBX-002`                           | 0  | 571   | work_log: `Auto-advanced execute -> review (all children resolved (>=1 done))`                    |

**Итого F2:** 19 вызовов (baseline 21), 2 ошибки — обе типизированные отказы по
дизайну (baseline 4, из них 2 спотыкания), 1 ретрай, 2290 симв. (baseline 5860).
Кривая обучения: ребёнок №1 = 6 lifecycle-вызовов, №2 = 5 (baseline 9 → 6).
Автоматика журналируется на карточке эпика (actor + reason) и в audit.jsonl,
но инлайн-наблюдаемость потеряна — стоило 2 обходных вызова (020, 031/032).

## 4. Флоу 3 — документы: прямая запись, freeze, drift

| #   | Команда (цель)                                                         | rc | Симв. | Заметка                                                                                              |
| --- | ---------------------------------------------------------------------- | -- | ----- | ---------------------------------------------------------------------------------------------------- |
| 033 | `rack doc --help`                                                      | 0  | 401   | discovery: ls/freeze/rm; «write = plain file write» прямо в help                                     |
| —   | прямая запись `evidence-watcher.log` (не CLI)                          | —  | —     | тот же файл, что baseline добавлял через `attach add`                                                |
| 034 | `rack doc ls SBX-001`                                                  | 0  | 471   | **видим сразу**: полное имя + абсолютный путь + mtime + digest                                       |
| 035 | `rack doc freeze SBX-001 evidence-watcher.log`                         | 0  | 59    | evidence-пин; digest напечатан; аудит в work_log                                                     |
| —   | прямые записи `design-note.md` + `attachments/stray-blob.txt` (не CLI) | —  | —     | оба «невидимки» baseline                                                                             |
| 036 | `rack doc ls SBX-001`                                                  | 0  | 857   | **оба видимы** (baseline: design-note не видела НИ ОДНА поверхность, stray — только internal verify) |
| —   | модификация замороженного файла (не CLI)                               | —  | —     | drift-проба                                                                                          |
| 037 | `rack doc ls SBX-001`                                                  | 1  | 991   | **drift пойман**: `frozen ✗ modified` + новый digest + рецепт (`--refreeze`); rc=1                   |
| 038 | `rack doc freeze … --refreeze`                                         | 0  | 59    | осознанный re-pin; work_log хранит old → new digest                                                  |
| 039 | `rack show SBX-001`                                                    | 0  | 1532  | карточка + все документы с путями; дублей work_log/attachments больше нет                            |

**Итого F3:** 7 вызовов (= baseline), 1 «ошибка» (drift-детекция по дизайну; в baseline
verify выводил криптичный `+ name`), 0 ретраев, 4370 симв. (baseline 3505 — паритет;
объём вырос за счёт информативных листингов, а не шума). Дыра «прямая запись
невидима» закрыта полностью — включая корень задачи, а не только `attachments/`.

## 5. Флоу 4 — контекст задачи со связями (HATS-1026)

| #   | Команда (цель)           | rc | Симв. | Заметка                                                                                                        |
| --- | ------------------------ | -- | ----- | -------------------------------------------------------------------------------------------------------------- |
| 040 | `rack context HATS-1026` | 0  | 2674  | одним вызовом: карточка + описание + свои документы (пути/mtime) + parent с plan-путём + 4 depends_on со state |
| 041 | `rack doc ls HATS-1014`  | 0  | 1122  | 5 дизайн-доков эпика: **полные цитируемые имена** + пути + digest (baseline: имена обрезаны)                   |

**Итого F4: 2 вызова, 3796 симв. ≈ 0.95K токенов — против 10 вызовов, 209 851 симв.
≈ 52K токенов baseline (−98.2%).** Discovery-модель отдаёт пути — контент агент
Read'ит выборочно по абсолютным путям (в `--json` есть `size` для решения «читать ли»).
Принудительной инжекции 174K симв. вложений больше нет; полное чтение всех доков
остаётся доступным (те же байты через Read — по выбору агента). Своя plan.md
(дыра #050 baseline) закрыта дважды: путь в context + `--with plan`
(один вызов, 12 492 симв., потолок 16K/док с честной пометкой truncated).

## 6. Эксперимент HYP-028/029/030 — staleness контекста

Сценарий (measured, F4H, 4 вызова / 3157 симв.):

1. У done-задачи SBX-001 появляется `summary.md` («fixed with bounded backoff, max 5»).
2. `rack context SBX-001`: summary виден с mtime 07:53:35Z; карточка `updated` старше —
   summary свежий, ему можно верить.
3. Жизнь уходит вперёд: reopen done→execute (`--reason "prod incident"`) + log
   «bound raised 5 → 8».
4. Повторный `rack context SBX-001` — в ОДНОМ выводе: `state: execute`,
   `updated: 07:53:49Z`, свежий work_log про 5→8 и `summary.md` с mtime 07:53:35Z <
   updated. Агент видит, что summary СТАРШЕ последнего события жизненного цикла, и
   обязан перечитать первоисточники, а не действовать по hint'у.

Выводы по гипотезам:

- **HYP-028** (anchor-снипеты ловят drift): discovery-модель решает класс проблемы
  структурно — hint не содержит контента, стареть нечему; для evidence-байтов есть
  более сильный сигнал, чем снипет: frozen-pin digest (`frozen ✗ modified`, F3 037).
- **HYP-029** (дисциплина staleness-протокола): протокол выродился в «сравни mtime и
  updated в уже полученном выводе» — ноль дополнительных вызовов против
  baseline-модели, где `show` инжектил контент связей ВООБЩЕ без временных меток и
  сигнала staleness не существовало.
- **HYP-030** (fresh-hint speedup ~75 turns / ~30% output): полный turns-to-first-Edit
  эксперимент требует двух когорт живых прогонов агента — вне рамок этой фазы; но
  его экономика воспроизведена стоимостью контекста: 0.95K токенов discovery против
  52K контент-инжекции на ту же цель (F4), при сохранённом доступе к полному контенту.
  Косвенно: staleness-инцидентов в фазе B ноль (сигнал есть), в baseline-модели сигнал
  отсутствует по построению.
- Гранулярность mtime в текстовом выводе — минуты (тесный кейс неразличим); в
  `--json` — секунды (07:53:35Z), tie-break есть. В gaps.

## 7. `--json`: полнота покрытия

Пробы на всех использованных read-глаголах: `show`, `doc ls`, `tree`, `ls`, `context`,
`audit` — **6/6 валидный JSON** (карточка целиком, документы с path/mtime(sec)/digest/
frozen/size, дерево, журнал аудита со structured detail). Мутирующие глаголы
(`create/transition/log/link/unlink/doc freeze/doc rm`) декларируют `--json` в help;
typed-ошибки тоже сериализуются (`error.code` + детали). Baseline: **0/6**,
единственная структура — Python-repr raw-dict.

## 8. Сводная таблица

| Флоу                   |                           Вызовы old→new | Ошибки (гейт/спотык) old→new | Ретраи old→new |                 Симв. old→new |
| ---------------------- | ---------------------------------------: | ---------------------------: | -------------: | ----------------------------: |
| F1 одиночная задача    |                                  11 → 13 |            3 (3/0) → 4 (2/2) |          3 → 3 |                 1 945 → 4 296 |
| F2 эпик + автоматика   |                                  21 → 19 |            4 (2/2) → 2 (2/0) |        3+1 → 1 |                 5 860 → 2 290 |
| F3 документы           |                                    7 → 7 |            1 (1/0) → 1 (1/0) |          0 → 0 |                 3 505 → 4 370 |
| F4 контекст со связями |                              10(+1ф) → 2 |                        0 → 0 |    1 обход → 0 |               209 851 → 3 796 |
| **Всего (4 флоу)**     |                              **49 → 41** |                    **8 → 7** |    **7+1 → 4** | **221 161 → 14 752 (−93.3%)** |
| `--json` покрытие      | 0/6 → 6/6 проб (12/12 глаголов с флагом) |                              |                |                               |
| HYP-эксперимент (F4H)  |               — → 4 вызова / 3 157 симв. |                              |                |                               |

Качественные дельты: типизированные отказы вместо traceback'ов (single-slot
2 368 → 213 симв.), полные цитируемые имена доков, `tree` для структуры эпика,
audit.jsonl на каждой карточке (subscriber-outcomes каждого перехода видны
через `rack audit`). Регрессии UX: не печатаются delta/авто-переходы (worktree-путь,
активация эпика — +3 обходных вызова за две фазы флоу), нет gitlink-hop из worktree
(2 спотыкания F1), consent-гейт merge — сырой traceback через driver.

## 9. Паритет реализации (а)

- **Сьюты зелёные**: `packages/ai-hats-rack/tests` — **300 passed** (2.1s; включая
  портированный паритет-гейт K3: state/attach/propagate/ownership-классы);
  `packages/ai-hats-tracker/tests` — **99 passed** (1.7s; старый стек зелёный,
  feature-freeze не нарушен). Логи: `/tmp/rack-sandbox-metrics/phase-b/*.log`.
- **LOC (wc -l / кодовые без пустых и комментариев)**:
  - старое ядро `state.py`: **1 279 / 1 007**;
  - rack-ядро K1 (kernel+dispatch+fsm+models+events + fsm.yaml): **1 116 / 888**;
  - экстеншены + интеграторная проводка (extensions/* + rack_wiring + rack_consumers):
    1 159 raw — то, что в `state.py` было вшито (worktree/ownership/автоматика/гейт),
    теперь подписчики;
  - весь rack-пакет (с CLI/docstore/linked/audit/resolver): 3 803 raw — против
    старого стека, где эти поверхности тоже живут в отдельных модулях.
- **Правки ядра на новую дисциплину — 0**: K2 (docstore), K3 (worktree/ownership/
  scaffold/gate/automation), K4 (hooks), K5 (link/context) не тронули ни один из
  пяти файлов ядра ни одним коммитом (git log --follow). Единственная пост-K1
  правка — K7 (HATS-1025): аддитивный journal-seam, заложенный в K1 по решению
  супервизора (in-charter, не «новая дисциплина»).

## 10. Known gaps (перед cutover)

Из стэша решений (`/tmp/hats-1014-questions.md`) + находки фазы B:

1. **Нет wired-CLI входа** (находка №1 фазы B): `rack` CLI = чистое ядро;
   `build_rack_kernel` + `consumer_subscribers` вызываются только тестами и
   driver'ом замера. Продакшен-вход обязан также: печатать work_log-delta
   (путь worktree) и авто-переходы автоматики; вызывать `views.refresh()` после
   create (форк K3 №7); типизированно обрабатывать интеграторные исключения
   (`WorktreeMergeConsentError` — паритет HATS-1019, `WorktreeStateLostError`).
2. **Вызов из worktree сломан**: walk-up-резолвер принимает worktree за корень
   (ai-hats.yaml в дереве, `.agent/` — нет) → вводящий в заблуждение
   «Task not found»; gitlink-hop старого CLI отсутствует, HATS-788-guard
   недостижим через cwd (сам guard запинен портированным тестом).
3. **`RackRoot` при явном override** (`--tasks-dir`/`RACK_TASKS_DIR`) ставит
   `project_dir = caller_cwd` — неверный якорь для worktree-эффектов wired-сборки.
4. **Ownership pid-aliasing** (живой инцидент реклейма этой задачи): сессии под
   одним долгоживущим harness-процессом делят `AI_HATS_ROOT_PID` → hold мёртвой
   сессии выглядит живым, реклейм HATS-955 отказан; потребовалась ручная
   `ownership.release()`. Нужен per-session liveness-ключ и/или CLI-верб
   `release/stop`.
5. **`rack update` отсутствует**: поля карточки (priority/reviewer/description/…)
   новым CLI не правятся — до генерализации живёт только в старом `task update`.
6. **rack не в release workflow** (стэш K4 №12, K1 №16): pre-push gate (e2e+smoke)
   не гоняет rack-сьюты (так красный master уже въезжал); wheel/versioning rack
   не в релизной автоматике; install-dep уже добавлен (K1 №16, частичная отмена
   форка №14).
7. Мелочи: mtime в текстовом выводе — минуты (в `--json` секунды); `context` не
   перечисляет attachments родителя (один лишний `doc ls`); `hyp`/`proposal`/
   `close`/`plan-extract`/`sync` — только в старом CLI до отдельного эпика
   генерализации.

## 11. PROP-070: план миграции on-disk артефактов (набросок)

1. Короткое write-freeze окно на живом бэклоге (feature-freeze уже действует).
2. Инвентаризация: `rack ls` + `rack doc ls` по всем карточкам — формат
   `task.yaml`/layout читается rack'ом без конвертации (проверено фазой B на
   живых копиях, включая 64K legacy-attachments HATS-841).
3. `task.yaml` — без миграции (anchor-поля совместимы; старый `attachments:`
   манифест едет через extras verbatim).
4. Legacy-манифесты вложений: либо разово сконвертировать в frozen-пины
   (digest уже есть), либо оставить как есть — docstore видит файлы и без пинов;
   решение на ревью cutover.
5. Derived views: однократный `views.refresh()` для STATE.md; удалить
   legacy `.agent/backlog.md`.
6. `ownership.json` — совместим (wired-сборка уже использует боевой реестр);
   fix gap №4 ДО cutover.
7. `audit.jsonl` у старых карточек отсутствует — zero-events-детект K7 помечает
   это как pre-K7 историю (документированное ожидание, не ошибка).
8. Consumer-канал: union-материализация уже в master (K4); переключить исполнение
   на in-process hook-runner wired-входа, git-dispatcher вывести после смоука.
9. CLI-cutover: скиллы/доки на rack-глаголы; старый `ai-hats task` остаётся для
   hyp/proposal/update до эпика генерализации; `attach`-глаголы — deprecated-шимы
   с указателем на `doc`.
10. Ревизия PROP на ревью cutover: реанимировать PROP-013/010/014; закрыть
    PROP-060/025/027 по refuted-HYP (из описания задачи).

## 12. Рекомендация: **доработка → cutover** (не откат)

Ядро и экстеншены воспроизводят рубцовую ткань старого трекера (300 зелёных
тестов, включая портированный паритет-гейт; все гейты/автоматика/ownership/worktree
наблюдались живьём в флоу), а UX-выигрыш решающий: −93% объёма вывода на тех же
флоу, контекст со связями −98.2%, fs-as-truth закрывает класс «невидимых» файлов,
`--json` везде, типизированные отказы вместо traceback'ов. Откатывать нечего —
паритет достигнут; но резать cutover сегодня нельзя: без wired-CLI входа новый стек
в продакшене буквально не исполняет ни один гейт.

**Блокирующие условия cutover (C1–C5):**

- **C1** — wired-CLI вход (gap №1) с печатью delta/авто-переходов и typed-обработкой
  wt-исключений;
- **C2** — резолюция корня из task-worktree (gitlink-hop или явный контракт, gap №2/3);
- **C3** — `rack update` либо задокументированное сосуществование со старым CLI
  для полей карточки (gap №5);
- **C4** — ownership: per-session liveness + release-верб (gap №4);
- **C5** — rack в release workflow: pre-push gate гоняет rack-сьюты, wheel в
  релизной автоматике (gap №6).

Некритично (после cutover): секунды в текстовом mtime, attachments родителя в
`context`, инлайн-печать автоматики поверх C1-минимума.

## 13. Артефакты фазы B

- Sandbox: `/tmp/rack-sandbox-b/` (мутирован флоу; pristine остаётся
  `/tmp/rack-sandbox-pristine.tar.gz`); фаза A нетронута в `/tmp/rack-sandbox/`.
- Логи: `/tmp/rack-sandbox-metrics/phase-b/{calls.tsv,out/,rack-suite.log,tracker-suite.log}`;
  раннер `measure-b.sh`; driver — `rack_driver.py` в доме документов HATS-1026.
- Решения фазы B: `/tmp/hats-1014-questions.md` § HATS-1026 (phase B).
