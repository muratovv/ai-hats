---
name: observability-setup
description: Logging, metrics, alerting, and dashboards setup (RED method)
---
# Observability Setup

Ensure every service is observable: logs, metrics, alerts.

## When to Use
- Deploying a new service or container
- Reviewing observability gaps after an incident
- Setting up monitoring infrastructure

## Procedure
1. **Logging**: JSON format for applications, `logrotate` for system/container logs. Centralized collection where possible.
2. **Metrics**: Expose `/metrics` for Prometheus. Cover CPU, memory, disk IO, and application-specific counters.
3. **Alerting**: Define thresholds for critical resources (disk >90%, CPU sustained >80%, memory >85%). Route alerts to the appropriate channel.
4. **Dashboards**: Key service health visible at a glance. Latency, error rate, throughput (RED method).
5. **Retention**: Define log and metric retention policy. Balance cost vs. debugging needs.

## Completion
- Service has structured logging, metrics endpoint, and alerts
- Dashboard shows RED metrics (rate, errors, duration)
- Retention policy documented

## Anti-Patterns
- Metrics without alerts — data nobody watches is useless
- Alerts without runbooks — alert fires and nobody knows what to do
- Logging PII or secrets — observability must not compromise security
