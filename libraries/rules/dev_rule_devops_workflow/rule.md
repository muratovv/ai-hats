# DevOps Infrastructure Workflow

## 1. Hierarchy of Tools
- **Level 1 (Terraform/OpenTofu)**: VM creation, storage allocation, network bridges.
- **Level 2 (Ansible)**: OS configuration, package installation, security hardening.
- **Level 3 (Docker Compose)**: Application-level orchestration.
- **Level 4 (Manual Shell)**: ONLY for investigation and emergency state recovery.

## 2. Observation Loop
Before proposing any change, run:
- `tofu plan` — check infrastructure drift
- `ansible-inventory --list` — verify host reachability
- `docker ps` — check service health

## 3. External Tool Logging
Every execution of `ssh`, `ansible-playbook`, `tofu/terraform`, or `docker` MUST be logged in the task card's `work_log`: tool name, command intent, justification, and result.

## 4. Production Readiness
Never consider an infrastructure task done without addressing:
- **Backup Strategy**: 3-2-1 rule. Verified recovery path.
- **Rollback Plan**: Explicit steps to revert to previous known-good state.
- **Observability**: Log rotation, centralized logging, alerting thresholds.
- **Reliability**: Resource limits (CPU/MEM), health checks, fault tolerance.

## 5. Time-Bounded Operations
All infrastructure commands MUST be wrapped in `timeout` (default 300s for playbooks).

## 6. Ask Before Heavy Run
Before executing long-running or impactful commands (`ansible-playbook`, `tofu apply`), ask the user for approval.
