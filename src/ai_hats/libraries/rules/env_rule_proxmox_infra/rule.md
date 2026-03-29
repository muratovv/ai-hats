# Proxmox Infrastructure Rules

## Tool Hierarchy
- **Level 1 (Terraform/OpenTofu)**: VM creation, storage allocation, network bridges.
- **Level 2 (Ansible)**: OS configuration, package installation, security hardening.
- **Level 3 (Docker Compose)**: Application-level orchestration.
- **Level 4 (Manual Shell)**: ONLY for investigation and emergency state recovery.

## Observation Loop
Before proposing any infrastructure change, run:
- `tofu plan` — check infrastructure drift
- `ansible-inventory --list` — verify host reachability
- `docker ps` — check service health
