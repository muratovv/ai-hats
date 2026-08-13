# Rule: Tool-Call Hygiene

**Reach for the narrowest tool this session actually offers; drop to Bash only
when none of them expresses the operation.** The names below are the common
spellings, not the boundary: `bat`, `fd`, `eza -R`, `git grep`, and
`sed -n '10,40p'` are the same violation as the classics.

- Search → **Grep** / **Glob** (not `grep`/`rg`/`find`/`ls -R`)
- Read known file → **Read** (not `cat`/`head`/`tail`)
- Edit → **Edit** / **Write** (not `sed -i`/`awk -i`)

**When a listed tool is absent from the session, the shell binary IS the right
call** — a tool you do not have is not a tool you are refusing to use. Absence
is observable, not assumed: the tool is missing from your available set, or
calling it returns *no such tool available*. Never infer it from a failed
search or an awkward pattern. Falling back tightens the budget below rather
than loosening it: raw output costs context that a dedicated tool would have
structured, so batch harder, scope tighter, and never let a fallback become a
run of one-off calls.

Bash is appropriate for: `git`, build commands, multi-stage pipes, shell-only state (env vars, processes).

**Discipline:**

- Independent reads → single parallel block, never sequential.
- Initial codebase exploration ≤3–5 calls; broader → `Agent(Explore)`.
- 5+ similar sequential calls → STOP, batch or use a more targeted tool.

For the full anti-pattern table and worked examples → skill **tool-call-hygiene**.
