# Reliability Checklist

Verify production readiness of a service or infrastructure change.

## When to Use
- Before promoting a service to production
- After significant infrastructure changes
- Periodic production readiness reviews

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
