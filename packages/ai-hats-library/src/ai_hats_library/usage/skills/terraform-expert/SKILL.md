---
name: terraform-expert
description: Terraform/OpenTofu IaC covering state management, DRY modules, and safety practices. Use when writing or reviewing Terraform/OpenTofu configurations, planning infrastructure changes, or managing state and provider versions.
license: MIT
---
# Terraform/OpenTofu Expert

Maintain robust, DRY, and secure infrastructure as code.

## When to Use
**Provisioning and declaring infrastructure** — resources, modules, state,
provider versions. Its sibling **ansible-ops** owns the next layer: configuring
and deploying onto the hosts Terraform created. Rule of thumb — if it changes
*what exists*, it's here; if it changes *what runs on what exists*, it's
ansible-ops.

## Conventions
- **State**: `tofu plan` before `apply`. NEVER edit state manually. Pin provider versions.
- **DRY**: Use modules for common resources (VPC, VM, SG). Use `locals` for naming logic.
- **Data-Driven**: Fetch existing IDs via `data` sources. Validate inputs with `validation` blocks.
- **Safety**: `sensitive = true` for secrets. `prevent_destroy = true` for production resources.
- **Scaling**: Prefer `for_each` over `count` for resource sets.

## Bundled Rules

### IaC Tools
1. **Documentation First**: Check current official docs before generating any IaC config.
2. **No Hardcoded Versions**: Verify OS images, ISOs, container tags exist before referencing.
3. **Environment Awareness**: Consult project environment docs for endpoints, storage, networks.

## Anti-Patterns
- `tofu apply` without `plan` — always review the plan first
- Manual state editing — use `tofu state mv/rm` commands
- Unpinned provider versions — leads to non-reproducible infrastructure
