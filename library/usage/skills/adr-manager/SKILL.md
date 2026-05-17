---
name: adr-manager
description: Document architectural decisions using Michael Nygard ADR format
---
# ADR Manager

Standardize documentation of architectural decisions for traceability.

## When to Use
When a significant architectural choice is made: picking a database, deciding on an API protocol,
adopting a new pattern, choosing between competing approaches.

## Format (Michael Nygard)
- **Title:** Short noun phrase
- **Context:** What is the problem we are solving?
- **Decision:** What is the change we are making?
- **Status:** Proposed / Accepted / Deprecated / Superseded
- **Consequences:** What becomes easier or harder as a result?

## Storage
Save all ADRs sequentially in `docs/adr/` (e.g., `0001-use-grpc-for-internal-services.md`).

## Completion
- ADR file created in `docs/adr/` with sequential numbering
- All 5 sections filled (Title, Context, Decision, Status, Consequences)

## Bundled Rules

### Architecture Foundation
1. **Context Management**: Summarize architectural state into ADRs. Operate at C4 level.
2. **Distributed Systems**: Design for failure — retries, circuit breakers, graceful degradation.
3. **API-First**: Define contracts before systems (OpenAPI/Protobuf). Strict backward compatibility.
4. **Delegation**: Delegate deep dives to sub-agents, synthesize findings into ADR updates.

## Anti-Patterns
- Missing consequences — the most valuable section, forces thinking about trade-offs
- Documenting after the fact without context — write the ADR when the decision is fresh
- ADR without status — unclear whether this is a proposal or accepted decision
