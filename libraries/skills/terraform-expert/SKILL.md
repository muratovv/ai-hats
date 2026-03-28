# Terraform/OpenTofu Expert

Maintain robust, DRY, and secure infrastructure as code.

## Principles
- **State**: `tofu plan` before `apply`. NEVER edit state manually. Pin provider versions.
- **DRY**: Use modules for common resources (VPC, VM, SG). Use `locals` for naming logic.
- **Data-Driven**: Fetch existing IDs via `data` sources. Validate inputs with `validation` blocks.
- **Safety**: `sensitive = true` for secrets. `prevent_destroy = true` for production resources.
- **Scaling**: Prefer `for_each` over `count` for resource sets.
