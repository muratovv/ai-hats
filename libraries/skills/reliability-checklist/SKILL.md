# Reliability Checklist

Verify production readiness of a service or infrastructure change.

## Checklist
1. **Resource limits**: CPU and memory limits set on all containers/VMs. No unbounded resource consumption.
2. **Health checks**: Liveness and readiness probes configured. Restart policy defined.
3. **Fault tolerance**: Single points of failure identified and mitigated (replication, failover, load balancing).
4. **Graceful degradation**: Service behaves predictably under partial failure (upstream down, DB slow).
5. **Right-sizing**: Resources allocated based on actual usage data, not estimates. Review after first week of production load.
