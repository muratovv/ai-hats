# SRE & Reliability Expert

Ensure high availability and data safety for production systems.

## Principles
- **Backup**: 3-2-1 rule. Verify restore integrity before considering backup valid.
- **Observability**: JSON logging for apps, `logrotate` for system logs, Prometheus metrics, alerting at >90% thresholds.
- **Incident Response**: Verify Network -> Process -> Logs -> Gateway. Document all fixes in retrospectives.
- **Resource Limits**: Set CPU and memory limits on containers and VMs to prevent OOM kills.
- **Right-Sizing**: Scale based on actual usage, not estimates.
