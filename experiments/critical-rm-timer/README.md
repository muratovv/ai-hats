# Critical-path removal and the harness's timer

`probe.py` answers one question: **when Claude Code flags an `rm` of a critical
path, can the ai-hats consent gate ask the person instead, and does the question
then wait?**

Claude Code treats `rm`/`rmdir` of a critical path (the filesystem root, a
top-level directory, the home directory, the working directory and its
ancestors, and targets it cannot resolve) as a circuit breaker. A hook `allow`
never approves one. In `auto` and `bypassPermissions`, the terminal prompt for it
counts down from two minutes and then denies. Headless `auto` denies at once.
`CLAUDE_CODE_DISABLE_DANGEROUS_RM_TIMEOUT=1` turns the timer off. In `auto`, the
removal then goes to the classifier instead.

Every arm runs `rm -rf <cwd>` on a throwaway working directory. The verdict is read
from the filesystem: the directory is gone, or it is not.

```bash
experiments/critical-rm-timer/probe.py headless                  # all headless arms, holder answers deny
experiments/critical-rm-timer/probe.py headless --arms H3,H5 --hold 150   # leave the question open
experiments/critical-rm-timer/probe.py headless --arms C1,H3,H5 --answer allow
experiments/critical-rm-timer/probe.py tui-run T1                # a person runs T1..T4 in a terminal
experiments/critical-rm-timer/probe.py tui-read                  # what the harness returned there
```

Headless is the raw stream-json wire with `--permission-prompt-tool stdio`. It is
the same wire `ai-hats headless` holds. The probe checks `system/init`, because
haiku silently runs `auto` as `default`. An arm whose mode did not take is
reported `INVALID`, never counted.

## Recorded verdict

Claude Code **2.1.283**, 2026-09-28, macOS, sonnet.

| arm | mode   | hook `ask` | timer env | terminal (a person watched)       | headless                                                  |
| --- | ------ | ---------- | --------- | --------------------------------- | --------------------------------------------------------- |
| 1   | auto   | –          | –         | countdown, then denial            | `permission_denied` (`safetyCheck`) at once, nobody asked |
| 2   | auto   | ✔          | –         | countdown anyway                  | same as 1: the hook's `ask` is dropped                    |
| 3   | auto   | ✔          | ✔         | untimed "Do you want to proceed?" | `can_use_tool` (`safetyCheck`); allow runs, deny blocks   |
| 4   | auto   | –          | ✔         | not run                           | **EXECUTED by the classifier, nobody asked**              |
| 5   | bypass | ✔          | –         | countdown                         | `can_use_tool`; allow runs                                |

- **Controls.** The hook fired in every arm (per-arm hook log). C1 (a harmless
  `touch` with a hook `ask`) reached the holder as `decision_reason_type: hook`,
  which shows the question channel works.
- **Hold.** In arm 3 and arm 5 the question was left unanswered for 150 s. The
  measured gap from `tool_use` to `tool_result` was 155 s and 150.5 s. No
  `control_cancel_request` arrived: headless has no timer on the holder's
  question.
- **Question text.** In arm 3 the question the person sees is Claude Code's
  `safetyCheck` reason, not the hook's. That holds in the terminal and on the
  wire alike.

So only arm 3 waits for a person in `auto`: a hook `ask` **and** the env var. The
env var without the hook is arm 4. What this decided:
`docs/adr/0039-the-consent-gate-asks-for-critical-path-removals.md`.

Re-run it before trusting that ADR on a newer Claude Code. It is a claim about
someone else's release, and nothing in CI can hold it.
