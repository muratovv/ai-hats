# Rule: E2E Test Gate for CLI / Shell / Pip Changes

A task may not transition to `done` if it changed any of the **trigger surface**
below without including at least one e2e test under `tests/e2e/` that exercises
the real command chain.

## 1. Trigger surface

The rule fires if the task changed any of:

- `src/ai_hats/cli/**/*.py` — click commands, command nesting, CLI args/flags.
- `scripts/*.sh` — shell scripts (`install-launcher.sh`, `bootstrap.sh`, etc.).
- `src/ai_hats/_bootstrap.py`, `src/ai_hats/cli/maintenance.py` — pip install / launcher / venv flow.
- `[project.scripts]` block in `pyproject.toml` — new or renamed entry-points.
- Anything else crossing an external contract: PEP 508 URL forms, click nesting, shell quoting, venv invocation.

**Does not trigger:** internal Python modules (storage, parsing, business logic), docs, tests-only changes, version bump.

## 2. What counts as an e2e test

A test passes the gate only if **all** of these hold:

- Lives under `tests/e2e/` (the dedicated real-subprocess CLI layer — see `tests/README.md`).
- Marked `@pytest.mark.integration`.
- Spawns a **real** subprocess chain: real `bash`, real `pip install`, real `ai-hats` binary. No `MagicMock`, no `monkeypatch` on `subprocess.Popen`, no `CliRunner.invoke()`.
- Asserts observable end-to-end side effects (exit codes, files on disk, captured output) — not internal call counts.

Pipeline-integration tests (`tests/pipeline/`) and in-process `CliRunner` tests do **not** satisfy this rule, regardless of marker.

## 3. Plan-stage requirement

When the trigger fires, the task plan must explicitly name the e2e test(s) it will add — file path and what it asserts. "Will add e2e coverage" is not sufficient.

## 4. Review-stage check

The reviewer verifies before approving `done`:

- The named e2e test exists at the declared path.
- `pytest -m integration tests/e2e/` passes locally.
- The test would fail if the change under review were reverted (i.e. it actually exercises the new behaviour, not just lives alongside it).

If any check fails, the card returns to `execute`.

## 5. Source

PROP-031 (accepted). Motivation: HATS-333 epic shipped two production bugs (PEP 508 rejection for local-path `ai-hats @ /path`, click command-nesting drift) past `done` because the unit suite stubbed the very contracts the change broke. The e2e gate is the cheapest reliable catch for this class of failure.
