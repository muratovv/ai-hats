# Observability Setup

Ensure every service is observable: logs, metrics, alerts.

## Procedure
1. **Logging**: JSON format for applications, `logrotate` for system/container logs. Centralized collection where possible.
2. **Metrics**: Expose `/metrics` for Prometheus. Cover CPU, memory, disk IO, and application-specific counters.
3. **Alerting**: Define thresholds for critical resources (disk >90%, CPU sustained >80%, memory >85%). Route alerts to the appropriate channel.
4. **Dashboards**: Key service health visible at a glance. Latency, error rate, throughput (RED method).
5. **Retention**: Define log and metric retention policy. Balance cost vs. debugging needs.
