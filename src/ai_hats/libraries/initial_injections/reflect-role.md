# Role coherence audit — {target_role}

Apply **role-coherence-protocol** end-to-end and emit the report
between `BEGIN_REFLECT` / `END_REFLECT` before exiting.

## Inputs (read via Read / Glob tools)

All inputs are files on disk — read them as you need, in any order;
do not ask the user for the content.

- **Target role composition** — layered breakdown at:
  `{composed_dir}/`
  - `manifest.yaml` — name, priorities, and the lists of bundled
    traits / rules / skills (start here to get the structure).
  - `role-injection.md` — the role's own injection text (if present).
  - `overlay-injection.md` — project-overlay text (if present).
  - `traits/<name>.md` — per-trait injection text.
  - `rules/<name>.md` — bundled rule bodies.
  - `skills/<name>.md` — bundled skill bodies.

- **Project CLAUDE.md** — user-owned root prompt:
  `{project_dir}/CLAUDE.md` (may not exist on fresh projects).

- **User rules overlay** — project-specific overrides:
  `{project_dir}/.agent/ai-hats/user-rules/*.md` (may be empty).

Begin by reading the manifest, then walk the components and the user
context per the protocol.
