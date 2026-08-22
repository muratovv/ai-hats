# Tests layout

Тесты разложены по физическим директориям, отражающим уровень изоляции.
Маркеры (`integration`, `smoke`) задают временной/ресурсный профиль и
определены в `pyproject.toml`.

## Directory layout

| Path                 | Layer                   | Что внутри                                                                                                                                                                                                                                                                                                                                      |
| -------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/` (root)      | **unit**                | Изолированные тесты модулей: monkeypatch, `CliRunner.invoke()`, in-process. Большинство файлов. Default `pytest`.                                                                                                                                                                                                                               |
| `tests/sessions/`    | **session-integration** | CLI-команда собирает и гоняет свой пайплайн по-настоящему; runner-граница (`WrapRunner` / `SubAgentRunner` / `SessionReviewRunner` / `subprocess.Popen`) застаблена через `conftest.py::mock_runners`. Никакого реального Claude/shell subprocess. Звалось `tests/pipeline/`, хотя ни один pipeline-модуль оттуда не импортируется (HATS-1783). |
| `tests/e2e/`         | **end-to-end**          | Real subprocess CLI: реальный `bash`, реальный `pip`, реальный `ai-hats` binary. Маркер `integration` обязателен. Медленные (~60s+), скипаются в обычном прогоне.                                                                                                                                                                               |
| `tests/e2e_harness/` | **unit**                | Тесты САМОЙ e2e-обвязки: `_helpers/*`, `tests/e2e/conftest.py`, AST-скан каталога `tests/e2e/`. Подпроцессов не порождают — потому лежат не в `tests/e2e/` (HATS-1600). Локальный `conftest.py` кладёт `tests/e2e` в `sys.path` ради `_helpers`.                                                                                                |
| `tests/smoke/`       | **smoke**               | Lightweight pre-commit gate. Маркер `smoke`. Канарейка для интеграционных задач.                                                                                                                                                                                                                                                                |
| `tests/fixtures/`    | (data)                  | Sanitised input для регрессионных прогонов (`real_backlog/`, `real_session/`).                                                                                                                                                                                                                                                                  |

Тесты **области** (ADR-0026 D5) лежат не здесь, а внутри самой области —
`src/ai_hats/<area>/tests/`, со своим `conftest.py`. Туда едет тест, которому
хватает поверхности области, стандартной библиотеки и фикстур (D7); тест,
поднимающий две области, остаётся в общем наборе. Каждая такая папка
перечисляется отдельной строкой в `testpaths` и исключается из колеса (D11).

## Markers

| Marker        | Defined in       | Покрытие                                                                               | Когда добавлять                                                                          |
| ------------- | ---------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `integration` | `pyproject.toml` | Тесты, спавнящие real subprocess или real PTY. Всегда — для всего, что в `tests/e2e/`. | Файл запускает реальный shell/pip/ai-hats binary или real PTY.                           |
| `smoke`       | `pyproject.toml` | Lightweight тесты для pre-commit gate на integration-эпиках.                           | Тест должен прогоняться очень быстро (< 1s) перед каждым коммитом интеграционной задачи. |

Default `pytest` прогоняет unit + pipeline. CI прогоняет с
`pytest -m "not integration"` (см. `.github/workflows/ci.yml`). Чтобы
прогнать e2e: `pytest -m integration` или `pytest tests/e2e/`.

## Гайдлайны для новых тестов

1. **Куда класть:**
   - Тесту достаточно in-process Python + `CliRunner` → `tests/`.
   - Тесту хватает поверхности одной области → `src/ai_hats/<area>/tests/` (ADR-0026 D5/D7).
   - Тест поднимает сессию целиком, но мокает runner-границу → `tests/sessions/`.
   - Тест запускает реальный `ai-hats` через subprocess → `tests/e2e/` + `@pytest.mark.integration`.
   - Предмет теста — сама e2e-обвязка (`_helpers`, e2e-`conftest.py`), а подпроцесса нет → `tests/e2e_harness/`. Маркер НЕ ставить: непомеченный файл в `tests/e2e/` невидим и стадии `integration` (игнорит по пути), и тиру `e2e` (селектит по маркеру).
2. **Имена файлов:** `test_<module>_<aspect>.py`. Префикс `test_e2e_*` в root зарезервирован за анти-паттерном — настоящие e2e живут в `tests/e2e/`.
3. **Stub vs real:** если тест мокает то, что объявлено проверяет (sub­process для bash-скрипта, pip для launcher-flow), он тавтологичен — повышайте уровень в `tests/e2e/` или удаляйте.
