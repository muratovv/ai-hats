# Migration: global pipx → venv-first launcher (HATS-333)

Эпик HATS-333 переводит ai-hats на **единый install flow** через
bash launcher + per-project venv. Удалено: HATS-318 opt-in venv
(`use-local`/`use-global`), HATS-330 mixed-install gate, python wrapper
re-exec.

## TL;DR

```bash
# 1. One-time per host: install launcher
curl -sSL https://github.com/muratovv/ai-hats/raw/main/scripts/install-launcher.sh | bash

# 2. Per project (existing project on pipx):
cd ~/dev/my-project
ai-hats self update          # launcher creates .agent/ai-hats/.venv + installs

# 3. Optional cleanup of old pipx install
pipx uninstall ai-hats
```

Существующий `ai-hats.yaml` и `.agent/ai-hats/` сохраняются — никаких
конфигурационных правок не требуется.

## Что изменилось

| До (≤ HATS-330) | После (HATS-333+) |
|---|---|
| `pipx install ai-hats` — global python install | bash launcher в `~/.local/bin/ai-hats` (~30 строк) |
| Опционально `ai-hats self use-local` для local venv | Default: venv в `<ai_hats_dir>/.venv/` всегда |
| python wrapper `_maybe_reexec_into_local_venv` — re-exec при наличии local venv | launcher делает exec напрямую |
| HATS-330 gate: `bump` отказывается стартовать при mismatch global/local | Удалён — mixed install невозможен (no global python install) |
| `ai-hats self update` — pip install через global pipx | `ai-hats self update` self-healing: heal-if-needed → pip install + auto-bump |
| `bootstrap.sh` создавал `<project>/.venv` + pip install + init | `bootstrap.sh` = install-launcher → self update → init |

## Сценарии migration

### Сценарий A: «У меня global pipx + проект работает»

```bash
# 1. Install launcher (does NOT touch pipx install)
curl -sSL https://github.com/muratovv/ai-hats/raw/main/scripts/install-launcher.sh | bash

# 2. Verify launcher in PATH (may need to add ~/.local/bin to PATH)
which ai-hats   # → expect: /Users/<you>/.local/bin/ai-hats

# 3. In each existing project:
cd ~/dev/my-project
ai-hats self update            # creates .agent/ai-hats/.venv + installs ai-hats
ai-hats config status          # smoke-test — выводит текущую composition

# 4. Optional: remove old pipx install
pipx uninstall ai-hats
```

Launcher проверит `~/.local/bin/ai-hats` приоритетнее `pipx` shim — `which ai-hats` это покажет. Если pipx shim перекрывает — добавь `export PATH="$HOME/.local/bin:$PATH"` в начало shell rc.

### Сценарий B: «У меня HATS-318 use-local venv»

Хорошие новости — твой venv уже в правильном месте (`<ai_hats_dir>/.venv/`).

```bash
# 1. Install launcher
curl -sSL https://github.com/muratovv/ai-hats/raw/main/scripts/install-launcher.sh | bash

# 2. Launcher автоматически подхватит существующий venv
cd ~/dev/my-project
ai-hats config status          # works — uses your existing venv

# 3. Update (optional)
ai-hats self update            # pip install --upgrade в существующий venv

# 4. Remove old global install (если был)
pipx uninstall ai-hats   # safe — больше не использует
```

### Сценарий C: «Хочу свой override venv» (например, проектный)

```bash
# 1. Edit ai-hats.yaml:
echo 'venv_path: .venv' >> ai-hats.yaml   # relative
# или
echo 'venv_path: /opt/shared-venv' >> ai-hats.yaml   # absolute

# 2. Create your venv (user-owned!)
python3 -m venv /opt/shared-venv          # or path you chose
/opt/shared-venv/bin/pip install "ai-hats @ git+ssh://git@github.com/muratovv/ai-hats.git"

# 3. Launcher автоматически читает venv_path из yaml
ai-hats config status
```

⚠️ Override venv — твой. Если он сломается, ai-hats **не** будет авто-чинить (только default location). Recovery — сам пересоздаёшь venv + pip install.

### Сценарий D: «Stuck — пытался self update до bootstrap, получил hard-fail»

```bash
# Если ai-hats команда не находится:
curl -sSL https://github.com/muratovv/ai-hats/raw/main/scripts/install-launcher.sh | bash
ai-hats self update

# Если launcher есть, но что-то прерывалось посередине:
rm -rf .agent/ai-hats/.venv      # удаляет default venv (data в tracker/ + sessions/ сохраняется)
ai-hats self update              # launcher recreates clean
```

### Сценарий E: «Python upgrade на хосте сломал venv (proxmox case)»

```bash
ai-hats self update          # launcher detects broken symlinks → auto-recreate + pip install
```

Это работает только для default location (`<ai_hats_dir>/.venv/`). Override-venv → user-managed.

## Recovery checklist

| Симптом | Команда |
|---|---|
| `ai-hats: command not found` | install-launcher one-liner |
| `ai-hats: venv missing at <path>` | `ai-hats self update` |
| `ai-hats: venv exists but ai-hats binary is missing` | `ai-hats self update` |
| `ai-hats: override venv ... is missing or broken` | `python3 -m venv <path> && <path>/bin/pip install 'ai-hats @ ...'` |
| Что-то сломалось в недрах (import error) | `rm -rf .agent/ai-hats/.venv && ai-hats self update` |
| Хочу полный wipe (без data) | `rm -rf .agent/ai-hats/.venv && ai-hats self update` |
| Хочу полный wipe (С data) | `rm -rf .agent/ai-hats/ && ai-hats self update && ai-hats init -r <role> -p <provider>` |

## Что осталось от старого мира

- `pipx install ai-hats` — больше не рекомендуемый путь. Работает, но
  не использует launcher heal — manual update через `pipx upgrade
  ai-hats`. Рекомендуем мигрировать на launcher.
- `<project>/.venv/` — если у тебя проектный venv с ai-hats (HATS-pre-318
  bootstrap) — отдельная история. Launcher его не видит (default location
  — `<ai_hats_dir>/.venv/`, не `<project>/.venv/`). Либо мигрируй на
  default (delete `<project>/.venv/.../ai-hats`, run launcher self update),
  либо укажи `venv_path: .venv` в yaml — launcher тогда подхватит.

## Cross-references

- HATS-333 (epic — venv-first install)
- HATS-334 (venv_path config foundation)
- HATS-336 (bootstrap.sh refactor)
- HATS-337 (python cleanup — wrapper/HATS-318/HATS-330 removed)
- HATS-339 (bash launcher impl)
- HATS-318 — replaced (was: opt-in local venv)
- HATS-330 — removed (was: mixed-install gate, obsolete by construction)
- HATS-315 — venv research, replaced by HATS-333 launcher architecture
- `docs/migration-311.md` — HATS-316 layout migration (still relevant)
