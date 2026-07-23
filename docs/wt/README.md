# Worktree (wt) Documentation Hub

Welcome to the documentation hub for **`ai-hats-wt`** and worktree management in `ai-hats`.

`ai-hats-wt` is a hook-agnostic, dependency-free **git-worktree engine** that creates, manages, and tears down linked git worktrees with layered file-locking concurrency control.

---

## Recommended Reading Order

1. **[Overview & Architecture](overview.md)** — High-level concepts, state storage layout, concurrency locks, and lifecycle extension points.
2. **[Worktree (wt) Glossary](glossary.md)** — Terminology reference (`Canonical base branch`, `wt core boundary`, `needs_worktree effect`, `IsolationMode`).
3. **[Worktree CLI & Workflow Manual](../how-to-advanced.md#2-worktree-workflow)** — User and agent guide for CLI usage (`wt create/list/merge/discard/cleanup`), execution, and safety guards.
4. **[Standalone Package API](../../packages/ai-hats-wt/README.md)** — `ai-hats-wt` PyPI package documentation, installation, and standalone Python API usage (`WorktreeManager`).
5. **[Worktree Lifecycle Hooks](../how-to-extend.md#worktree-lifecycle-hooks)** — How to write `wt_in` / `wt_out` lifecycle hooks for seeding and harvesting gitignored data across worktree boundaries.

---

## Architectural Decision Records (ADRs)

Detailed historical decision records governing worktree design:

- **[ADR-0006: Layered Concurrency Defense](../adr/0006-worktree-concurrency-layered-defense.md)** — Design of the L1–L4 file-locking hierarchy for concurrent worktree operations.
- **[ADR-0012: Worktree Data Transfer](../adr/0012-worktree-data-transfer.md)** — Design of carry-in / carry-out lifecycle hooks across worktree boundaries.
- **[ADR-0013: WT Core Extraction Boundary](../adr/0013-wt-core-extraction-boundary.md)** — Extraction of the hook-agnostic `ai-hats-wt` engine from the framework core.
- **[ADR-0014: Composable Component Decomposition](../adr/0014-composable-component-decomposition.md)** — Decoupling task tracker FSM from worktrees (`WorktreeEffects` seam).
- **[ADR-0015: Task Ownership](../adr/0015-task-ownership.md)** — Coordination of active agent sessions, task ownership, and worktree recovery.
- **[ADR-0017: Backlog YAML Single Definition](../adr/0017-backlog-yaml-single-definition.md)** — Extension binding for worktree side-effects in `ai-hats-rack`.

---

## Navigation & Quick Links

- [Main Documentation Index](../INDEX.md)
- [Main Glossary](../glossary.md)
- [Worktree CLI & Workflow Manual](../how-to-advanced.md#2-worktree-workflow)
- [PyPI Package (`ai-hats-wt`)](../../packages/ai-hats-wt/README.md)
