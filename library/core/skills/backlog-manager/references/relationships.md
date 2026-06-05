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
