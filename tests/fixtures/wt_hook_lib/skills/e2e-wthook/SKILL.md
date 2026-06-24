---
name: e2e-wthook
description: E2E fixture skill (HATS-823). Declares worktree wt_in/wt_out lifecycle hooks so the worktree-hook e2e can prove the materialize → create/teardown → live-script chain through the real ai-hats binary. Not shipped.
ai_hats:
  worktree:
    wt_in:
      - script: hooks/seed.sh
    wt_out:
      - script: hooks/drain.sh
        on: [merge, discard, cleanup]
---

# e2e-wthook

E2E fixture skill (HATS-823). Exists only so the worktree-hook e2e can compose
a role that declares `worktree:` hooks through the real `ai-hats` binary. Not
part of the shipped library.
