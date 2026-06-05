---
name: network-documentation
description: Maintain network topology and DNS documentation as source of truth. Use after any network topology change (new service, port, tunnel, route), when onboarding a new service or domain, or during periodic accuracy audits of network docs.
---
# Network Documentation

Maintain the network source of truth throughout SRE tasks.

## When to Use
Keeping the **network source-of-truth accurate** after a topology change. The
sibling **network-change-verification** is the *act of confirming the change
works* (pre/post probes); this skill captures what the topology now *is* once it
does. Don't conflate proving connectivity with recording it.

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
