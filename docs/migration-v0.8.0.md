# Migration: v0.7.0 → v0.8.0

`v0.8.0` is a MAJOR bump under the pre-1.0 `0.MAJOR.MINOR` scheme: it removes
two commands from the stable CLI surface. Both removals are mechanical to
migrate — the underlying capability is preserved or replaced one-to-one.

This doc is the migration reference required by the breaking-change protocol [1]
and is linked from the `Removed` entries in the changelog [2].

## 1. `ai-hats task plan-sync` removed; `.claude/plans` plan-import gone

**What changed (HATS-637).** A plan is now always a task and always lives at the
one canonical path `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`.

- `ai-hats task plan-sync <ID> [--from-file <f>]` is removed, together with its
  engine internals (`_sync_plan_from_claude_plans`, `find_claude_plan_for_task`,
  `PlanSyncAmbiguousError`).
- `ai-hats task transition <ID> plan` no longer imports
  `.claude/plans/<NN>-*.md`. A stray file there is now inert.

**Why.** The `.claude/plans → plan-sync` round-trip was a second write path for
the same artefact. It drifted from the CLI across installs (a stale skill kept
telling agents to run `plan-sync` against binaries that no longer had it) and
added a step with no value over writing the plan where it lives. One canonical
home removes the skew class entirely.

**Migrate.** Replace the round-trip with a direct edit of the tracker plan:

```bash
# Before
ai-hats task transition PROJ-001 plan
ai-hats task plan-sync PROJ-001 --from-file .claude/plans/01-foo.md

# After
ai-hats task transition PROJ-001 plan          # scaffolds plan.md
# then Write/Edit the plan body directly into:
#   <ai_hats_dir>/tracker/backlog/tasks/PROJ-001/plan.md
```

The per-section plan gate (HATS-635) still blocks `transition execute` on an
empty scaffold, so the "no execute without a real plan" guarantee is unchanged.
In Claude Code plan-mode (read-only), the `.claude/plans/<slug>.md` draft is
expected Phase-1 scratch — transfer it into the tracker `plan.md` as the first
post-approval action. See the `plan-discipline` skill for the full flow.

## 2. `ai-hats self bump` removed

**What changed.** The standalone `ai-hats self bump` command is removed. The
bump capability itself is **preserved** — it now runs only via the auto-bump
path inside `ai-hats self update` (fresh subprocess, HATS-400) and inline from
`ai-hats self init`.

**Why.** Direct user invocation of bump was rare; folding it into `self update` /
`self init` makes its framework-internal status honest and removes a footgun
(running a raw bump out of the update flow).

**Migrate.** Use the update flow, which bumps as part of its work:

```bash
# Before
ai-hats self bump

# After
ai-hats self update      # refreshes + bumps
# or, when only re-materializing composition:
ai-hats self init
```

The hidden `python -m ai_hats._bump_internal` entry-point remains for the
subprocess case (HATS-470); it is not a user-facing command.

## Timeline & protocol note

Both commands were removed in the work that landed for `v0.8.0` (HATS-637 for
`plan-sync`, the `self bump` extraction for the latter) and ship for the first
time in this release. Neither went through a prior deprecation-warning release:
each was an internal authoring/maintenance helper rather than a load-bearing
public command, and each has a direct one-to-one replacement above. The MAJOR
bump + this migration doc are the user-facing signal.

## References

**[1]** — [`docs/RELEASING.md`](RELEASING.md) — SemVer policy and the
breaking-change protocol (deprecation, migration doc, changelog reference).

**[2]** — [`CHANGELOG.md`](../CHANGELOG.md) — the `[0.8.0]` section; the
`Removed` entries reference this doc.
