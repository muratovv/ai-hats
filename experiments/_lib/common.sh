# shellcheck shell=bash
# Shared helpers for experiment scripts (HATS-1053). Source, don't execute.
# shellcheck disable=SC2034  # SCRUB is consumed by the sourcing scripts

# Ambient session env leaks the parent project into the sandbox: sessions land in
# the wrong project dir, ownership checks read the wrong actor, git plumbing hits
# the real repo (HATS-897 / 944 / 955 / 982 / 886). Prefix every sandbox command.
SCRUB=(env
  -u AI_HATS_DIR -u AI_HATS_PROJECT_DIR
  -u AI_HATS_SESSION_ID -u AI_HATS_ROOT_PID
  -u AI_HATS_VENV
  -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE
)
