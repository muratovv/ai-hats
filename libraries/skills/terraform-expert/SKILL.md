# Terraform/OpenTofu Expert

Maintain robust, DRY, and secure infrastructure as code.

## When to Use
- Writing or reviewing Terraform/OpenTofu configurations
- Planning infrastructure changes
- Managing state and provider versions

## Conventions
- **State**: `tofu plan` before `apply`. NEVER edit state manually. Pin provider versions.
- **DRY**: Use modules for common resources (VPC, VM, SG). Use `locals` for naming logic.
- **Data-Driven**: Fetch existing IDs via `data` sources. Validate inputs with `validation` blocks.
- **Safety**: `sensitive = true` for secrets. `prevent_destroy = true` for production resources.
- **Scaling**: Prefer `for_each` over `count` for resource sets.

## Anti-Patterns
- `tofu apply` without `plan` — always review the plan first
- Manual state editing — use `tofu state mv/rm` commands
- Unpinned provider versions — leads to non-reproducible infrastructure
