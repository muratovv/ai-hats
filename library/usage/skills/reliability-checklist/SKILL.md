---
name: reliability-checklist
description: Production readiness verification for services and infrastructure. Use before promoting a service to production, after significant infrastructure changes, or during periodic production readiness reviews.
---
# Reliability Checklist

Verify production readiness of a service or infrastructure change.

## When to Use
The **production-readiness gate** before promoting a service — a pass/fail audit
spanning SLOs, runbooks, capacity, rollback, observability. It *checks that*
observability exists; actually building those logs/metrics/alerts is
**observability-setup**. For the network-boundary correctness behind a
distributed service, see **distributed-systems-checklist**.

## Checklist
1. **Resource limits**: CPU and memory limits set on all containers/VMs. No unbounded resource consumption.
2. **Health checks**: Liveness and readiness probes configured. Restart policy defined.
3. **Fault tolerance**: Single points of failure identified and mitigated (replication, failover, load balancing).
4. **Graceful degradation**: Service behaves predictably under partial failure (upstream down, DB slow).
5. **Right-sizing**: Resources allocated based on actual usage data, not estimates. Review after first week of production load.

## Completion
- All 5 checklist items verified with evidence (command output, config snippet)
- Findings reported: pass/fail per item with remediation for failures

## Anti-Patterns
- Rubber-stamping — checking boxes without verifying actual state
- Skipping right-sizing — over-provisioning wastes resources, under-provisioning causes outages
- One-time check — reliability must be re-verified after significant changes
