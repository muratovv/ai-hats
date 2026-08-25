# Rule: E2E Test Gate for CLI / Shell / Pip Changes

A task may not transition to `done` if it changed any of the **trigger surface**
below without including at least one e2e test under `tests/e2e/` that exercises
the real command chain.

## 1. Trigger surface

**The invariant:** the rule fires on any change to code that runs *outside* the
Python process — an entry point, a shell script, the install/venv flow, or any
script the assembler installs as a hook. That is where the contract lives at a
subprocess boundary the unit suite cannot observe. The list below names the
current surfaces; a path that is not on it but crosses the same boundary
triggers the gate all the same.

Concretely, the rule fires if the task changed any of:

- `src/ai_hats/cli/**/*.py` — click commands, command nesting, CLI args/flags.
- `scripts/*.sh` — shell scripts (`install-launcher.sh`, `bootstrap.sh`, etc.).
- `src/ai_hats/_bootstrap.py`, `src/ai_hats/cli/maintenance.py` — pip install / launcher / venv flow.
- `[project.scripts]` in any workspace `packages/*/pyproject.toml` — new or
  renamed entry-points (the root `pyproject.toml` declares none).
- `packages/ai-hats-library/**/hooks/**` and `**/git_hooks/**` — PreToolUse /
  PostToolUse hook scripts and the installed git hooks (`git_hooks` is a
  distinct segment: a `**/hooks/**` glob does not match it).
  Highest blast radius in the repo: a hook gates *every* tool call of *every*
  role composing it, so a four-line change can disable every agent. The test
  must drive the **composed chain** (all hooks on the matcher, in order) via
  `tests/e2e/_helpers/hook_chain.py`, never one script in isolation: a
  single-hook test cannot observe a second hook overriding its verdict, which
  is how HATS-1113 shipped a blanket deny past a green suite (HATS-1253).
- Anything else crossing an external contract: PEP 508 URL forms, click nesting, shell quoting, venv invocation.

**Does not trigger:** internal Python modules (storage, parsing, business logic), docs, tests-only changes, version bump.

## 2. What counts as an e2e test

A test passes the gate only if **all** of these hold:

- Lives under `tests/e2e/` (the dedicated real-subprocess CLI layer — see `tests/README.md`).
- Marked `@pytest.mark.integration`.
- Spawns a **real** subprocess chain: real `bash`, real `pip install`, real `ai-hats` binary. No `MagicMock`, no `monkeypatch` on `subprocess.Popen`, no `CliRunner.invoke()`.
- Asserts observable end-to-end side effects (exit codes, files on disk, captured output) — not internal call counts.

In-process `CliRunner` tests do **not** satisfy this rule, regardless of marker.

## 3. Plan-stage requirement

When the trigger fires, the task plan must explicitly name the e2e test(s) it will add — file path and what it asserts. "Will add e2e coverage" is not sufficient.

## 4. Review-stage check

The reviewer verifies before approving `done`:

- The named e2e test exists at the declared path.
- `pytest -m integration tests/e2e/` passes locally.
- The test would fail if the change under review were reverted (i.e. it actually exercises the new behaviour, not just lives alongside it).

If any check fails, the card returns to `execute`.

## 5. Source

PROP-031, from HATS-333 — two production bugs shipped past `done` because the
unit suite stubbed the very contracts the change broke.
