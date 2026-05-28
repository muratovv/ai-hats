---
name: ansible-ops
description: Ansible playbook management covering inventory, vault, syntax checks, and idempotency. Use when writing or running Ansible playbooks, configuring servers or deploying services, or managing secrets with Ansible Vault.
---
# Ansible Ops

Manage infrastructure using Ansible playbooks and roles.

## When to Use
- Writing or running Ansible playbooks
- Configuring servers, deploying services
- Managing secrets with Ansible Vault

## Conventions
- **Inventory**: Always explicitly specify the inventory file with `-i`.
- **Vault Safety**: Never run commands requiring interactive password entry (`--ask-vault-pass`). Generate the command for the user to run instead.
- **Syntax First**: Run `ansible-lint` or `ansible-playbook --syntax-check` before presenting a playbook.
- **Timeout**: Prefix playbook runs with `timeout 300s` to prevent hangs.
- **Idempotency**: Every playbook must be safe to re-run. Use `creates:`, `when:`, and handler-based restarts.

## Anti-Patterns
- Running playbooks without `--syntax-check` first — catch errors before execution
- Interactive password prompts (`--ask-vault-pass`) — generate commands for the user
- Non-idempotent tasks — every playbook must be safe to re-run
