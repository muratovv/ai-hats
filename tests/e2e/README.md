# ai-hats e2e tests

End-to-end tests that exercise the **real** ai-hats CLI surface,
launcher install flow, and (where needed) the live Claude SDK. They
catch integration bugs that the unit suite stubs away.

If you're adding a new e2e test, read [How to add an e2e
test](#how-to-add-an-e2e-test) first.

## Cost tiers

Every e2e test belongs to one of three tiers — pick the cheapest one
that still exercises the surface under test. The full Core catalog
with per-scenario classification lives in
`.claude/plans/466-scenarios-catalog-v1.md`.

| Tier | Fixture | Wall-clock budget | Quota | Use for |
|---|---|---|---|---|
| free | `tmp_project` | <5s per test | $0 | CLI commands that don't spawn an agent (`ai-hats list`, `config`, `task`, `attach`, …) |
| venv | `tmp_venv_project` | <120s first test in module, <5s subsequent | $0 | Launcher install, `self update`, `self init`, `self bump`, anything that needs a real installed binary |
| live | `probe_project` + `live_session()` | <30s per test | budget-capped via `max_budget_usd`, typically <$0.05 per test | Agent-loop scenarios — tool use, multi-turn state, session-id stability |

The 51 Core scenarios in the catalog split 32 free / 19 venv / a
handful live. Always prefer free over venv over live.

## How to add an e2e test

1. **Pick the tier** using the table above. If the test can pass
   without `ai-hats self update`, it's free-tier. If it needs the
   bash launcher path, it's venv-tier. Only reach for live-tier when
   the assertion is about agent behaviour.

2. **Pick the fixture and import idioms**:

   ```python
   # free-tier — see tests/e2e/test_wave1_free_tier.py
   def test_something(tmp_project) -> None:
       tmp_project.run("list", "providers").expect_ok().expect_stdout_contains(
           "claude", "gemini",
       )

   # venv-tier — see tests/e2e/test_wave1_venv_tier.py
   import pytest
   pytestmark = pytest.mark.integration

   def test_something(tmp_venv_project) -> None:
       tmp_venv_project.run("self", "init", "-r", "assistant", "-p", "claude",
                            "--no-update").expect_ok()

   # live-tier — see tests/e2e/test_w0_pilot_session.py
   import asyncio
   from _helpers.live import live_session

   def test_something(probe_project, requires_claude_auth) -> None:
       async def _drive():
           async with live_session(probe_project, role="probe",
                                   max_budget_usd=0.10) as s:
               r = await s.send("Reply OK!")
               r.expect_no_error().expect_contains_ci("ok")
       asyncio.run(_drive())
   ```

3. **Keep the body small.** Fluent `.expect_*` verbs from `RunResult`
   / `TurnResult` / `LiveSession` chain — one verb = one assertion,
   each returns self. If your test body breaks past ~10 lines, the
   fixture probably needs to absorb more setup.

4. **Map to a Core scenario** in the docstring (e.g. "→ S-CLI-22").
   That keeps the catalog and the test files connected.

5. **Run it.** `pytest tests/e2e/test_<your_file>.py -v`. To skip
   venv- and live-tier in fast iterations:
   `pytest -m "not integration" tests/e2e/`.

## Fixtures (`conftest.py`)

| Fixture | Scope | Returns | Notes |
|---|---|---|---|
| `repo_root` | session | `Path` | Repo checkout root. Process-wide constant. |
| `requires_claude_auth` | function | `None` | Skip marker. Skips if `claude --version` doesn't exit 0. |
| `tmp_project` | function | `Project` | Role-less project + dev-venv binary. Free-tier. |
| `tmp_venv_project` | function (on a module-scoped venv builder) | `Project` | Fresh project dir + shared launcher venv via `AI_HATS_VENV`. Venv-tier. |
| `probe_project` | function | `Path` | Bakes a deterministic `probe` role for live-session tests. Gated on `requires_claude_auth` at the test signature when a live SDK call follows. |

`tmp_venv_project` is layered: an internal module-scoped builder
(`_shared_launcher_venv`) runs `bash scripts/install-launcher.sh` +
`ai-hats self update` once per file (~30-60s on a cold pip cache),
while the user-facing fixture is function-scoped and hands each test
a fresh project directory pointing at the shared venv via
`AI_HATS_VENV`. Tests can mutate their own project freely. **The
shared venv MUST NOT be mutated destructively** — no
`rm -rf <venv>`, no `pip uninstall`, no `self bump` to a different
ai-hats version. Tests that need a hostile venv should declare their
own function-scoped builder.

## Helper modules (`tests/e2e/_helpers/`)

| File | What it provides |
|---|---|
| `project.py` | `Project` + `RunResult` — subprocess driver for one-shot CLI invocations with fluent `.expect_*` verbs. |
| `live.py` | `live_session()` + `LiveSession` + `TurnResult` — async multi-turn driver for the Claude Agent SDK. |
| `venv.py` | `build_launcher_venv()` — installs the bash launcher and bootstraps its inner ai-hats venv. Used by `tmp_venv_project`. |

The original framework plan
(`.claude/plans/466-framework-skeleton.md`) sketched a separate
`assertions.py` module — the `RunResult` / `TurnResult` /
`LiveSession` fluent verbs absorbed those, so a third module would
be YAGNI today.

## When something breaks

* **Free-tier tests fail with "ai-hats: command not found"** — your
  dev venv is missing or stale. Run `pip install -e '.[dev]'`.
* **Venv-tier tests skip with "launcher venv build failed"** — pip
  needs network to fetch transitive deps and the cache is cold.
  Either ensure network access or pre-warm the cache.
* **Live-tier tests skip with "claude binary not found"** — install
  the Claude CLI and authenticate (`claude login`).
* **Live-tier tests fail with budget exceeded** — bump
  `max_budget_usd=` on `live_session()`. Default per test is $0.10.

## Adding a new fixture

Before adding a fixture, ask whether `tmp_project` /
`tmp_venv_project` / `probe_project` can be extended instead.
Three fixtures cover the entire tier space today; a fourth needs a
distinct cost/scope profile to justify its existence.
