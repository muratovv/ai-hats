---
name: clean-architecture-guide
description: Audit a codebase for DDD/Hexagonal/Ports-and-Adapters compliance. Use when reviewing a module that owns business logic, in a pre-merge audit when a PR touches domain types, repositories, or transport layers, when drafting an ADR for a new bounded context, or triaging a codebase drifting toward a Big Ball of Mud.
---
# Clean Architecture Guide

Verify a codebase or proposed design against the four invariants of Domain-Driven Design + Hexagonal Architecture. Use as an architectural lint before merge or as input to an ADR.

## When to Use
- Reviewing a new module that owns business logic (not pure infra glue)
- Pre-merge audit when a PR touches domain types, repositories, or transport layers
- Drafting an ADR for a new bounded context
- Triage when a codebase is drifting toward a Big Ball of Mud

## Checklist

For each item: state the **claim** (rule satisfied? where?) and the **violation** (concrete file/symbol if any). Mark `N/A` only with explicit justification.

1. **Entities & Value Objects.**
   Entities have a stable, unique identity that survives mutation. Value Objects are immutable and equal-by-value (Python: `@dataclass(frozen=True)`; Go: structs with no exported mutators or pointer-typed fields). No "anaemic entity that is really a Value Object", no "Value Object with a setter".
2. **Aggregate Rule.**
   Each aggregate has exactly one root. Internal entities are mutated only through the root, which enforces invariants and transactional boundaries. No repository for aggregate-internal types. External code holds references to the root, never to inner parts.
3. **Dependency Rule.**
   Dependencies point inward. Domain defines ports (interfaces). Adapters (DB drivers via GORM/SQLAlchemy, gRPC/REST handlers, message-bus clients) live in the outer layer and *implement* domain ports. The domain package imports nothing from `adapters/`, `infra/`, framework packages, or HTTP libraries. Verify with linter rules (e.g. `import-linter`, `go-cleanarch`).
4. **Layer Map.**
   Sketch concentric layers: Domain ← Application ← Adapters ← Frameworks. Place every package in exactly one layer. If a package straddles layers, split it. The application layer orchestrates use-cases; it does not contain business invariants (those belong in domain).

## Completion
- All 4 items have `claim` + `violation` (or justified `N/A`)
- Findings appended to ADR or PR description with severity
- Critical violations (Dependency Rule breach, leaked aggregate internals) blocking the merge

## Example
A `payments/` module exposes `Order` as a struct with public fields and stores it via an `OrderRepository` that returns the same struct. Hidden bug: `OrderLine` (an aggregate-internal entity) has its own `OrderLineRepository`, so callers can mutate lines without touching the `Order` root. Fixes: drop `OrderLineRepository`; expose `Order.AddLine(...)` enforcing invariants; make `OrderLine` package-private; introduce `OrderID` as a Value Object so identity isn't a bare string.

## Anti-Patterns
- "Domain-driven" naming with no enforced inversion — folder called `domain/` that imports `gorm` is theatre, not architecture
- Repositories for everything — proliferating repositories signal a missing aggregate root
- "Just one helper in domain that calls the HTTP client" — this kills the dependency rule
- Layer purity without use-case clarity — a clean import graph that nobody can navigate is still a failure
