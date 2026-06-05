---
name: incident-response
description: Structured procedure for investigating and resolving production incidents. Use when a service or infrastructure component is down or degraded, when alerts fire for critical thresholds (disk, CPU, connectivity), or when a user reports a production issue.
---
# Incident Response

Structured procedure for investigating and resolving production incidents.

## When to Use
**Live production degradation or outage** — the on-call path: stabilise,
mitigate, communicate, then root-cause. A non-urgent code bug with no
running-system pressure is **systematic-debugging** instead. During an incident
you'll often *use* the observability signals (**observability-setup**) and reach
for **rollback-plan** to revert the triggering change.

## Procedure
1. **Triage**: Assess severity and blast radius. Who/what is affected?
2. **Diagnose**: Verify Network → Process → Logs → Gateway. Follow the dependency chain.
3. **Mitigate**: Apply the fastest safe fix to restore service. Permanent fix can follow.
4. **Communicate**: Keep stakeholders informed of status and ETA.
5. **Document**: Record timeline, root cause, and fix in `<ai_hats_dir>/sessions/retros/`. Include what worked and what didn't.
6. **Follow up**: Create tasks for permanent fix, monitoring gaps, and process improvements.

## Completion
- Service restored and verified via health checks
- Incident report written in `<ai_hats_dir>/sessions/retros/`
- Follow-up tasks created in backlog

## Anti-Patterns
- Jumping to fix without diagnosis — leads to wrong fixes and longer outages
- Skipping communication — stakeholders assume the worst
- No follow-up tasks — same incident will repeat
