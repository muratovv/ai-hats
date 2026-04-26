---
name: agentic-topology-design
description: Choose a multi-agent topology and enforce structured contracts between sub-agents
---
# Agentic Topology Design

Pick the right multi-agent shape for a given task and enforce machine-parseable contracts between sub-agents. Treat sub-agents as microservices with tightly scoped prompts; coordination is the architect's responsibility.

## When to Use
- Designing a flow that uses ≥2 sub-agents (code review pipeline, deep research, incident triage, batch refactor)
- Replacing a "monolithic prompt" agent that has been showing interference / attention dilution
- Drafting an ADR for an agentic feature

## Topology Decision Matrix

| Topology | Coordination | Use when | Failure mode it prevents |
|----------|-------------|---------|---------------------------|
| **Sequential (Pipeline)** | Output of step N → input of step N+1 | Deterministic order, each step narrows focus (requirements → schema → code → tests) | Hallucinations from over-broad single prompt |
| **Parallel (Coordinator/Workers)** | Coordinator fans out independent tasks, aggregates JSON | Latency-critical or independent perspectives (multi-critic review, parallel log scan) | Single-agent code review with hundreds of nitpicks and missed real issues |
| **Hierarchical Decomposition** | Top agent plans abstract DAG; leaf agents call concrete tools (MCP) | Open-ended research, multi-stage incidents, dynamic scope | Premature commitment to a fixed plan |

## Conventions

- **Structured Outputs Mandate.** Every sub-agent emits JSON Schema-validated output. No free-form natural-language hand-offs between agents — that path leads to brittle parsing and silent corruption.
- **Tightly-Scoped Sub-Agent Prompts.** Each sub-agent owns one responsibility (search bugs / audit error handling / check security / lint corporate rules). Resist bundling.
- **Supervisor Owns the DAG.** One coordinator agent forms and mutates the execution graph; workers do not call other workers directly.
- **Bounded Concurrency.** Cap parallel sub-agents (Brooks/Ringelmann projection — see `context-handoff`); coordination overhead grows superlinearly past ~4–6 workers on the same artifact.
- **Idempotent Re-runs.** Sub-agent contracts must support replay with the same input → same output for cache-friendly recovery.

## Anti-Patterns
- One mega-prompt that "routes, formats, queries DB, and styles output" — instructions interfere; safety bullets get crowded out
- Free-text contracts ("write a paragraph the next agent will understand") — debug it once and you'll never repeat
- Workers calling workers ad-hoc — the DAG becomes implicit and unmonitorable
- Picking Hierarchical for a deterministic 4-step flow — over-engineering; Sequential wins
