---
name: api-evolution-checklist
description: Audit public-API/contract changes against Hyrum, Tesler, Leaky Abstractions, and Lehman laws. Use when changing a public API signature, response shape, or error contract, refactoring a shared interface used by two or more callers, drafting an interface ADR, or reviewing a PR touching api/, proto/, or OpenAPI specs.
license: MIT
---
# API Evolution Checklist

Vet a public-API or cross-component contract change against four laws that describe how interfaces decay over time.

## When to Use
Specifically about the **compatibility** of a change to a shared/public
interface — what breaks downstream callers, how the contract decays over time.
The *decision record* for a versioning/deprecation choice is **adr-manager**
(this checklist often feeds one). Not for internal-only refactors with a single
caller — the cost is justified by ≥2 consumers of the surface.

## Checklist

For each item: state the **assumption / claim** and the **mitigation in place**. Mark `N/A` only with explicit justification.

1. **Hyrum's Law — observable behavior is a contract.**
   List undocumented behaviors that callers may already depend on: timing/latency, error message text, ordering, retry semantics, optional-field defaults, idempotency, encoding edge cases. For each, decide: preserve, deprecate-with-window, or break-with-migration.
2. **Tesler's Law — complexity is conserved, not eliminated.**
   When the change "simplifies" the API, name where the removed complexity moved (caller code, runtime check, ops runbook, migration script). If you cannot point to it, the simplification is illusory.
3. **Law of Leaky Abstractions — name the leaks.**
   Document leak points the abstraction does NOT hide: error propagation, timeouts/cancellation, transactional boundaries, encoding/locale, performance cliffs. Make leaks part of the contract, not surprises.
4. **Lehman's Laws — plan the next evolution, not just this one.**
   Versioning scheme, deprecation window, sunset criteria, migration tooling, observability for old-vs-new traffic. A contract without an evolution plan freezes into legacy.

## Completion
- All 4 items have `assumption` + `mitigation` (or justified `N/A`)
- Findings appended to the API ADR or PR description
- For breaking changes: deprecation window + migration path documented before merge

## Example
A team replaces `GET /users?status=active` with `GET /users` returning all users (filter via client). Hyrum: existing dashboards depend on the implicit "active-only" set — break-with-migration, not silent change. Tesler: filtering complexity moved to every caller — provide a client lib helper. Leaky: old endpoint streamed paginated; new one returns full list and OOMs at 100k users — keep pagination contract. Lehman: introduce `v2/users` while `v1/users` returns deprecation header; sunset after 2 release cycles.

## Anti-Patterns
- "It's just a refactor, no contract change" — if anyone outside this module calls it, it is a contract
- Treating documentation as the contract — observable behavior outranks docs (Hyrum)
- One-shot deprecation: removing without a window forces caller fire-drills
- Hiding leaks behind nicer names instead of documenting them
