# Migration: v0.8.0 → v0.9.0

`v0.9.0` is a MAJOR bump under the pre-1.0 `0.MAJOR.MINOR` scheme. It overhauls
how ai-hats is **installed and updated**: `uv` becomes the single host
prerequisite, and a per-project **channel** now decides where `ai-hats self
update` pulls the harness from. Existing projects keep working — the new
defaults are chosen so an untouched `ai-hats.yaml` migrates cleanly — but the
*source* an update resolves changes, so the move is worth understanding.

This doc is the migration reference required by the breaking-change protocol [1]
and is linked from the `Changed` / `Removed` entries in the changelog [2].

## 1. `uv` is now the single host prerequisite (Python precondition removed)

**What changed (HATS-763).** The env engine moved from `python -m venv` + `pip`
to [`uv`](https://docs.astral.sh/uv/). `uv` is now the one thing a host needs:
it provisions Python (pinned to 3.11) and builds every managed venv. The old
"install Python 3.11+ first" precondition is gone — there is **no pip
fallback**; a missing `uv` fails loud with the install one-liner.

**Why.** One static binary that both provisions Python and manages venvs removes
the "which Python?" class of setup failures and dedupes packages across projects
via uv's global cache.

**Migrate.** Install `uv` once per host:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

In practice this is usually automatic: the bash bootstrap (`scripts/bootstrap.sh`)
auto-installs `uv` when absent, and the launcher's heal path fails loud with the
same one-liner if it is missing. Your **first** `ai-hats self update` on v0.9.0
rebuilds the existing pip-built venv as a uv-built versioned install — no manual
step, and your project data is untouched.

## 2. Default install source is now the `stable` PyPI channel

**What changed (HATS-764, HATS-765).** `ai-hats self update` now resolves the
harness through a per-project **channel** recorded as `harness.channel` in
`ai-hats.yaml`. Three channels map to three audiences (full semantics in [3]):

- **`stable`** — `ai-hats==<latest-tag>` from PyPI. Pinned, semver-monotonic. **The default.**
- **`edge`** — `git+https://<repo>@<branch-HEAD-sha>`. A moving target — the old "latest master" behaviour.
- **`local`** — `uv pip install -e <path>`. An editable working tree, for developing ai-hats itself.

Before v0.9.0, `self update` tracked **master HEAD from GitHub**. After v0.9.0,
an `ai-hats.yaml` with no `harness:` block loads as **`stable`**, so the next
update installs the latest *published release* instead of bleeding-edge master.

**Why.** End users should get reviewed, versioned releases by default; tracking
an unreleased branch should be an explicit opt-in, not the silent default.

**Migrate.** Pick the channel that matches how you use ai-hats:

```bash
# End user — get published releases (this is the default; nothing to do).
ai-hats config set --channel stable

# Keep tracking the latest master (the pre-v0.9.0 behaviour).
ai-hats config set --channel edge --repo https://github.com/muratovv/ai-hats.git

# Developing ai-hats itself — editable install of your checkout.
ai-hats config set --channel local --path /path/to/ai-hats
```

Do **not** hand-edit the `harness:` block; `ai-hats config set` keeps it
consistent. Power-user channel knobs (running `edge` against a fork,
`AI_HATS_REPO_URL`) — see [4] §6.

## 3. `git+ssh` install specs → `git+https` (the repo is public)

**What changed (HATS-766).** The repository is now public, and every install
path uses `git+https`. If you maintain a **manual install spec** — typically an
override venv (`venv_path:`) you populate yourself — that points at
`git+ssh://git@github.com/muratovv/ai-hats.git`, switch it to `git+https`:

```bash
# Before
uv pip install --python <venv>/bin/python "ai-hats @ git+ssh://git@github.com/muratovv/ai-hats.git"

# After
uv pip install --python <venv>/bin/python "ai-hats @ git+https://github.com/muratovv/ai-hats.git"
```

No SSH key or repo access is needed any more. Managed (default-venv) installs
are migrated automatically by the resolver; only hand-rolled specs need editing.

## 4. `ai-hats self clean` was removed (it was a no-op on v4)

The `ai-hats self clean` command is gone. On v4 it was already a **total
no-op**: framework content is composed in memory, so the rules/skills mirrors
it wiped are empty, the legacy `.agent/{skills,hooks}` it swept don't exist, and
the `.ai-hats-managed` manifest its sweep read was never written. It cleaned
nothing.

**Migration:** drop any `ai-hats self clean` invocations — they had no effect.
To re-materialize a project's managed tree (the command's closest real intent),
run `self update` (or `self init` on a fresh project):

```bash
ai-hats self update                          # reinstall + re-materialize managed content
ai-hats self init -r <role> -p <provider>    # fresh project
```

## References

**[1]** — [`docs/RELEASING.md`](RELEASING.md) — SemVer policy and the
breaking-change protocol (deprecation, migration doc, changelog reference).

**[2]** — [`CHANGELOG.md`](../CHANGELOG.md) — the `[0.9.0]` section (finalized at
the release cut); its `Changed` / `Removed` entries reference this doc.

**[3]** — [`docs/glossary.md`](glossary.md) — **Harness source / channel**: the
local / edge / stable channel model and its resolver.

**[4]** — [`docs/how-to-advanced.md`](how-to-advanced.md) — §6 channel &
install-source power-user knobs (`edge` against a fork, `AI_HATS_REPO_URL`).
