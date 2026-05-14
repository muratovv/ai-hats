# Migration: v3 → v4 layout (HATS-311 epic)

`ai-hats` 0.X+ consolidates every framework-managed artefact under a single
configurable root `<ai_hats_dir>/` (default `.agent/ai-hats/`). Legacy
top-level directories under `.agent/` and `.gitlog/` are migrated in one
shot when you run `ai-hats self bump` against the new release.

This page is the on-call sheet for upgrading an existing project.

## TL;DR

```bash
pip install --upgrade ai-hats  # or: pipx upgrade ai-hats
ai-hats self bump              # runs migration + re-applies the role
```

After bump:

```bash
ai-hats task show <ID>          # works against new paths
ai-hats task sync               # refreshes <ai_hats_dir>/STATE.md
ai-hats reflect <something>     # session traces land in <ai_hats_dir>/sessions/runs/
```

`git status` will show only the canonical `.agent/ai-hats/` tree changing
(everything inside is gitignored by default).

## What moves where

The migration is grouped into three classes; each runs idempotently and
can be re-invoked safely.

### Sessions (per-run / runtime)

| Legacy | New |
|---|---|
| `.gitlog/` (whole tree, including `pipeline_runs/` and `session_<id>/`) | `<ai_hats_dir>/sessions/runs/` |
| `.agent/retrospectives/` | `<ai_hats_dir>/sessions/retros/` |
| `.agent/audits/` | `<ai_hats_dir>/sessions/audits/` |
| `.agent/handoffs/` | `<ai_hats_dir>/sessions/handoffs/` |
| `.agent/experiments/` | `<ai_hats_dir>/sessions/experiments/` |
| `.agent/worktrees/` | `<ai_hats_dir>/sessions/worktrees/` |
| `.agent/worktree.json` | `<ai_hats_dir>/sessions/worktree.json` |

### Tracker (lifecycle records)

| Legacy | New |
|---|---|
| `.agent/backlog/tasks/` | `<ai_hats_dir>/tracker/backlog/tasks/` |
| `.agent/backlog/proposals/` | `<ai_hats_dir>/tracker/backlog/proposals/` |
| `.agent/hypotheses/` | `<ai_hats_dir>/tracker/hypotheses/` |
| `.agent/decisions/` | `<ai_hats_dir>/tracker/decisions/` |
| `.agent/STATE.md` | `<ai_hats_dir>/STATE.md` |
| `.agent/.last_backup` | `<ai_hats_dir>/.last_backup` |

### Library (managed mirrors)

| Legacy | New |
|---|---|
| `.agent/rules/<name>/` | `<ai_hats_dir>/library/rules/<name>/` |
| `.agent/skills/<name>/` | `<ai_hats_dir>/library/skills/<name>/` |
| `.agent/hooks/` | `<ai_hats_dir>/library/hooks/` |
| `.agent/mcp/` | `<ai_hats_dir>/library/mcp/` |

### Not migrated (external publish targets)

These remain at their existing locations — they are owned by tooling
outside `ai-hats` and `ai-hats` keeps publishing into them with copy:

- `.claude/skills/` — Claude Code's skill registry.
- `.githooks/` — git looks here via `core.hooksPath`.
- `.claude/plans/`, `.claude/CLAUDE.md`, `.claude/settings.json` — Claude
  Code feature surface.

## Configuration

Schema v4 adds one required field to `ai-hats.yaml`:

```yaml
schema_version: 4
ai_hats_dir: .agent/ai-hats/   # explicit; migration writes the default automatically
```

Resolution order (env > yaml > bootstrap):

1. `AI_HATS_DIR` env var — runtime override (tests, sandbox, debug).
2. `ai-hats.yaml` `ai_hats_dir` field — primary source.
3. `.agent/ai-hats/` bootstrap fallback — used pre-migration / fresh
   projects / when no yaml exists.

If you delete the `ai_hats_dir` line from a v4 yaml, ai-hats raises a
loud `ProjectConfigError` on next load — the field is required.

## Backup & rollback

Before any write, `bump` snapshots the prior state to
`<ai_hats_dir>/.last_backup` (a file holding the temp-dir path of the
real backup tree). To roll back:

```bash
cat <ai_hats_dir>/.last_backup        # prints the backup path
cp -R "$(cat <ai_hats_dir>/.last_backup)/.agent" .
# inspect, then re-run `ai-hats self bump` to re-apply.
```

The backup tree persists across runs until you `rm -rf` it manually.

## Smoke checklist

After `ai-hats self bump`:

- [ ] `ai-hats task show <ANY_ID>` returns the task card.
- [ ] `ai-hats task sync` updates `<ai_hats_dir>/STATE.md` without errors.
- [ ] `ls <ai_hats_dir>/` contains `sessions/`, `tracker/`, `library/`,
      `STATE.md`, `.last_backup` (plus the existing `rules/`, `traits/`,
      `pipeline_steps/`, `traces/`, etc.).
- [ ] `.agent/retrospectives/`, `.gitlog/`, `.agent/backlog/`,
      `.agent/STATE.md` — gone.
