---
name: network-documentation
description: Maintain network topology and DNS documentation as source of truth
---
# Network Documentation

Maintain the network source of truth throughout SRE tasks.

## When to Use
- After any network topology change (new service, port, tunnel, route)
- When onboarding a new service or domain
- Periodic accuracy audits of network docs

## Procedure
1. **Locate index**: Find the project's infrastructure index (e.g., `INFRASTRUCTURE.md`).
2. **Update mapping**: Maintain `Domain → Internal IP:Port → Tunnel/Gateway` table after every topology change.
3. **Record rationale**: Document WHY a specific port, bridge, or route was chosen.
4. **Verify accuracy**: Cross-check the documented state against the live state periodically.

## Completion
- Infrastructure index updated with current topology
- All domains/ports/tunnels have documented rationale
- Documented state matches live state

## Anti-Patterns
- Undocumented port assignments — leads to conflicts and debugging nightmares
- Documenting what but not why — future changes will break unknown assumptions
- Stale docs — worse than no docs because they mislead
