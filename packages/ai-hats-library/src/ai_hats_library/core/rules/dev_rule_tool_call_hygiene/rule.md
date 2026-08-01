# Rule: Tool-Call Hygiene

**If a dedicated tool can express the operation, Bash must not be used for it —
whatever binary you would have reached for.** The names below are the common
spellings, not the boundary: `bat`, `fd`, `eza -R`, `git grep`, and
`sed -n '10,40p'` are the same violation as the classics.

- Search → **Grep** / **Glob** (not `grep`/`rg`/`find`/`ls -R`)
- Read known file → **Read** (not `cat`/`head`/`tail`)
- Edit → **Edit** / **Write** (not `sed -i`/`awk -i`)

Bash is appropriate for: `git`, build commands, multi-stage pipes, shell-only state (env vars, processes).

**Discipline:**

- Independent reads → single parallel block, never sequential.
- Initial codebase exploration ≤3–5 calls; broader → `Agent(Explore)`.
- 5+ similar sequential calls → STOP, batch or use a more targeted tool.

For the full anti-pattern table and worked examples → skill **tool-call-hygiene**.