- [ ] `.claude/skills/` and `.githooks/` — unchanged.
- [ ] `git status` shows only managed artefacts (everything inside
      `.agent/ai-hats/` is gitignored).

## Troubleshooting

### `bump` interrupted mid-flight

The migration is idempotent: re-run `ai-hats self bump`. The
`_idempotent_move` helper deals with partially-migrated layouts by
copying anything that still lives under the legacy path and discarding
collisions in favour of the new side.

### Active worktrees / unsaved local edits

Worktree state (`.agent/worktree.json` and `.agent/worktrees/`) is
session-class — it moves to `<ai_hats_dir>/sessions/`. Close any
in-progress worktrees before running `bump` if you want a clean view;
otherwise the new state is still consistent — just less ergonomic to
reason about.

Unsaved hand edits in `.agent/backlog/tasks/<ID>/`: the migration moves
the directory wholesale; your edits travel with it. If you put files
that look like task cards somewhere else in `.agent/`, move them
manually after bump.

### `ai-hats task list` shows nothing right after upgrade

Almost certainly bump did not run yet. After upgrading the package, you
must run `ai-hats self bump` once — the CLI looks at the new paths
immediately, but the on-disk move happens inside bump.

### `.agent/` is empty after migration

That's expected. `ai-hats` no longer manages anything outside
`<ai_hats_dir>/`. The wrapper `.agent/` is left in place because it
might hold files you put there — `ai-hats` will print:

```
NOTE: .agent/ holds only the managed ai-hats/ namespace; legacy
top-level artefacts (rules/, skills/, hooks/, backlog/, STATE.md, ...)
have migrated to <ai_hats_dir>/. If nothing else of yours lives in
.agent/, the wrapper is no longer required — ai-hats will not remove
it automatically.
```

If you confirm `.agent/` has only `.agent/ai-hats/` inside, feel free
to flatten with a manual move, or leave it as-is (the canonical default
keeps the wrapper).

## `.gitignore`

The dynamic managed-block generator from prior releases is gone
(HATS-317). On `init`, ai-hats appends a single line:

```
.agent/ai-hats/
```

…and never touches `.gitignore` again. If you want to commit something
that lives inside `<ai_hats_dir>/` (e.g. project-pinned `role.md`),
add an explicit `!.agent/ai-hats/role.md` entry yourself.

## Local-venv (opt-in, HATS-318)

`ai-hats` is normally installed via `pipx install ai-hats`. For CI and
multi-machine teams that need version pinning, a local venv at
`<ai_hats_dir>/.venv/` is supported as an opt-in alternative.

```bash
ai-hats self use-local     # creates <ai_hats_dir>/.venv/ and installs ai-hats
ai-hats self use-global    # removes the local venv (revert to global)
```

When the local venv is active, every subsequent `ai-hats` invocation
re-execs through `<ai_hats_dir>/.venv/bin/python` — `ai-hats self update`
updates that copy, while the global `pipx` install is left untouched.

The local venv is detected purely by the presence of
`<ai_hats_dir>/.venv/bin/python`; no marker file is needed. To check what
the wrapper is using, look at `sys.executable` (printed at the top of
`ai-hats self update`).

Decision rationale and trade-offs:
`<ai_hats_dir>/tracker/decisions/2026-05-13-hats-315-venv-research.md`.

## Compatibility: mixed installs (HATS-330 / HATS-331)

If you have **two** ai-hats installs targeting one project — a global
`pipx` *and* a project-local `<project>/.venv/bin/ai-hats` (Poetry, uv,
manual pip) — keep their versions in lock-step.

`ai-hats self bump` rewrites `ai-hats.yaml` in the schema understood by
the *bumping* interpreter. A stale local install will then crash on the
next invocation with `pydantic_core.ValidationError: extra_forbidden`
on an unknown top-level field (e.g. `ai_hats_dir` after v3 → v4).

**Gate (HATS-330):** since v4, `ai-hats self bump` detects a local
`<project>/.venv/bin/ai-hats` and refuses to start when its version
differs from the bumping interpreter. The error message includes the
exact `pip install -U` command to fix the local install. Override
(NOT recommended): `--force-allow-mismatch`.

Recovery if you hit this before the gate landed:

```bash
<project>/.venv/bin/pip install -U --force-reinstall \
    "ai-hats @ git+ssh://git@github.com/muratovv/ai-hats.git"
```

Then `ai-hats self bump` once more to confirm both installs see the
same yaml.

The HATS-318 opt-in venv at `<ai_hats_dir>/.venv/` is *not* affected:
the wrapper re-exec keeps the bumping interpreter and the local-venv
interpreter identical by construction.

## Cross-references

- ADR: `<ai_hats_dir>/tracker/decisions/2026-05-13-hats-316-ai-hats-dir-layout.md`
- Venv research: `<ai_hats_dir>/tracker/decisions/2026-05-13-hats-315-venv-research.md`
- Epic: HATS-311 (parent), HATS-312 (sessions), HATS-313 (tracker),
  HATS-314 (library), HATS-316 (foundation), HATS-317 (cleanup).
