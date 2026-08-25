---
name: safety-guard
description: PreToolUse hooks that refuse destructive commands, hold the pause before an irreversible shared-state write, keep the tracker writable only through `rack`, and turn a consent-declared move into a question for the supervisor. Infrastructure — read it to diagnose a block, not to invoke it.
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

## Some moves are consent-gated

On a few moves the guard turns your command into a question for the supervisor
instead of running it. Which moves those are is the role's declaration, not
yours to know here. Three things you can do, and nothing else:

- **Run the command bare and let the question happen.** Do not prefix it with a
  consent flag or a ticket — the guard mints those, and one you typed is refused
  as a self-grant.
- **Present what you are asking approval for, and stop.** While a question is
  waiting, do not nudge or re-ask: it does not expire, and the supervisor may
  take as long as reading needs.
- **If the engine refused for want of an answer, say so and stop.** A re-run
  helps only where a question can be raised at all; on a surface with no hooks
  it buys a second refusal. What opens the move is the supervisor typing the
  verb himself (`consent <type> <minutes>`), and his window then covers the
  series without asking again. Mutating the command never helps: the answer
  binds to the exact call he was shown, and a consent flag you add yourself is
  a self-grant.

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
