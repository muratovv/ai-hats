---
name: safety-guard
description: PreToolUse hooks enforcing global_rule_destructive_actions, rule_pause_before_shared_state_write and rule_backlog_discipline. Prevents destructive commands like in-place sed edits, deletion of protected data, and disk-formatting binaries, holds the pause before an irreversible shared-state write, and keeps the tracker backlog writable only through `rack`.
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Bash|run_command|execute
        script: hooks/safety_gate.py
      - matcher: Bash
        script: hooks/pre_bash_shared_state_guard.sh
      - matcher: Edit|Write|MultiEdit
        script: hooks/backlog_write_gate.py
license: MIT
---

# safety-guard

A PreToolUse runtime-hook for Bash/terminal tools. It parses the command line
(quote-aware) and matches **what the command targets**, not what it is named,
because `global_rule_destructive_actions` protects paths.

## The three outcomes

| Target                                                                                                                                                                                            | Outcome                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| Scratch space, build artefacts, `/tmp` — the cleanup `global_rule_resource_hygiene` mandates                                                                                                      | allowed                                      |
| Protected data — anything not regenerable from repo + toolchain; matched by the fast path `*.db` `*.sqlite*` `*.sql` `*.dump`, `volumes/` `data/` `storage/` `runs/`, `terraform.tfstate`, `.env` | denied; `AI_HATS_DESTRUCTIVE_ACK=1` opens it |
| Filesystem root, `$HOME`, disk-formatting (`mkfs.*`), `dd of=/dev/…`                                                                                                                              | denied; no flag opens it                     |

Every denial names the flag that unblocks it, or says plainly that none does.
A guard that can only say "no" pushes the agent toward blunt instruments.

`git push` is **not** handled here — `pre_bash_shared_state_guard.sh` owns it
(`rule_pause_before_shared_state_write`). Two gates on one concern means the
coarser one silently wins (HATS-1253).

## A wrapper does not hide the command

`timeout`, `nice`, `sudo`, `stdbuf` and their kin run another binary and eat a
variable number of operands first. The gate does **not** model each one's option
arity — the entry a table gets wrong makes the guard blind rather than merely
imprecise, which is how `timeout 5 rm -rf /` was allowed while `rm -rf /` was
denied (HATS-1682, measured). When a wrapper leads, every later slice of the
command is offered to the checks instead.

## Consent is declared by the ROLE, on the point

Where the supervisor is asked is not compiled into this hook. A role's
`composition.consent` names the points it wants asked on, in each application's
own point grammar:

```yaml
composition:
  consent:
    rack:
      tasks: [edge:plan--execute, edge:review--done]
    wt: [pre-merge]
```

The guard reads that declaration from the session envelope and raises the
question exactly there; the engine reads the same declaration in-lock and
refuses the move when no answer arrived. Changing the surface of consent is
therefore an edit to the role's topology, never to this gate.

The answer rides a one-shot ticket (`AI_HATS_CONSENT_TICKET`) minted by this
hook and bound to the card, the session, the exact argv, and one use. **The
question does not expire** — an expiry would only fence the supervisor's
thinking, since the ticket is written when the question is raised. Typing a
ticket, or any consent flag, inline is refused as a self-grant.

Nothing chained after a gated command: a redirect is dropped from the binding,
but a second command in the same call is not what the supervisor was shown.

## The tracker backlog is `rack`-only

`backlog_write_gate.py` (Edit/Write/MultiEdit) and a matching predicate in
`safety_gate.py` (Bash) deny writes under `<ai_hats_dir>/tracker/backlog/**` —
`rule_backlog_discipline` §1. A hand-edited card desynchronises the state
machine from its locks and audit trail, so the card moves with
`rack transition <ID> <state> --log "..."`, fields change with
`rack transition <ID> --set <field>=<value>`, and a document (`summary.md`,
`audit.md`, …) is written outside the tracker and brought in with
`rack transition <ID> --attach /tmp/summary.md:summary.md`. Reads (`cat`,
`grep`) are not touched, and `tasks/<ID>/plan.md` stays directly writable
(§1b) — it is the agent's own deliverable, not FSM-owned state.

`ai_hats_dir` is resolved from the TARGET path's own `ai-hats.yaml`, never from
`$AI_HATS_DIR`: that variable leaks between checkouts, and a worktree session
editing the main checkout's tracker must be judged by that tracker's config.

There is no per-call flag. If the tracker is broken and only a raw edit can
repair it, the supervisor exports `AI_HATS_BACKLOG_GATE_OFF=1` for the session.

## YOLO Mode

If you expect to run a large number of destructive commands (e.g. bulk file deletion, complex refactors requiring many in-place edits), you can request YOLO mode from the user to bypass the safety gate for the duration of your session.
To do this, attempt to execute `export AI_HATS_YOLO=1` in your Bash tool. The safety gate will intercept this and prompt the supervisor for permission.

**Export it; do not prefix it.** The gate reads `AI_HATS_YOLO` from its own
environment, so `AI_HATS_YOLO=1 <command>` inline is refused by design: a guard
the agent can switch off per-command is not a guard. Only an exported value,
which the supervisor's shell grants, takes effect.

**CRITICAL RULES FOR YOLO MODE:**

1. You may request YOLO mode from the supervisor.
2. You MUST NOT request YOLO mode arbitrarily or "just in case".
3. When requesting YOLO mode (which triggers a prompt to the supervisor), you MUST explicitly output a message stating *why* you need it and exactly what destructive actions you plan to perform.
