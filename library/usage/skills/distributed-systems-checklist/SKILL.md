---
name: distributed-systems-checklist
description: Audit distributed services against the 8 fallacies and the CAP trade-off. Use during design review for any service that crosses a network boundary, during incident triage when root cause is unclear and a network or IPC layer is involved, or when drafting an architectural ADR for new distributed components.
---
# Distributed Systems Checklist

Surface unstated assumptions in distributed-system designs and incidents.

## When to Use
- Design review for any service that crosses a network boundary
- Incident triage when root cause is unclear and a network/IPC layer is involved
- Architectural ADR drafting for new distributed components

## Fallacies Checklist
For each, state the **assumption made** and the **mitigation in place**. Mark `N/A` only with explicit justification.

1. **The network is reliable**: retries, circuit breakers, idempotency keys, deadlines.
2. **Latency is zero**: budget per call, p99 target, async where viable, batch I/O.
3. **Bandwidth is infinite**: payload size limits, compression, pagination, streaming for large data.
4. **The network is secure**: mTLS, authn/authz at every hop, no plaintext credentials in transit.
5. **Topology doesn't change**: service discovery, no hard-coded IPs/hosts, graceful re-resolution.
6. **There is one administrator**: explicit ownership, runbook, on-call mapping per component.
7. **Transport cost is zero**: cost-aware design (egress, cross-AZ), measured not assumed.
8. **The network is homogeneous**: protocol/version negotiation, no implicit "all clients are v2".

## CAP Trade-off
Document for every distributed datastore/coordinator: which **two of {Consistency, Availability, Partition tolerance}** the system guarantees, and what behavior occurs under partition. No "we have all three".

## Completion
- 8 fallacies — each has `assumption` + `mitigation` (or justified `N/A`)
- CAP statement — written, with partition behavior
- Findings appended to design ADR or incident retrospective

## Anti-Patterns
- Rubber-stamp "we use HTTP, so it's fine" without listing concrete mitigations
- Skipping CAP analysis because the system "feels eventually consistent"
- Treating the checklist as a one-time gate instead of a re-verification on every topology change
