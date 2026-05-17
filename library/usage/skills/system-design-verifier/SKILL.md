---
name: system-design-verifier
description: Audit architectures against failure modes (resilience, scalability, consistency, security)
---
# System Design Verifier

Audit proposed or existing architectures against common failure modes.

## When to Use
- Reviewing a new system design or architecture proposal
- Before major infrastructure changes
- Post-incident analysis of architectural weaknesses

## Procedure
1. **Identify Interfaces:** Map all network boundaries (REST, gRPC, queues, DB connections).
2. **Verify Resilience:** For each boundary — what happens if this dependency is down or slow? Ensure timeouts, retries, and circuit breakers are designed.
3. **Verify Scalability:** Identify stateful components. Can they scale horizontally? Are there single points of failure?
4. **Verify Consistency:** Is data eventually consistent or strongly consistent? Does the business logic handle eventual consistency correctly (e.g., Sagas for distributed transactions)?
5. **Verify Security:** Are interfaces authenticated? Are trust boundaries documented? Is data encrypted in transit and at rest?
6. **Report:** Produce a structured findings list: component, risk, severity, recommendation.

## Completion
- All interfaces mapped and verified across 4 dimensions (resilience, scalability, consistency, security)
- Findings report produced with severity and recommendations

## Anti-Patterns
- Reviewing only the happy path — failure modes are the whole point
- Ignoring data consistency model — leads to subtle bugs under load
- No severity ranking — treating all findings equally dilutes focus
