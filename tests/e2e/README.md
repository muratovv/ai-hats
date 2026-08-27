# ai-hats e2e tests

End-to-end tests that exercise the **real** ai-hats CLI surface,
launcher install flow, and (where needed) the live Claude SDK. They
catch integration bugs that the unit suite stubs away.

If you're adding a new e2e test, read [How to add an e2e test](#how-to-add-an-e2e-test) first.

## Cost tiers

Every e2e test belongs to one of three tiers — pick the cheapest one
that still exercises the surface under test. What the tier covers today,
as user flows, is `CATALOG.md` — rendered from each test's docstring block
by `scripts/gen_e2e_catalog.py` and kept current by the `e2e-catalog` stage
of `scripts/ci-local.sh`.

| Tier | Fixture                                      | Wall-clock budget                          | Quota               | Use for                                                                                                |
| ---- | -------------------------------------------- | ------------------------------------------ | ------------------- | ------------------------------------------------------------------------------------------------------ |
| free | `tmp_project`                                | <5s per test                               | $0                  | CLI commands that don't spawn an agent (`ai-hats list`, `config`, `task`, `attach`, …)                 |
| venv | `tmp_venv_project`                           | <120s first test in the session, <5s after | $0                  | Launcher install, `self update`, `self init`, `self bump`, anything that needs a real installed binary |
| live | `requires_claude_auth` / `requires_agy_auth` | <30s per test                              | real provider quota | Agent-loop scenarios — tool use, session artefacts, provider behaviour                                 |

Always prefer free over venv over live.

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
           "claude",
           "claude",
       )


   # venv-tier — see tests/e2e/test_wave1_venv_tier.py
   import pytest

   pytestmark = pytest.mark.integration


   def test_something(tmp_venv_project) -> None:
       tmp_venv_project.run(
           "self", "init", "-r", "assistant", "-p", "claude", "--no-update"
       ).expect_ok()


   # live-tier — see tests/e2e/test_subagent_sdk_smoke.py. There is no live
   # project fixture: take `requires_claude_auth` (or `requires_agy_auth`)
   # alongside whichever project fixture the test needs, and drive the real
   # runner or binary. The gate fixture skips when nothing is authenticated.
   def test_something(some_project, requires_claude_auth) -> None:
       ...
   ```

3. **Keep the body small.** Fluent `.expect_*` verbs from `RunResult`
   chain — one verb = one assertion, each returns self. If your test
   body breaks past ~10 lines, the fixture probably needs to absorb
   more setup.

4. **Open the module docstring with the flow block** — `flow:` / `cmds:` /
   `expect:` / `why:`. `CATALOG.md` is rendered from it, and the
   `e2e-catalog` stage exits non-zero naming any file that carries none
   (`test_e2e_catalog_uncatalogued_refusal.py`).

5. **Run it.** `pytest tests/e2e/test_<your_file>.py -v`. To skip
   venv- and live-tier in fast iterations:
   `pytest -m "not integration" tests/e2e/`. For a deterministic
   **offline / no-auth** run that keeps the rest of the e2e suite but
   drops real provider calls:
   `pytest -m "not live_claude and not live_agy" tests/e2e/`. The
   `live_claude` and `live_agy` markers are auto-applied (no decorator
   needed) to tests gating on `requires_claude_auth` and
   `requires_agy_auth`, respectively. The canonical full e2e gate excludes
   `live_agy`; run that cohort explicitly with
   `pytest -m live_agy tests/e2e/`.

## Fixtures (`conftest.py`)

| Fixture                | Scope                                       | Returns   | Notes                                                                                                                                                                                      |
| ---------------------- | ------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `repo_root`            | session                                     | `Path`    | Repo checkout root. Process-wide constant.                                                                                                                                                 |
| `checkout_bin`         | session                                     | `Path`    | Every consent-wrapped surface (`ai-hats`, `rack`) as a shim over the interpreter under test. Lead a session `PATH` with THIS — a surface it lacks comes from the ambient PATH (HATS-1847). |
| `requires_claude_auth` | function                                    | `None`    | Skip marker. Skips if `claude --version` doesn't exit 0.                                                                                                                                   |
| `requires_agy_auth`    | session                                     | `None`    | Skip marker. Skips unless `agy --version` and a bounded live turn both exit 0.                                                                                                             |
| `tmp_project`          | function                                    | `Project` | Role-less project + dev-venv binary. Free-tier.                                                                                                                                            |
| `tmp_venv_project`     | function (on a session-scoped venv builder) | `Project` | Fresh project dir + shared launcher venv via `AI_HATS_VENV`. Venv-tier.                                                                                                                    |

`tmp_venv_project` is layered: an internal session-scoped builder
(`_shared_launcher_venv`) runs `bash scripts/install-launcher.sh` +
`ai-hats self update` once per session (~30-60s on a cold cache, HATS-569),
while the user-facing fixture is function-scoped and hands each test
a fresh project directory pointing at the shared venv via
`AI_HATS_VENV`. Tests can mutate their own project freely. **The
shared venv MUST NOT be mutated destructively** — no
`rm -rf <venv>`, no `pip uninstall`, no `self bump` to a different
ai-hats version. Tests that need a hostile venv should declare their
own function-scoped builder.

## Helper modules (`tests/e2e/_helpers/`)

The high-signal subset a test author actually meets — **not** the full
listing. `ls tests/e2e/_helpers/` is the listing; reading this table as one
is how a row for a deleted module survived here (HATS-1847).

| File            | What it provides                                                                                                                                          |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `project.py`    | `Project` + `RunResult` — subprocess driver for one-shot CLI invocations with fluent `.expect_*` verbs.                                                   |
| `venv.py`       | `build_launcher_venv()` — installs the bash launcher and bootstraps its inner ai-hats venv. Used by `tmp_venv_project`.                                   |
| `env.py`        | `clean_env()` + `ENV_DENYLIST` — subprocess env hygiene, so a run exercises the installed artefact and not a source-tree shadow (HATS-685).               |
| `surfaces.py`   | `SURFACES` + `write_surface_shims()` — the executables a PATH entry must carry to be the checkout under test. Behind `checkout_bin`.                      |
| `hook_chain.py` | `run_chain()` + `Verdict` — drives the MATERIALIZED PreToolUse chain rather than a single hook, so one hook overriding another is observable (HATS-1253). |
| `sessions.py`   | `stand_in_wrapped_session()` + `wait_for_new_session_dir()` — stand-in HITL session, and polling for session artefacts.                                   |

## When something breaks

- **Free-tier tests fail with "ai-hats: command not found"** — your
  dev venv is missing or stale. Run `uv pip install -e ".[dev]"`.
- **Venv-tier tests skip with "launcher venv build failed"** — `uv` is
  missing from PATH, or it needs network for transitive deps against a cold
  cache. Ensure network access or pre-warm the cache. Under
  `AI_HATS_E2E_REQUIRE_VENV=1` (what the master gate exports) the same
  condition FAILS instead of skipping — cannot verify ⇒ cannot push.
- **Live-tier tests skip with "claude binary not found"** — install
  the Claude CLI and authenticate (`claude login`). The `agy` cohort skips
  the same way and is excluded from the full e2e gate; run it explicitly
  with `pytest -m live_agy tests/e2e/`.

## Adding a new fixture

Before adding a fixture, ask whether `tmp_project` or `tmp_venv_project`
can be extended instead. Those two cover the free and venv tiers, and a
live-tier test composes a gate fixture onto one of them — a new fixture
needs a distinct cost/scope profile to justify its existence.
