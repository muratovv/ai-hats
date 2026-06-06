# Relationships: parent_task vs depends_on

Two distinct relationship types — pick by intent, don't conflate:

- **`parent_task`** (single, scalar) — *composition*. The task is a child of an epic
  or a sub-step of a larger work item. A task has at most one parent. Use this
  to model `epic → tickets` hierarchies.
- **`depends_on`** (list, multiple) — *blocking*. The task cannot meaningfully
  start (or complete) until each listed task is `done`. Use this for ordering
  constraints between peers.

Both live in the YAML card as first-class fields — **do NOT** stuff
"Parent: PROJ-X" / "Depends on: PROJ-Y" lines into the description as
free text. That worked historically but is invisible to CLI filters,
not validated, and breaks on typos.

```bash
# Create with both relationships
ai-hats task create "Implement export pipeline" \
  --id PROJ-110 \
  --parent-task PROJ-100 \
  --depends-on PROJ-105 --depends-on PROJ-107

# Set / change parent later
ai-hats task update PROJ-110 --parent-task PROJ-101
ai-hats task update PROJ-110 --clear-parent

# Mutate blockers later
ai-hats task update PROJ-110 --add-depends PROJ-108 --remove-depends PROJ-105

# Inspect — `task show` resolves depends_on to a "Blocked by:" section
# with each blocker's current state, so you can see at a glance what's
# still unblocking the task.
ai-hats task show PROJ-110

# Find everything that depends on PROJ-105 (regex search covers depends_on too)
ai-hats task list --search PROJ-105
```

**Validation behavior:**
- Self-references (parent or depends pointing at the same task) → hard error.
- Immediate two-task cycles (`A.depends=[B]` and `B.depends=[A]`) → hard error.
  Deeper transitive cycles are not detected — keep the dependency graph shallow.
- Unknown reference IDs → **warning** (yellow), but the write succeeds. This
  allows forward-references during planning and lets you fix typos with a
  follow-up `task update`.

## Harness behaviour: linked-context injection (HATS-689)

Links are not just inert metadata. When a sub-agent takes a task non-interactively
(`ai-hats agent --ticket <id>` or `ai-hats execute --batch --ticket <id>`), the
harness auto-injects a `# LINKED_CONTEXT` section into the agent's first prompt —
the **cards of all directly-linked tasks**, so the agent doesn't have to chase
them with `task show`.

- **Salience order:** `parent_task` (epic, first) → `depends_on` → `related` →
  `see_also` (deduped; self / missing targets skipped).
- **Per card:** a trimmed view (`id, title, state, description`) plus only the
  **latest** `work_log` entry. The **parent epic** additionally carries its
  `plan.md` (the decomposition / design lives there); other links are card-only.
- **Direct links only** — one level, no recursion / transitive walk.

So a bug `related` to a release arrives with the release card as planning context.
Interactive `ai-hats execute` (HITL) does not currently receive ticket links.
