# Ansible Ops

Manage infrastructure using Ansible playbooks and roles.

## Core Rules
- **Inventory**: Always explicitly specify the inventory file with `-i`.
- **Vault Safety**: Never run commands requiring interactive password entry (`--ask-vault-pass`). Generate the command for the user to run instead.
- **Syntax First**: Run `ansible-lint` or `ansible-playbook --syntax-check` before presenting a playbook.
- **Timeout**: Prefix playbook runs with `timeout 300s` to prevent hangs.
- **Idempotency**: Every playbook must be safe to re-run. Use `creates:`, `when:`, and handler-based restarts.
