"""`ai-hats self update` — self-maintenance of the tool."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import click

from ._helpers import _assembler, _project_dir, console, logger

# HATS-496: accept tag / branch / full-or-short SHA as a --revision argument.
# Bare SHA detection skips the ls-remote pre-flight (git ls-remote returns
# tags/branches keyed by refspec, not arbitrary commit ids — for raw SHA we
# defer to pip's own resolution downstream).
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


# HATS-337: AI_HATS_REPO_URL env overrides the default git URL, mirroring
# the bash launcher (HATS-339) so a single env var pins the install source
# end-to-end (CI, airgapped mirrors, custom forks).
def _git_install_url() -> str:
    return os.environ.get(
        "AI_HATS_REPO_URL", "git+ssh://git@github.com/muratovv/ai-hats.git"
    )


def _build_update_cmd(ref: str | None = None) -> list[str]:
    """Build the pip command for updating ai-hats from GitHub.

    NOTE: we intentionally do NOT pass --no-deps. Dropping it means new
    dependencies declared in pyproject.toml (e.g. ptyprocess added in
    HATS-207) get pulled in on update; otherwise users hit
    ModuleNotFoundError at runtime after an update.

    HATS-337/follow-up: PEP 508 `name @ url` requires a URL scheme. For
    local-path AI_HATS_REPO_URL (e.g. `--local /path` in bootstrap.sh) we
    pass the path directly — pip detects pyproject.toml and installs.

    HATS-496: when ``ref`` is set, append ``@<ref>`` so pip installs that
    tag / branch / commit instead of the implicit ``HEAD`` of master.
    Caller is expected to have validated ``ref`` upstream (``_resolve_ref``)
    and to have refused local-path URLs (which don't support refs).
    """
    url = _git_install_url()
    if ref and "://" in url:
        target = f"ai-hats @ {url}@{ref}"
    elif ref:
        # Local path with --revision is refused upstream; defensive fallback
        # appends @ref anyway so the failure shows up at pip rather than
        # silently installing HEAD.
        target = f"{url}@{ref}"
    else:
        target = f"ai-hats @ {url}" if "://" in url else url
    # HATS-563: dropped --no-cache-dir. --force-reinstall already re-installs
    # the named target unconditionally; transitive deps (click, pydantic,
    # rich, pyyaml, ...) are safe to serve from the local wheel cache. Keeps
    # `self update` fast even when the host has a warm pip cache. UX bonus
    # to all users; also unlocks ~100s on the e2e+smoke gate (HATS-550).
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--force-reinstall",
        target,
    ]


def _read_direct_url() -> dict | None:
    """Return the parsed ``direct_url.json`` (PEP 610) for the active install.

    Returns ``None`` when the metadata is missing, malformed, or the
    package isn't installed — callers treat that as "no install info
    available" rather than blocking. The PEP 610 dict contains:

    - ``url``           — install source (file://, https://, git+ssh://, …)
    - ``dir_info``      — ``{"editable": bool}`` for local-dir installs
    - ``vcs_info``      — ``{"vcs": "git", "commit_id": …,
      "requested_revision": …}`` for git installs

    HATS-497 reads ``vcs_info`` / ``dir_info`` for ``config status``
    install diagnostics. HATS-496 reads ``dir_info`` for the editable-
    target protection in ``self update --revision``.
    """
    try:
        dist = distribution("ai-hats")
        raw = dist.read_text("direct_url.json")
    except (PackageNotFoundError, FileNotFoundError):
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _is_editable_install() -> tuple[bool, str | None]:
    """Detect whether the active ai-hats install is editable.

    Returns ``(is_editable, source_url)``. Backward-compatible wrapper
    over :func:`_read_direct_url`. Falls back to ``(False, None)`` when
    direct_url.json is missing — treat as "installable / non-editable"
    so a fresh venv with no ai-hats yet doesn't trigger the protection
    path.
    """
    data = _read_direct_url()
    if data is None:
        return (False, None)
    dir_info = data.get("dir_info") or {}
    return (bool(dir_info.get("editable")), data.get("url"))


def _resolve_ref(repo_url: str, ref: str) -> str | None:
    """Validate ``ref`` against ``repo_url`` via ``git ls-remote``.

    Returns the resolved SHA on success, ``None`` if the ref isn't found
    on the remote or the probe fails (timeout, missing git). A bare SHA
    (``[0-9a-f]{7,40}``) short-circuits and returns itself — ``git
    ls-remote`` keys by refspec, not arbitrary commit ids, so we defer
    invalid-SHA detection to pip downstream.

    The ``git+`` PEP 508 prefix is stripped before invoking git, which
    only speaks the bare URL schemes.
    """
    if _SHA_RE.fullmatch(ref):
        return ref

    ls_url = repo_url.removeprefix("git+")
    try:
        result = subprocess.run(
            ["git", "ls-remote", ls_url, ref],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if not out:
        return None
    # First column of first matching line is the SHA.
    return out.splitlines()[0].split()[0]


# ---------- HATS-647: managed blue-green versioned install ----------


def _active_venv_root() -> Path:
    """Root of the venv the running interpreter belongs to.

    Uses ``sys.prefix`` — for a venv interpreter that IS the venv root.
    ``Path(sys.executable)`` would be wrong: ``<venv>/bin/python`` is
    typically a symlink to the base interpreter, so ``.resolve()`` escapes
    the venv (this is the bug that left versioning silently disabled).
    """
    return Path(sys.prefix)


def _is_managed_install(project_dir: Path) -> bool:
    """True when the active install is the ai-hats-managed default venv and is
    therefore eligible for blue-green versioning (HATS-647).

    Managed ⇔ NOT editable AND the active venv is either the legacy default
    ``<ai_hats_dir>/.venv`` or a ``<ai_hats_dir>/versions/<sha>/`` dir. An
    explicit user override (``AI_HATS_VENV`` / yaml ``venv_path`` pointing
    elsewhere) or an editable dev checkout bypasses versioning — we never
    manage a user-owned venv (HATS-339) nor rewrite a source checkout.
    """
    is_editable, _ = _is_editable_install()
    if is_editable:
        return False
    from ..paths import ai_hats_dir, versions_root

    try:
        venv_root = _active_venv_root().resolve()
        default_venv = (ai_hats_dir(project_dir) / ".venv").resolve()
        vroot = versions_root(project_dir).resolve()
    except OSError:
        return False
    return venv_root == default_venv or venv_root.parent == vroot


def _build_install_cmd(python_exe: str, url: str, ref: str) -> list[str]:
    """pip-install ai-hats into the venv owning ``python_exe``.

    URL source (``git+ssh://…``) → PEP 508 ``ai-hats @ url@ref`` so the exact
    sha is installed. Local-path source (e.g. ``--local`` bootstrap, e2e
    harness) → bare path: pip builds the working tree and does NOT support an
    ``@ref`` suffix on local paths (the same reason ``--revision`` refuses
    them). The version dir is named by the sha resolved separately, not by the
    install spec.
    """
    target = f"ai-hats @ {url}@{ref}" if "://" in url else url
    return [python_exe, "-m", "pip", "install", "--force-reinstall", target]


def _flip_current(project_dir: Path, sha: str) -> None:
    """Atomically point ``versions/current`` at ``sha`` (tmp-write + replace).

    The pointer is the single source of truth the launcher reads; the rename
    is atomic on the same filesystem so a crash can never leave a torn
    pointer. Flipped only after a fully-successful install (HATS-647) —
    crash-during-install leaves ``current`` untouched (still the old sha), so
    the tool never bricks.
    """
    from ..paths import current_pointer, versions_root

    versions_root(project_dir).mkdir(parents=True, exist_ok=True)
    ptr = current_pointer(project_dir)
    tmp = ptr.with_name(f".current.{sha}.tmp")
    tmp.write_text(f"{sha}\n", encoding="utf-8")
    os.replace(tmp, ptr)


def _version_string(python_exe: str) -> str:
    """Read ``ai_hats.__version__`` from an arbitrary venv's interpreter."""
    result = subprocess.run(
        [python_exe, "-c", "from ai_hats import __version__; print(__version__)"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _run_managed_versioned_update(
    project_dir: Path,
    *,
    url: str,
    target_sha: str | None,
    old_version: str,
    active_role: str | None,
    config_unreadable: bool,
    migrate_force: bool,
    check_branches: bool,
) -> None:
    """Blue-green ``self update`` for the managed default venv (HATS-647).

    Installs the new version into ``versions/<sha>/`` — never the live venv —
    then atomically flips ``versions/current``. A concurrently-live run pinned
    (via inherited ``AI_HATS_VENV``) to the old sha keeps its frozen env; the
    next invocation resolves the new sha. The library/composition diff is
    produced by the fresh-interpreter bump in the new venv; the rich
    cross-venv diff is deferred (informational).

    Crash-safety (R0): ``current`` flips only after a fully-successful
    install+verify, so an interrupted update never bricks the tool — at worst
    it leaves an unreferenced half-written ``versions/<sha>/`` dir. The proper
    ``.tmp-<sha>`` staging + ``.complete`` sentinel + ``.tmp-*`` recovery sweep
    is R1 (HATS-648); here we simply never trust a pre-existing dir.
    """
    import shutil

    from ..paths import (
        complete_sentinel,
        is_complete,
        read_current_sha,
        version_dir,
    )

    if "://" not in url:
        # Local-path source: pip installs the working tree, so identify the
        # version by the local repo's HEAD — NOT the remote-master probe,
        # which can point at a different branch (e.g. a worktree checked out
        # off master). URL sources keep the probe / revision sha because pip
        # installs that exact ref.
        target_sha = _resolve_ref(url, "HEAD") or target_sha
    if not target_sha:
        target_sha = _resolve_ref(url, "HEAD")
    if not target_sha:
        console.print(
            "[red]Update failed[/]: could not resolve a target revision to "
            "install (offline, or the remote/source is unreachable). A "
            "versioned install needs a resolvable sha to name versions/<sha>/."
        )
        sys.exit(2)

    # HATS-648 (R1): clean incomplete residue from prior crashed updates before
    # staging the new build. Same idempotent, TTL-guarded sweep as the
    # create_session chokepoint (a *recent* incomplete dir may be a concurrent
    # update in flight, so it is kept). No-silent-caps.
    from ..version_recovery import sweep_incomplete_versions

    for _residue in sweep_incomplete_versions(project_dir):
        console.print(
            f"[dim]Reclaimed incomplete residue: versions/{_residue.name}[/]"
        )

    vdir = version_dir(project_dir, target_sha)
    already_current = read_current_sha(project_dir) == target_sha

    if already_current:
        console.print(
            f"[green]Already up to date[/] ({old_version}) "
            f"[dim]— current → {target_sha[:12]}[/]"
        )
        new_python = sys.executable
    elif vdir.exists() and is_complete(project_dir, target_sha):
        # A prior successful install of this exact sha (sentinel present):
        # trust it and reuse — just (re)flip current below. A blind reinstall
        # would rmtree a dir that may be a LIVE pinned run's frozen env
        # (e.g. run still on sha A while current already moved to B, then a
        # `self update --revision A`), so keying reuse on the sentinel is a
        # safety guard, not only an optimisation (HATS-648).
        new_python = str(vdir / "bin" / "python")
        console.print(f"[cyan]Reusing complete[/] versions/{target_sha[:12]} …")
        _flip_current(project_dir, target_sha)
        new_version = _version_string(new_python)
        console.print(
            f"[green]Updated[/]: {old_version} → [bold]{new_version}[/] "
            f"[dim](current → {target_sha[:12]})[/]"
        )
    else:
        # No dir, or incomplete crash residue (no .complete sentinel) — never
        # trust it; rebuild fresh (HATS-648 build-in-place + sentinel).
        if vdir.exists():
            shutil.rmtree(vdir, ignore_errors=True)
        vdir.parent.mkdir(parents=True, exist_ok=True)
        with console.status(
            f"[cyan]Creating versioned venv[/] versions/{target_sha[:12]} …",
            spinner="dots",
        ):
            venv_proc = subprocess.run(
                [sys.executable, "-m", "venv", str(vdir)],
                capture_output=True,
                text=True,
            )
        if venv_proc.returncode != 0:
            console.print(f"[red]Update failed[/] (venv create): {venv_proc.stderr}")
            return
        new_python = str(vdir / "bin" / "python")
        with console.status(
            "[cyan]Downloading ai-hats from GitHub …[/] "
            "[dim](pip install — may take a minute)[/]",
            spinner="dots",
        ):
            install = subprocess.run(
                _build_install_cmd(new_python, url, target_sha),
                capture_output=True,
                text=True,
            )
        if install.returncode != 0:
            # current is untouched → the tool still runs on the old sha. The
            # incomplete dir (no sentinel) is swept by version_recovery.
            console.print(f"[red]Update failed[/]: {install.stderr}")
            return
        with console.status("[cyan]Verifying install …[/]", spinner="dots"):
            verify = subprocess.run(
                [new_python, "-m", "ai_hats._bootstrap", "verify"],
                capture_output=True,
                text=True,
            )
        if verify.returncode != 0:
            warning = (verify.stderr or verify.stdout or "").strip() or "see logs"
            console.print(f"[red]Update failed[/] (verify): {warning}")
            return
        # Sentinel written LAST, only after a fully-successful install+verify —
        # the authoritative completeness marker (HATS-648). Only then is the
        # atomic flip allowed; current never points at a dir lacking .complete.
        complete_sentinel(project_dir, target_sha).write_text("", encoding="utf-8")
        _flip_current(project_dir, target_sha)
        new_version = _version_string(new_python)
        console.print(
            f"[green]Updated[/]: {old_version} → [bold]{new_version}[/] "
            f"[dim](current → {target_sha[:12]})[/]"
        )
        changelog = _get_changelog()
        if changelog:
            console.print("\n[bold]Recent changes:[/]")
            for line in changelog.splitlines()[:7]:
                console.print(f"  {line}")
        # No-silent-caps: prior version dirs are retained until R2 GC.
        console.print(
            "[dim]Note: previous version(s) kept under versions/; "
            "reclaimed automatically by GC (R2 / HATS-649).[/]"
        )

    # Bump / re-assemble with the NEW code (fresh interpreter in the target
    # venv) — mirrors the legacy HATS-400 fresh-interpreter bump.
    if active_role or config_unreadable:
        role_label = active_role or "(config unreadable — healing)"
        console.print(f"\n[bold]Re-assembling:[/] {role_label}")
        bump_cmd = [new_python, "-m", "ai_hats._bump_internal"]
        if migrate_force:
            bump_cmd.append("--migrate-force")
        if check_branches:
            bump_cmd.append("--check-branches")
        # Pin the bump child to the venv it actually runs in (the new sha),
        # not the old AI_HATS_VENV inherited from the launcher — so any
        # venv_path() resolution inside the bump agrees with sys.prefix.
        bump_env = {**os.environ, "AI_HATS_VENV": str(vdir)}
        proc = subprocess.run(
            bump_cmd, cwd=str(project_dir), env=bump_env, check=False,
        )
        if proc.returncode != 0:
            console.print(
                f"  [yellow]Bump (fresh interpreter) exited {proc.returncode} "
                f"— review output above[/]"
            )


# HATS-497: Install diagnostics for ``ai-hats config status`` Health section.
# Helpers below produce a flat dict of display-key → display-value. Layer
# boundary: install-level (interpreter, venv, source); does NOT touch the
# Assembler (which is project-level).


def _format_install_source() -> str:
    """Format the ``Source:`` line for ``ai-hats config status``.

    Reads PEP 610 ``direct_url.json`` via :func:`_read_direct_url`. Three
    branches map to user-visible labels:

    - ``editable @ <url>``                            — local dev install
    - ``pinned @ <ref> → <sha>``                      — ``vcs_info`` has
      ``requested_revision`` AND it's not "HEAD" / branch-tip
    - ``git @ <ref-or-HEAD> → <sha>``                 — plain git install

    Falls back to ``"(unknown — direct_url.json missing)"`` when the
    metadata isn't available. Truncates SHA to 7 chars for display;
    full SHA stays in direct_url.json for tooling.
    """
    data = _read_direct_url()
    if data is None:
        return "(unknown — direct_url.json missing)"
    url = data.get("url") or "?"
    dir_info = data.get("dir_info") or {}
    if dir_info.get("editable"):
        return f"editable @ {url}"
    vcs_info = data.get("vcs_info") or {}
    commit = vcs_info.get("commit_id") or ""
    short = commit[:7] if commit else "?"
    requested = vcs_info.get("requested_revision")
    if requested and requested not in ("HEAD",):
        return f"pinned @ {requested} → {short}"
    if vcs_info:
        return f"git @ {requested or 'HEAD'} → {short}"
    # Non-editable, non-vcs (e.g. local-path / wheel install).
    return f"installed @ {url}"


def _library_path() -> str:
    """Resolve the built-in library root via ``importlib.resources``."""
    try:
        from importlib.resources import files

        return str(files("ai_hats.library"))
    except (ModuleNotFoundError, FileNotFoundError, OSError) as exc:
        logger.debug("library path lookup failed", exc_info=exc)
        return "(unknown)"


def _resolved_via_heuristic(venv: Path) -> str:
    """Best-effort guess at which knob routed the launcher to this venv.

    Heuristic — see HATS-497 plan D4. Python sees only ``sys.executable``;
    the launcher's bash-side precedence (``AI_HATS_VENV`` env >
    ``ai-hats.yaml`` ``venv_path`` > default ``<PWD>/.agent/ai-hats/.venv``)
    isn't directly observable. We compare canonical paths and report the
    first knob that matches; document as best-effort, do NOT promise
    source-of-truth semantics.
    """
    try:
        venv_real = venv.resolve()
    except OSError:
        venv_real = venv

    env_val = os.environ.get("AI_HATS_VENV")
    if env_val:
        env_path = Path(env_val.replace("~", str(Path.home()))).resolve()
        if env_path == venv_real:
            return "AI_HATS_VENV env"

    # ai-hats.yaml venv_path (relative to project_dir, expanded by paths.py).
    try:
        project_dir = _project_dir()
        yaml_path = project_dir / "ai-hats.yaml"
        if yaml_path.is_file():
            # Lightweight grep — matches the launcher's bash-side scan
            # (scripts/ai-hats-launcher) rather than loading the full
            # ProjectConfig (heavier import, fires yaml-load WARNs).
            for line in yaml_path.read_text().splitlines():
                if line.startswith("venv_path:"):
                    candidate = line.split(":", 1)[1].strip().strip("'\"")
                    if candidate:
                        candidate_path = Path(
                            candidate.replace("~", str(Path.home()))
                        )
                        if not candidate_path.is_absolute():
                            candidate_path = project_dir / candidate_path
                        if candidate_path.resolve() == venv_real:
                            return "ai-hats.yaml venv_path"
                    break
    except OSError as exc:
        logger.debug("venv_path heuristic probe failed", exc_info=exc)

    return "default <PWD>/.agent/ai-hats/.venv"


def _repo_head_for_editable() -> str | None:
    """Return ``<short-sha> (<branch>, clean|dirty)`` if active install is editable.

    Returns ``None`` for non-editable installs (in those cases the SHA is
    already in ``direct_url.json.vcs_info.commit_id`` and shown under
    "Source"), or when ``git`` isn't available / the path isn't a repo.
    """
    is_editable, source_url = _is_editable_install()
    if not is_editable or not source_url:
        return None
    # source_url is typically ``file:///abs/path``. Strip the scheme.
    repo_path = source_url.removeprefix("file://")
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        return None
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if sha.returncode != 0:
            return None
        branch = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        porcelain = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    branch_name = (branch.stdout.strip() if branch.returncode == 0 else "?") or "?"
    clean_flag = "clean" if not porcelain.stdout.strip() else "dirty"
    return f"{sha.stdout.strip()} ({branch_name}, {clean_flag})"


def _gather_install_info() -> dict[str, str]:
    """Gather install-level diagnostics for ``ai-hats config status``.

    Returns an ordered ``dict`` of display-key → display-value. Order
    matches the plan (D2): install identity → source → resolution →
    optional editable repo state. Keys are OMITTED (not in dict) when
    not applicable — caller iterates dict, so omission = no print.

    Output is human-readable only (D3); add ``--json`` in a follow-up
    if a programmatic consumer appears.
    """
    from .. import __version__

    info: dict[str, str] = {}
    info["Version"] = __version__
    info["Interpreter"] = (
        f"{sys.executable} "
        f"({sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro})"
    )
    venv = Path(sys.executable).parent.parent
    info["Venv"] = str(venv)
    info["Source"] = _format_install_source()
    info["Library"] = _library_path()
    info["Resolved via"] = _resolved_via_heuristic(venv)
    head = _repo_head_for_editable()
    if head is not None:
        info["Repo HEAD"] = head
    return info


def _get_installed_version() -> str:
    """Get the currently installed ai-hats version via subprocess.

    Uses a fresh Python process to avoid import caching.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "from ai_hats import __version__; print(__version__)"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _get_changelog() -> str:
    """Get recent commits from GitHub via shallow clone."""
    import subprocess
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ai-hats-changelog-")
    try:
        result = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "10",
                "--filter=blob:none",
                "--quiet",
                "ssh://git@github.com/muratovv/ai-hats.git",
                tmp,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return ""
        log = subprocess.run(
            # `--no-merges`: hide `Merge branch 'task/hats-NNN'` titles —
            # conventional-commit titles from the actual work are more useful
            # than the wrapping merge commits under a no-ff merge convention.
            ["git", "-C", tmp, "log", "--oneline", "--no-merges", "-7"],
            capture_output=True,
            text=True,
        )
        return log.stdout.strip() if log.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        logger.debug("changelog fetch failed", exc_info=True)
        return ""
    finally:
        import shutil

        # Local tempfile.mkdtemp() — own temp dir, no user data.
        shutil.rmtree(tmp, ignore_errors=True)  # safe-delete: ok own-tmpdir


def _snapshot_dep_versions() -> dict[str, str]:
    """Snapshot ``{distribution_name: version}`` via a fresh ``pip list`` subprocess.

    Fresh subprocess avoids importlib cache divergence between pre- and
    post-update — important for HATS-213 activation banner.
    """
    import json
    import subprocess

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        logger.debug("pip list snapshot failed", exc_info=True)
        return {}
    if result.returncode != 0:
        return {}
    try:
        items = json.loads(result.stdout or "[]")
    except (ValueError, TypeError):
        return {}
    return {item["name"].lower(): item.get("version", "") for item in items if "name" in item}


def _snapshot_library() -> dict[str, set[str]]:
    """Snapshot available component names from built-in + global library paths."""
    from ..assembler import _builtin_library_layers
    from ..paths import user_home
    from ..resolver import LibraryResolver
    from ..models import ComponentType

    paths = list(_builtin_library_layers())
    # HATS-532: ``user_home()`` honours ``AI_HATS_USER_HOME`` so the
    # snapshot reflects the same global slice the assembler resolved.
    global_lib = user_home() / ".ai-hats"
    if global_lib.is_dir():
        paths.append(global_lib)
    resolver = LibraryResolver(paths)
    return {ct.value: set(resolver.list_components(ct)) for ct in ComponentType}


def _format_component_diff(
    before: dict[str, set[str]],
    after: dict[str, set[str]],
) -> bool:
    """Print added/removed components. Returns True if any changes found."""
    any_changes = False
    for component_type in ("role", "trait", "rule", "skill"):
        old = before.get(component_type, set())
        new = after.get(component_type, set())
        added = sorted(new - old)
        removed = sorted(old - new)
        if added or removed:
            any_changes = True
            for name in added:
                console.print(f"  [green]+[/] {component_type}: {name}", highlight=False)
            for name in removed:
                console.print(f"  [red]-[/] {component_type}: {name}", highlight=False)
    return any_changes


def _snapshot_composition(asm) -> tuple[set[str], set[str]]:
    """Snapshot current role's rules and skills via composition.

    HATS-407: falls back to default_role when active_role is empty —
    fresh projects (post-init, pre-first-session) carry intent in
    default_role only.
    """
    from ..assembler import AssemblyError

    cfg = asm.project_config
    role = cfg.active_role or cfg.default_role
    if not role:
        return set(), set()
    try:
        from ..materialize import compose_for_role

        result = compose_for_role(asm, role)
        return {r.name for r in result.rules}, {s.name for s in result.skills}
    except (AssemblyError, ValueError, OSError, KeyError, AttributeError):
        logger.debug("composition snapshot failed", exc_info=True)
        return set(), set()


# HATS-441: refusal exit code for state-guard failures (installed ahead of
# remote master, or diverged history). Distinct from click's 0 (success),
# 1 (UsageError), 2 (BadParameter) so scripts can disambiguate.
DOWNGRADE_REFUSAL_EXIT_CODE = 3


def _probe_remote_state(project_dir: Path):
    """Run the ahead/behind probe. Returns the cache entry or ``None``.

    Wrapper around :func:`update_check.checker.run_check` that swallows
    transport errors — a network blip MUST NOT block an explicit
    ``self update`` invocation. Returns ``None`` only when the probe could
    not resolve SHAs (no network, non-git install, malformed remote).
    """
    from ..update_check.checker import run_check

    try:
        return run_check(project_dir)
    except (OSError, ValueError):
        logger.debug("update-check probe failed", exc_info=True)
        return None


def _classify_downgrade(entry) -> str | None:
    """Classify a probe entry vs the downgrade gate. Returns reason or None.

    HATS-441: reuses the HATS-432 probe infrastructure to determine whether
    ``ai-hats self update`` would silently regress the local install. Two
    refusal classes:

    - ``"ahead"`` — installed strictly ahead of remote master
      (``ahead > 0 and behind == 0``); ``pip install -U git+…`` would
      overwrite local commits.
    - ``"diverged"`` — both sides have commits the other lacks
      (``ahead > 0 and behind > 0``); same overwrite risk.

    Returns ``None`` (gate inactive — proceed) when:

    - entry is ``None`` (probe failed),
    - ahead/behind couldn't be resolved (``None`` axes),
    - installed is behind or in sync (normal update / no-op).
    """
    if entry is None or entry.ahead is None or entry.behind is None:
        return None
    if entry.ahead > 0 and entry.behind == 0:
        return "ahead"
    if entry.ahead > 0 and entry.behind > 0:
        return "diverged"
    return None


def _render_downgrade_refusal(reason: str, entry) -> None:
    """Print a coloured refusal message naming installed/remote + override hint."""
    installed = entry.installed_label or entry.installed_sha[:9]
    latest = entry.latest_label or entry.latest_sha[:9]
    if reason == "ahead":
        console.print(
            f"[red]Installed version[/] [bold]{installed}[/] is ahead of "
            f"remote master [bold]{latest}[/] by {entry.ahead} commits. "
            f"[red]Refusing to downgrade.[/]\n"
            f"Use [bold]--force-downgrade[/] to override "
            f"(will replace your local install).",
            highlight=False,
        )
    else:  # diverged
        console.print(
            f"[red]Installed version[/] [bold]{installed}[/] has diverged "
            f"from remote master [bold]{latest}[/] "
            f"(local ahead: {entry.ahead}, remote ahead: {entry.behind}). "
            f"[red]Refusing to downgrade.[/]\n"
            f"Use [bold]--force-downgrade[/] to override "
            f"(will replace your local install).",
            highlight=False,
        )


@click.command()
@click.option(
    "--migrate-force",
    is_flag=True,
    help="Bypass v0.6 → v0.7 user-edit refusal during auto-bump "
    "(logs WARN per overwritten file).",
)
@click.option(
    "--check-branches",
    is_flag=True,
    help="Warn if local branches modify any v0.7-migration path slated for deletion.",
)
@click.option(
    "--force-downgrade",
    is_flag=True,
    help="Bypass the ahead/diverged guard (HATS-441). Replaces the local "
    "install with the remote master state — destroys unpushed work in "
    "editable installs.",
)
@click.option(
    "--revision",
    "revision",
    default=None,
    metavar="REF",
    help="Install ai-hats at an explicit tag, branch, or commit SHA "
    "instead of remote master (HATS-496). Bypasses the ahead/diverged "
    "guard; pre-flight 'git ls-remote' validates the ref before any pip "
    "call. Editable target venv requires --force.",
)
@click.option(
    "--force",
    "force",
    is_flag=True,
    help="With --revision: overwrite the editable install in the target "
    "venv (HATS-496 D2). No effect without --revision. Distinct from "
    "--force-downgrade, which only applies to plain master-targeted "
    "updates.",
)
def update(
    migrate_force: bool,
    check_branches: bool,
    force_downgrade: bool,
    revision: str | None,
    force: bool,
):
    """Update ai-hats from GitHub.

    Auto-bumps after install. HATS-415: ``bump`` now self-heals v0.6 →
    v0.7 layouts transparently for the common case (no user edits). If
    user edits are detected on the v0.6 canonical files, the bump
    refuses with per-file guidance — re-run with ``--migrate-force``
    after relocating the content (or to overwrite). ``--check-branches``
    surfaces a warning when local branches modify the paths slated for
    deletion.

    HATS-496: ``--revision <REF>`` pins the install to an explicit tag,
    branch, or commit SHA. Skips the downgrade probe / guard (D1). On an
    editable target venv, refuses unless ``--force`` is passed (D2). A
    pre-flight ``git ls-remote`` validates the ref before any pip call.
    """
    from .. import __version__ as old_version
    from ..assembler import AssemblyError
    from ..models import ProjectConfigError

    console.print(f"Current version: [bold]{old_version}[/]")
    # HATS-318: surface which interpreter we're updating. When the wrapper has
    # already re-exec'd into <ai_hats_dir>/.venv, the install goes to that env
    # by virtue of sys.executable; this banner makes the target unambiguous.
    if "/.venv/bin/python" in sys.executable or "/versions/" in sys.executable:
        console.print(f"[dim]Target venv:[/] {sys.executable}")

    project_dir = _project_dir()

    # HATS-496: --revision short-circuits the guard machinery — the user
    # is explicit about the target ref, so probing master and comparing
    # ahead/behind axes would only obstruct. Editable check (D2) and ref
    # pre-flight (steps 2-3 of plan) happen here, BEFORE the snapshot /
    # install path runs.
    probe = None
    managed_target_sha: str | None = None  # HATS-647: resolved sha for versioned install
    if revision:
        url = _git_install_url()
        if "://" not in url:
            console.print(
                f"[red]--revision requires a git URL[/] "
                f"(AI_HATS_REPO_URL={url!r} looks like a local path; "
                "local-path installs do not support refs)."
            )
            sys.exit(2)

        is_editable, source_url = _is_editable_install()
        if is_editable and not force:
            console.print(
                f"[red]Target venv has editable install[/] at "
                f"[bold]{source_url}[/]. Set "
                f"[bold]AI_HATS_VENV=<other-path>[/] to install into a "
                f"different venv, or pass [bold]--force[/] to overwrite "
                f"the editable install."
            )
            sys.exit(2)

        with console.status(
            f"[cyan]Resolving ref[/] {revision} on {url} …",
            spinner="dots",
        ):
            resolved = _resolve_ref(url, revision)
        if resolved is None:
            console.print(
                f"[red]error:[/] ref '{revision}' not found on remote {url}"
            )
            sys.exit(2)

        console.print(
            "[yellow]Warning:[/] --revision bypasses the ahead/diverged "
            "guard. Installing arbitrary ref may downgrade your install."
        )
        console.print(f"  [dim]Resolved {revision} → {resolved}[/]")
        managed_target_sha = resolved
    else:
        # HATS-441: refuse silent downgrade when installed HEAD is ahead of
        # remote master. ``--force-downgrade`` opts back into the destructive
        # ``pip install --force-reinstall git+…`` behaviour for callers who
        # know what they're doing (e.g. discarding a stale dev branch).
        # Single probe — feeds both the downgrade gate and the no-op
        # short-circuit below. Avoids running run_check twice per invocation.
        probe = None if force_downgrade else _probe_remote_state(project_dir)
        managed_target_sha = probe.latest_sha if probe is not None else None

        if force_downgrade:
            console.print(
                "[yellow]Warning:[/] --force-downgrade bypasses the "
                "ahead/diverged guard. Your local install (including "
                "editable / unpushed commits) will be replaced by remote "
                "master."
            )
        else:
            reason = _classify_downgrade(probe)
            if reason is not None:
                _render_downgrade_refusal(reason, probe)
                sys.exit(DOWNGRADE_REFUSAL_EXIT_CODE)

    # 1. Snapshot before update
    before_lib = _snapshot_library()
    before_deps = _snapshot_dep_versions()
    config_path = project_dir / "ai-hats.yaml"
    active_role = None
    before_rules: set[str] = set()
    before_skills: set[str] = set()
    # HATS-549 Phase 3: flag for in-process bump failure (smoke-assert
    # raise or any AssemblyError/OSError from the bump pipeline).
    # Used at the bottom of the function to surface a non-zero exit.
    bump_in_process_failed = False
    # HATS-581: set when the INSTALLED code can't parse ai-hats.yaml. The
    # recovery command (``self update``) must not be blocked by a config the
    # current code rejects — degrade and let the fresh-interpreter bump (new
    # code) heal it.
    config_unreadable = False

    if config_path.exists():
        # HATS-408 review (R1): we used to call ``ProjectConfig.from_yaml``
        # AND ``_assembler`` (which itself calls ``from_yaml``), firing the
        # yaml-load WARNs (deprecated-field strip, default_role heal) twice
        # per ``self update``. Build the Assembler once and read its config.
        try:
            asm = _assembler(project_dir)
        except ProjectConfigError as e:
            # HATS-581: degrade instead of crashing. Install the new package,
            # then force the fresh-interpreter bump below — new code may heal
            # the config (forward-compat strip) or report it cleanly.
            console.print(
                "  [yellow]ai-hats.yaml not parseable by the installed "
                f"version[/]:\n  [dim]{e}[/]\n"
                "  [dim]Proceeding with update; the new version will attempt "
                "to heal the config during bump.[/]"
            )
            config_unreadable = True
        else:
            cfg = asm.project_config
            # HATS-407: active_role is the runtime cache (empty until first
            # session). For a freshly-installed project where only default_role
            # is set, we still want auto-bump to run so migrations and the
            # canonical aggregator refresh. Fall back to default_role for the
            # bump-trigger decision.
            active_role = cfg.active_role or cfg.default_role or None
            if active_role:
                before_rules, before_skills = _snapshot_composition(asm)

    # HATS-647: managed default venv → blue-green versioned install. Install
    # into versions/<sha>/ (never the live venv) + atomic current flip, so a
    # concurrently-live run survives. Editable / override venvs fall through
    # to the legacy in-place install path below.
    if _is_managed_install(project_dir):
        _run_managed_versioned_update(
            project_dir,
            url=_git_install_url(),
            target_sha=managed_target_sha,
            old_version=old_version,
            active_role=active_role,
            config_unreadable=config_unreadable,
            migrate_force=migrate_force,
            check_branches=check_branches,
        )
        return

    # 2. Install — short-circuited when the probe confirms installed SHA
    # already matches remote master AND the ahead/behind axes resolved to
    # exactly (0, 0). The double check guards against environments where
    # SHA detection returns identical garbage on both sides (e.g.,
    # subprocess.run mocks that yield ``stdout=""`` for every git call);
    # ahead/behind only resolve to (0, 0) when ``git rev-list`` actually
    # walked real commits. No point paying ``pip install --force-reinstall``'s
    # 10-15s re-download for a no-op; bump() below still runs to apply
    # any pending migrations.
    # HATS-496: --revision always re-installs. The user asked for a specific
    # ref; even if the resolved SHA happens to equal the installed SHA, force
    # the pip call so direct_url.json.vcs_info.requested_revision is rewritten
    # to the literal ref the user typed (HATS-497 reads this).
    skip_install = (
        not force_downgrade
        and not revision
        and probe is not None
        and probe.installed_sha == probe.latest_sha
        and probe.ahead == 0
        and probe.behind == 0
    )
    if skip_install:
        console.print(
            f"[green]Already up to date[/] ({old_version}) "
            "[dim]— skipping pip install[/]"
        )
        new_version = old_version
    else:
        cmd = _build_update_cmd(ref=revision)
        # Wrapped in a Rich spinner so the terminal isn't silent while pip
        # downloads (can take 30s+ on slow links).
        with console.status(
            "[cyan]Downloading ai-hats from GitHub …[/] "
            "[dim](pip install — may take a minute)[/]",
            spinner="dots",
        ):
            result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            console.print(f"[red]Update failed[/]: {result.stderr}")
            return

        # 2b. HATS-213 stage-2 verify: run a fresh interpreter against the
        # just-installed on-disk code, so any new declared runtime dep that
        # somehow didn't land gets healed before the user's next invocation.
        # Failures are non-fatal — layer A in cli.main() catches the rest.
        with console.status("[cyan]Verifying install …[/]", spinner="dots"):
            verify = subprocess.run(
                [sys.executable, "-m", "ai_hats._bootstrap", "verify"],
                capture_output=True,
                text=True,
            )
        if verify.returncode != 0:
            warning = (verify.stderr or verify.stdout or "").strip() or "see logs"
            console.print(f"[yellow]Post-install verify warned[/]: {warning}")

        # 3. Version diff
        new_version = _get_installed_version()
        if new_version == old_version:
            console.print(f"[green]Already up to date[/] ({old_version})")
    if new_version != old_version:
        console.print(f"[green]Updated[/]: {old_version} → [bold]{new_version}[/]")

        changelog = _get_changelog()
        if changelog:
            console.print("\n[bold]Recent changes:[/]")
            for line in changelog.splitlines()[:7]:
                console.print(f"  {line}")

    # 3b. Dep activation banner — flag the chicken-and-egg cycle: new in-
    # memory code is still the OLD one, so any changed dep won't be wired
    # until the next ai-hats invocation. (HATS-213)
    after_deps = _snapshot_dep_versions()
    dep_changes: list[str] = []
    for name, ver in after_deps.items():
        old = before_deps.get(name)
        if old is None:
            dep_changes.append(f"  [green]+[/] {name} {ver}")
        elif old != ver:
            dep_changes.append(f"  [cyan]~[/] {name} {old} → {ver}")
    for name in before_deps.keys() - after_deps.keys():
        dep_changes.append(f"  [red]-[/] {name}")
    if dep_changes:
        console.print("\n[bold]Dependency activation:[/]")
        for line in dep_changes:
            console.print(line, highlight=False)
        console.print("  Restart your shell or run any 'ai-hats' command to activate new deps.")
        console.print("  If anything misbehaves, run: ai-hats   (it will self-heal)")

    # 4. Library diff
    after_lib = _snapshot_library()
    console.print("\n[bold]Library:[/]")
    if not _format_component_diff(before_lib, after_lib):
        console.print("  [dim]No changes[/]")

    # 5. Auto-bump if role active (HATS-285: migration runs inside bump now;
    # standalone `ai-hats self migrate` was removed). HATS-400: when the
    # update actually changed the version on disk, run bump in a *fresh*
    # subprocess so the new code (migrations, healer, etc.) is loaded —
    # in-process pipeline (was `asm.bump()`, HATS-469 now `_refresh`+helpers)
    # would silently keep using the OLD code from
    # this update's interpreter, leaving the project half-fixed until the
    # user manually re-runs the bump-internal entry. HATS-470: the
    # subprocess entry-point moved from `ai-hats self bump` (CLI command
    # removed) to `python -m ai_hats._bump_internal` (hidden module).
    # HATS-581: also run the bump when the pre-install config read failed —
    # the fresh-interpreter bump (new code) is exactly what heals the config.
    if active_role or config_unreadable:
        role_label = active_role or "(config unreadable — healing)"
        console.print(f"\n[bold]Re-assembling:[/] {role_label}")
        version_changed = new_version != old_version
        # HATS-581: force the fresh-interpreter subprocess whenever the config
        # was unreadable in-process — the in-process branch would re-run
        # ``_assembler`` with the same un-parsing code and re-crash.
        if version_changed or config_unreadable:
            # Fresh interpreter → new code (healer, migrations, etc.).
            # Stdout/stderr passthrough so [heal] lines / spinners stream live.
            bump_cmd = [sys.executable, "-m", "ai_hats._bump_internal"]
            if migrate_force:
                bump_cmd.append("--migrate-force")
            if check_branches:
                bump_cmd.append("--check-branches")
            proc = subprocess.run(
                bump_cmd,
                cwd=str(project_dir),
                check=False,
            )
            if proc.returncode != 0:
                console.print(
                    f"  [yellow]Bump (fresh interpreter) exited "
                    f"{proc.returncode} — review output above[/]"
                )
                if revision:
                    # HATS-496 D3: pinning to an older ref often means the
                    # on-disk ai-hats.yaml schema is ahead of installed code.
                    # Surface the explicit recovery path so the user isn't
                    # left guessing at the traceback.
                    console.print(
                        "  [dim]Hint: yaml schema may be ahead of the "
                        "pinned version; run [bold]ai-hats self init "
                        "--migrate-force[/bold] manually if migrations "
                        "stalled.[/]"
                    )
            # Snapshot composition AFTER bump to compute rule/skill diff.
            # HATS-581: the bump may not have healed a non-strippable error
            # (e.g. a wrong-type value, not an unknown key). The package is
            # already installed; tolerate the re-read failure and report no
            # diff rather than crashing the recovery command.
            try:
                asm = _assembler(project_dir)
                after_rules, after_skills = _snapshot_composition(asm)
            except ProjectConfigError:
                after_rules, after_skills = before_rules, before_skills
        else:
            # No version change → no chicken-and-egg risk; in-process is fine
            # and avoids ~150ms subprocess overhead. Wrapped in a spinner so
            # the terminal isn't silent while migrations / healers run — on a
            # warm install bump is ~50ms, but cold filesystem walks (heal_
            # external_refs scans the whole project tree) can push 1-2s and
            # users have mistaken the quiet pause for a hang.
            try:
                asm = _assembler(project_dir)
                # HATS-469: ``Assembler.bump`` was replaced by ``_refresh``;
                # the bump pipeline is now an explicit composition (same
                # as ``cli/assembly.py::do_bump``).
                from ..materialize import compose_for_role
                from ..migration_assert import assert_runtime_hooks_resolve
                from ..migration_backup import snapshot_pre_bump

                # HATS-549: pre-bump snapshot for the no-version-change
                # in-process branch. The version-change branch above
                # delegates to ``_bump_internal``, which itself calls
                # ``do_bump`` and snapshots there. Without the explicit
                # call here, no-op self-update on a stuck project would
                # skip the safety net entirely.
                #
                # BackupError extends OSError → falls through to the
                # outer ``except (AssemblyError, ValueError, OSError)``
                # which renders "Bump failed:" and preserves
                # before_rules/before_skills as the after-state.
                inproc_backup = snapshot_pre_bump(project_dir, label="bump")

                with console.status(
                    f"[cyan]Migrating / refreshing[/] {active_role} …",
                    spinner="dots",
                ):
                    asm._run_v07_migration(
                        force=migrate_force, check_branches=check_branches,
                    )
                    cfg = asm.project_config
                    role_name = cfg.active_role or cfg.default_role
                    bump_result = (
                        compose_for_role(asm, role_name) if role_name else None
                    )
                    asm._refresh(install_time=True, result=bump_result)
                    asm._run_diagnostics()
                    # HATS-549 Phase 3: end-of-bump smoke-assert.
                    # Mirrors do_bump's final step. Failure surfaces
                    # via the outer AssemblyError/OSError except handler
                    # which renders "Bump failed:" — composition diff
                    # below shows no changes.
                    assert_runtime_hooks_resolve(
                        project_dir, backup_path=inproc_backup,
                    )
                if bump_result:
                    after_rules = {r.name for r in bump_result.rules}
                    after_skills = {s.name for s in bump_result.skills}
                    if bump_result.errors:
                        for err in bump_result.errors:
                            console.print(f"  [yellow]{err}[/]")
                else:
                    after_rules, after_skills = set(), set()
            except (AssemblyError, ValueError, OSError) as e:
                console.print(f"  [red]Bump failed[/]: {e}")
                after_rules, after_skills = before_rules, before_skills
                # HATS-549 Phase 3 wiring: surface the failure as a
                # non-zero CLI exit so callers (CI, scripts, hooks)
                # detect the bump didn't complete. Pre-fix this branch
                # swallowed AssemblyError and reported "Bump failed:"
                # on stdout but returned exit 0 — defeating the
                # safety-net contract end-of-bump smoke-assert
                # established.
                bump_in_process_failed = True

        added_r = sorted(after_rules - before_rules)
        removed_r = sorted(before_rules - after_rules)
        added_s = sorted(after_skills - before_skills)
        removed_s = sorted(before_skills - after_skills)
        has_diff = bool(added_r or removed_r or added_s or removed_s)
        if has_diff:
            for r in added_r:
                console.print(f"  [green]+[/] rule: {r}", highlight=False)
            for r in removed_r:
                console.print(f"  [red]-[/] rule: {r}", highlight=False)
            for s in added_s:
                console.print(f"  [green]+[/] skill: {s}", highlight=False)
            for s in removed_s:
                console.print(f"  [red]-[/] skill: {s}", highlight=False)
        else:
            console.print("  [dim]No composition changes[/]")

        # HATS-549 Phase 3: propagate in-process bump failure as a
        # non-zero exit so the safety-net contract holds for the
        # no-version-change path too (`do_bump` already does this
        # natively via the AssemblyError-return-1 branch).
        if bump_in_process_failed:
            sys.exit(1)


# HATS-285: `ai-hats self migrate` removed. Migration is transparent inside
# `Assembler.set_role` / `Assembler._refresh(install_time=True)` (HATS-469;
# the latter is reached via init and the do_bump CLI pipeline). Yaml-side
# migration lives in `ProjectConfig.from_yaml`. Cleanup of obsolete files
# is registry step=3 (HATS-471).

# HATS-415/469: `ai-hats self migrate-v07` removed. The v0.6 → v0.7 layout
# migration runs inline in the `do_bump` CLI pipeline (and on `Assembler.init`
# re-init for existing projects), exposed via
# `ai-hats self update --migrate-force` / `--check-branches`. Helpers
# (`migration_guidance`,
# `empty_composition`, `render_user_edits_refusal`) live in
# :mod:`ai_hats.migration_v07`; the Assembler owns hook-source and
# tier-2 source-lookup discovery as private methods.
