# Migration: global pipx → venv-first launcher (HATS-333)

Epic HATS-333 moves ai-hats to a **unified install flow** via a bash launcher + per-project venv. Removed: HATS-318 opt-in venv (`use-local`/`use-global`), HATS-330 mixed-install gate, the Python wrapper re-exec.

## TL;DR

```bash
# 1. One-time per host: install the launcher
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash

# 2. Per project (existing project on pipx):
cd ~/dev/my-project
ai-hats self update          # launcher creates .agent/ai-hats/.venv + installs

# 3. Optional cleanup of the old pipx install
pipx uninstall ai-hats
```

Your existing `ai-hats.yaml` and `.agent/ai-hats/` are preserved — no config edits required.

## What changed

| Before (≤ HATS-330) | After (HATS-333+) |
|---|---|
| `pipx install ai-hats` — global Python install | bash launcher at `~/.local/bin/ai-hats` (~30 lines) |
| Optional `ai-hats self use-local` for a local venv | Default: venv at `<ai_hats_dir>/.venv/`, always |
| Python wrapper `_maybe_reexec_into_local_venv` — re-exec when a local venv is present | the launcher execs directly |
| HATS-330 gate: `bump` refuses to start on global/local mismatch | removed — mixed install is no longer possible (no global Python install) |
| `ai-hats self update` — pip install via global pipx | `ai-hats self update` self-healing: heal-if-needed → pip install + auto-bump |
| `bootstrap.sh` created `<project>/.venv` + pip install + init | `bootstrap.sh` = install-launcher → self update → init |

## Migration scenarios

### Scenario A: "I have global pipx and the project works"

```bash
# 1. Install the launcher (does NOT touch the pipx install)
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash

# 2. Verify the launcher is in $PATH (you may need to add ~/.local/bin to PATH)
which ai-hats   # → expect: /Users/<you>/.local/bin/ai-hats

# 3. In each existing project:
cd ~/dev/my-project
ai-hats self update            # creates .agent/ai-hats/.venv + installs ai-hats
ai-hats config status          # smoke-test — prints the current composition

# 4. Optional: remove the old pipx install
pipx uninstall ai-hats
```

The launcher resolves `~/.local/bin/ai-hats` ahead of the `pipx` shim — `which ai-hats` will confirm. If the pipx shim shadows it, add `export PATH="$HOME/.local/bin:$PATH"` to the top of your shell rc.

### Scenario B: "I'm on a HATS-318 use-local venv"

Good news — your venv is already in the right place (`<ai_hats_dir>/.venv/`).

```bash
# 1. Install the launcher
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash

# 2. The launcher picks up the existing venv automatically
cd ~/dev/my-project
ai-hats config status          # works — uses your existing venv

# 3. Update (optional)
ai-hats self update            # pip install --upgrade into the existing venv

# 4. Remove the old global install (if any)
pipx uninstall ai-hats         # safe — no longer used
```

### Scenario C: "I want my own override venv" (e.g. a project venv)

```bash
# 1. Edit ai-hats.yaml:
echo 'venv_path: .venv' >> ai-hats.yaml          # relative
# or
echo 'venv_path: /opt/shared-venv' >> ai-hats.yaml   # absolute

# 2. Create your venv (user-owned!)
python3 -m venv /opt/shared-venv                 # or the path you chose
/opt/shared-venv/bin/pip install "ai-hats @ git+ssh://git@github.com/muratovv/ai-hats.git"

# 3. The launcher reads venv_path from the yaml automatically
ai-hats config status
```

⚠️ An override venv is yours. If it breaks, ai-hats will **not** auto-heal it (only the default location). Recovery — you recreate the venv + pip install.

### Scenario D: "Stuck — I tried self update before bootstrap and got a hard fail"

```bash
# If the ai-hats command can't be found:
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash
ai-hats self update

# If the launcher is present but something was interrupted mid-flight:
rm -rf .agent/ai-hats/.venv      # removes the default venv (data under tracker/ + sessions/ is preserved)
ai-hats self update              # the launcher recreates a clean venv
```

### Scenario E: "A host Python upgrade broke the venv (the Proxmox case)"

```bash
ai-hats self update          # launcher detects broken symlinks → auto-recreate + pip install
```

This works only for the default location (`<ai_hats_dir>/.venv/`). Override venvs are user-managed.

## Recovery checklist

| Symptom | Command |
|---|---|
| `ai-hats: command not found` | install-launcher one-liner |
| `ai-hats: venv missing at <path>` | `ai-hats self update` |
| `ai-hats: venv exists but ai-hats binary is missing` | `ai-hats self update` |
| `ai-hats: override venv ... is missing or broken` | `python3 -m venv <path> && <path>/bin/pip install 'ai-hats @ ...'` |
| Something is broken deep inside (import error) | `rm -rf .agent/ai-hats/.venv && ai-hats self update` |
| Full wipe (without data) | `rm -rf .agent/ai-hats/.venv && ai-hats self update` |
| Full wipe (WITH data loss) | `rm -rf .agent/ai-hats/ && ai-hats self update && ai-hats self init -r <role> -p <provider>` |

## What's left of the old world

- `pipx install ai-hats` — no longer the recommended path. It still works,
  but does not use the launcher heal — manual update via `pipx upgrade
  ai-hats`. We recommend migrating to the launcher.
- `<project>/.venv/` — if you have a project venv with ai-hats (pre-HATS-318
  bootstrap) — that's a separate story. The launcher does not see it
  (default location is `<ai_hats_dir>/.venv/`, not `<project>/.venv/`).
  Either migrate to the default (delete `<project>/.venv/.../ai-hats`, run
  launcher self update), or set `venv_path: .venv` in the yaml — the
  launcher will then pick it up.

## Cross-references

- HATS-333 (epic — venv-first install)
- HATS-334 (venv_path config foundation)
- HATS-336 (bootstrap.sh refactor)
- HATS-337 (python cleanup — wrapper / HATS-318 / HATS-330 removed)
- HATS-339 (bash launcher implementation)
- HATS-318 — replaced (was: opt-in local venv)
- HATS-330 — removed (was: mixed-install gate, obsolete by construction)
- HATS-315 — venv research, replaced by the HATS-333 launcher architecture
- `docs/migration-311.md` — HATS-316 layout migration (still relevant)
