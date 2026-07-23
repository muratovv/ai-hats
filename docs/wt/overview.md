# Worktree Engine Architecture & Overview

This document provides a conceptual and architectural overview of `ai-hats-wt`, the hook-agnostic git-worktree engine that provides isolated development environments in `ai-hats`.

---

## 1. High-Level Concept

When an agent or developer starts work on a task (e.g. `rack transition HATS-XXX execute`), `ai-hats` isolates all code and configuration edits inside a dedicated **git worktree**.

Key properties:
- **Isolation by Default:** Code edits in the main repository checkout are prevented/discouraged to keep `master`/`main` clean.
- **Dedicated Branching:** Worktrees are created on temporary task branches (`task/hats-XXX` or `ai-hats-wt-task-hats-XXX-...`).
- **Clean Lifecycle Teardown:** Upon task completion, changes are merged back into the target branch (fast-forward or squash merge) or cleanly discarded.

---

## 2. State, Storage & Configuration Layout

Worktree state metadata is maintained in a project-local state directory (`<project>/.wt/`):

```
<project>/.wt/
├── manager.lock        # L2 Process lock for manager creation/teardown
└── state/
    ├── <id>.json       # Worktree state metadata (branch, creation time, status)
    └── <id>.lock       # L1 Worktree-level state lock
```

Project-level worktree behavior (such as setting non-default base branches or merge targets) is configured via the `worktree:` block in `ai-hats.yaml`. See [How to configure › the `worktree` block](../how-to-configure.md#1a-the-worktree-block--fork-workflows-base--merge-target).

---

## 3. Concurrency & Layered File-Locking (L1–L4)

Concurrent worktree operations (such as parallel subagents creating or merging worktrees on the same repo) are protected against race conditions and git index corruption by a **4-tier concurrency model**:

| Level | Lock Target / Scope | Purpose |
| ----- | ------------------ | ------- |
| **L1** | `<project>/.wt/state/<id>.lock` | Per-worktree state lock preventing concurrent mutations on the same worktree instance. |
| **L2** | `<project>/.wt/manager.lock` | Global process lock serializing worktree creation (`git worktree add`) and removal. |
| **L3** | `.git/index.lock` retries | Retries around git index lock contention during staging and commits. |
| **L4** | `.git/refs/heads/<branch>.lock` retries | Retries around git ref lock contention during branch creation and merge operations. |

For full concurrency rationale, see [ADR-0006](../adr/0006-worktree-concurrency-layered-defense.md).

---

## 4. Lifecycle Hooks & Extension Point (`WorktreeLifecycle`)

The worktree engine itself (`ai-hats-wt`) is **hook-agnostic**: it knows where lifecycle events trigger, but does not execute framework logic directly.

`ai-hats` extends `ai-hats-wt` by passing a `WorktreeLifecycle` collaborator at construction:

```python
class WorktreeLifecycle:
    def on_created(self, ctx: LifecycleContext) -> None:
        """Invoked immediately after `git worktree add` (carry-in / seeding phase)."""
        ...

    def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
        """Invoked before teardown (`merge`, `discard`, `cleanup`) (carry-out phase)."""
        ...
```

- **Carry-in (`wt_in`):** Seeds gitignored configuration, environment variables, or local state into the new worktree.
- **Carry-out (`wt_out`):** Harvests logs, test results, or audit artifacts out of the worktree before it is deleted.

For details on lifecycle hook contracts, see [how-to-extend → Worktree lifecycle hooks](../how-to-extend.md#worktree-lifecycle-hooks) and [ADR-0012](../adr/0012-worktree-data-transfer.md).

---

## 5. Worktree Effects Seam & Task FSM (`needs_worktree`)

The task tracker FSM (`packages/ai-hats-tracker` / `packages/ai-hats-rack`) has no hard dependency on `ai-hats-wt` ([ADR-0014](../adr/0014-composable-component-decomposition.md)). Side-effects are injected via the `WorktreeEffects` protocol:

- `setup(task_id)` → Creates worktree on `→ execute`.
- `teardown(task_id, target_state)` → Merges or discards worktree on `→ done`/`failed`/`cancelled`.
- `assert_canonical_base()` → Refuses execution if HEAD is off `master`/`main`.

For task lifecycle integration and FSM transition rules, see [how-to-backlog.md](../how-to-backlog.md) and [Worktree (wt) Glossary → needs_worktree effect](glossary.md#needs_worktree-effect).
