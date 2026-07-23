"""`ai-hats self update` — self-maintenance of the tool."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ai_hats_core import scrubbed_git_env
from .. import health
from ..paths import PROJECT_CONFIG, ENV_AI_HATS_VENV
from ..constants import ENV_REPO_URL, ENV_LAUNCHER_DEST
from ._helpers import _assembler, _project_dir, console, logger

if TYPE_CHECKING:
    from ..channel import ChannelResolution
    from ..self_heal import HealResult

# HATS-496: accept tag / branch / full-or-short SHA as a --revision argument.
# Bare SHA detection skips the ls-remote pre-flight (git ls-remote returns
# tags/branches keyed by refspec, not arbitrary commit ids — for raw SHA we
# defer to pip's own resolution downstream).
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)

# Lowest supported interpreter (pyproject requires-python>=3.11); uv provisions it.
PINNED_PYTHON = "3.11"


def _require_uv() -> None:
    """Fail loud with the install hint if uv is missing (D2: no pip fallback).

    uv is the single hard dependency — no pip fallback (D2). Called only right
    before a real uv call, so reuse / already-current no-ops stay green without
    uv (idempotency).
    """
    import shutil

    if shutil.which("uv") is None:
        console.print(
            "[red]ai-hats requires uv[/] but it was not found on PATH.\n"
            "  Install: [bold]curl -LsSf https://astral.sh/uv/install.sh | sh[/]\n"
            "  then re-run."
        )
        sys.exit(1)


# HATS-337: AI_HATS_REPO_URL env overrides the default git URL, mirroring
# the bash launcher (HATS-339) so a single env var pins the install source
# end-to-end (CI, airgapped mirrors, custom forks).
# HATS-766: public default is anonymous git+https (override still accepts ssh/local).
def _git_install_url() -> str:
    return os.environ.get(ENV_REPO_URL, "git+https://github.com/muratovv/ai-hats.git")


def _build_update_cmd(ref: str | None = None, target_python: str | Path | None = None) -> list[str]:
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
    # B1 (HATS-763): pin --python or `uv pip install` targets the nearest cwd
    # venv, not this interpreter. `--reinstall` == pip's `--force-reinstall`.
    python_bin = str(target_python) if target_python is not None else sys.executable
    return [
        "uv",
        "pip",
        "install",
        "--python",
        python_bin,
        "--reinstall",
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


def _render_heal_result(result: "HealResult | None") -> None:
    """Render a self-heal result to the console — UI only; the detect/lock/re-point
    logic lives in ``self_heal.run_editable_heal`` (HATS-966)."""
    if result is None:
        return
    for h in result.healed:
        console.print(
            f"[green]· self-heal[/] re-pointed [bold]{h.provider.module}[/] → {h.canonical}"
        )
    for w in result.warned:
        console.print(f"[yellow]· self-heal[/] {w.provider.module}: {w.reason}\n    fix: {w.fix}")


@click.command("heal-editables", hidden=True)
def heal_editables() -> None:
    """Re-point stale surface-plugin editables (internal; called by the launcher)."""
    from ..self_heal import run_editable_heal

    _render_heal_result(run_editable_heal())


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
            env=scrubbed_git_env(),
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


def _versioned_layout_dormant(project_dir: Path, *, pre_existing_versioned: bool) -> bool:
    """True iff a usable versioned install is being ignored by a stale launcher.

    The runtime symptom (HATS-655): a complete ``versions/<sha>/`` install already
    existed at the start of this update, yet **this** managed process is running
    from the legacy ``.venv`` (``current_run_sha → None``) rather than from the
    versioned venv. On a managed install (``.venv`` or ``versions/<sha>/``, not an
    override, not editable — :func:`_is_managed_install`) the only reasons to be on
    ``.venv`` instead of the existing versioned install are (a) a host launcher
    that predates ``versions/current`` resolution (HATS-647) and cannot select it,
    or (b) the **first** migration update (no versioned install yet). The
    ``pre_existing_versioned`` gate excludes (b), leaving (a): the launcher is
    stale and the versioned layout — crash-safe blue-green updates, orphan-version
    GC, legacy ``.venv`` reclaim — is silently dormant.

    Pure: composes existing predicates, no marker / byte-compare / subprocess.
    """
    if not pre_existing_versioned:
        return False  # first migration update — running from .venv is expected
    if not _is_managed_install(project_dir):
        return False  # override / editable — not our launcher's concern
    from ..version_refs import current_run_sha

    return current_run_sha(project_dir) is None  # on .venv despite versioned exists


def _installed_launcher_path() -> Path:
    """Best-effort path to the host launcher, for an accurate advisory hint.

    Resolution: ``AI_HATS_LAUNCHER_DEST`` env override → ``ai-hats`` on ``PATH``
    (``shutil.which`` — names the launcher that actually invoked this run) → the
    documented default ``~/.local/bin/ai-hats``. The env-override and default
    endpoints match ``install-launcher.sh``'s destination logic; the ``PATH``
    step is an extra accuracy fallback the installer itself does not have.
    Display-only; never written.
    """
    import shutil

    dest = os.environ.get(ENV_LAUNCHER_DEST)
    if dest:
        return Path(dest).expanduser()
    found = shutil.which("ai-hats")
    if found:
        return Path(found)
    return Path.home() / ".local" / "bin" / "ai-hats"


def _sanctioned_launcher_dest() -> Path:
    """The ONE on-PATH ``ai-hats`` entry that is legitimate (HATS-791).

    The host launcher install target: ``AI_HATS_LAUNCHER_DEST`` env override →
    documented default ``~/.local/bin/ai-hats``. Mirrors
    ``install-launcher.sh``'s ``DEST`` logic exactly. Unlike
    :func:`_installed_launcher_path`, this deliberately does NOT fall back to
    ``shutil.which`` — the whole point is to compare *every* ``ai-hats`` on
    ``$PATH`` against the sanctioned target, so resolving to whatever PATH finds
    would defeat the detector.
    """
    dest = os.environ.get(ENV_LAUNCHER_DEST)
    if dest:
        return Path(dest).expanduser()
    return Path.home() / ".local" / "bin" / "ai-hats"


def find_stray_launchers(
    path_env: str | None = None,
    sanctioned: Path | None = None,
) -> list[Path]:
    """Scan ``$PATH`` for ``ai-hats`` executables OUTSIDE the sanctioned host
    launcher (HATS-791 stray-shadow detector).

    A stray is any ``<dir>/ai-hats`` on ``PATH`` whose resolved path differs
    from the sanctioned launcher destination (``AI_HATS_LAUNCHER_DEST`` or
    ``~/.local/bin/ai-hats``). These are the "shadow" binaries a stale
    ``pip install ai-hats`` into a project app-venv can leave ahead of the host
    launcher. PURE + deterministic: takes ``PATH`` and the sanctioned dest as
    args (defaulting to the live environment) so it is unit-testable without
    mutating the process environment.

    NEVER deletes — destructive-actions rule. Returns the de-duplicated list of
    stray paths in PATH order; callers WARN + instruct.
    """
    raw = path_env if path_env is not None else os.environ.get("PATH", "")
    target = sanctioned if sanctioned is not None else _sanctioned_launcher_dest()
    try:
        target_resolved = target.expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        target_resolved = target
    strays: list[Path] = []
    seen: set[Path] = set()
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "ai-hats"
        try:
            if not (candidate.is_file() and os.access(candidate, os.X_OK)):
                continue
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved == target_resolved:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        strays.append(candidate)
    return strays


def _stray_shadow_warning(strays: list[Path]) -> str:
    """Build the WARN-and-instruct text for detected stray launchers (HATS-791).

    Warn only — never auto-delete (destructive-actions rule). Names the
    sanctioned launcher and the remediation (uninstall from the offending venv
    / remove the stray) so a user can clean it up by hand.
    """
    sanctioned = _sanctioned_launcher_dest()
    lines = [
        "ai-hats: stray 'ai-hats' executables found on PATH outside the host launcher.",
        f"  Sanctioned launcher: {sanctioned}",
        "  These shadow the host launcher and may run a stale ai-hats mis-resolved:",
    ]
    lines += [f"    - {p}" for p in strays]
    lines += [
        "  ai-hats will NOT delete them. To fix, for each stray either:",
        "    - uninstall ai-hats from its venv:  uv pip uninstall ai-hats",
        "    - or remove the stray file, and ensure the host launcher dir precedes it on PATH.",
    ]
    return "\n".join(lines)


def _build_install_cmd(python_exe: str, install_spec: str) -> list[str]:
    """uv-install ``install_spec`` into the venv owning ``python_exe``.

    HATS-764: ``install_spec`` is the resolved channel target —
    ``ai-hats @ git+https://…@<sha>`` (edge), ``ai-hats==<ver>`` (stable), or a
    bare local-path repo (e2e harness). The PEP 508 / local-path shaping moved
    into the channel resolver (:func:`ai_hats.channel._edge_install_spec`); this
    builder is now a thin uv-command wrapper. ``--reinstall`` == pip's
    ``--force-reinstall``; ``--python`` (B1, HATS-763) pins the interpreter so
    uv targets it rather than the nearest cwd venv.
    """
    return ["uv", "pip", "install", "--python", python_exe, "--reinstall", install_spec]


def _run_post_install_verify(python_exe: str) -> tuple[bool, str]:
    """Prove the just-installed tree is usable; return ``(ok, detail)``.

    A fresh interpreter is what makes this meaningful — the calling process
    still holds the pre-install code in memory (HATS-213).
    """
    with console.status("[cyan]Verifying install …[/]", spinner="dots"):
        verify = subprocess.run(
            [python_exe, "-m", "ai_hats._bootstrap", "verify"],
            capture_output=True,
            text=True,
        )
    if verify.returncode == 0:
        return True, ""
    return False, (verify.stderr or verify.stdout or "").strip() or "see logs"


def _flip_current(project_dir: Path, sha: str) -> None:
    """Atomically point ``versions/current`` at ``sha`` (tmp-write + replace).

    The pointer is the single source of truth the launcher reads; the rename
    is atomic on the same filesystem so a crash can never leave a torn
    pointer. Flipped only after a fully-successful install (HATS-647) —
    crash-during-install leaves ``current`` untouched (still the old sha), so
    the tool never bricks.
    """
    from ai_hats_core import atomic_write_text
    from ..paths import current_pointer

    # atomic_write_text creates the parent (versions/) and writes via a unique
    # tmp + os.replace — same atomicity as the prior inline form (HATS-716).
    atomic_write_text(current_pointer(project_dir), f"{sha}\n")


def _version_string(python_exe: str) -> str:
    """Read ``ai_hats.__version__`` from an arbitrary venv's interpreter."""
    result = subprocess.run(
        [python_exe, "-c", "from ai_hats import __version__; print(__version__)"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _pause_after_complete_for_test() -> None:
    """Test-only seam (HATS-650 e2e): block between the ``.complete`` write and
    the ``current`` flip so an e2e can deterministically interleave a concurrent
    GC — or a kill — while the install still holds the version lock. Gated by
    ``AI_HATS_TEST_PAUSE_AFTER_COMPLETE=<gate>``; unset (a no-op) in production.

    Signals readiness by touching ``<gate>.ready`` (the install is now paused,
    ``.complete`` written, ``current`` not yet flipped, lock held), then blocks
    until ``<gate>`` appears — the test creates it to release. Bounded so a buggy
    test can never hang the process forever.
    """
    gate = os.environ.get("AI_HATS_TEST_PAUSE_AFTER_COMPLETE")
    if not gate:
        return
    import time

    Path(gate + ".ready").write_text("", encoding="utf-8")
    gate_path = Path(gate)
    deadline = time.monotonic() + 120
    while not gate_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)


def _run_managed_versioned_update(
    project_dir: Path,
    resolution: ChannelResolution,
    *,
    old_version: str,
    active_role: str | None,
    config_unreadable: bool,
    migrate_force: bool,
    check_branches: bool,
) -> None:
    """Blue-green ``self update`` for the managed default venv (HATS-647).

    HATS-764: driven by a :class:`~ai_hats.channel.ChannelResolution` — the
    version dir is named by ``resolution.version_id`` (edge sha | stable tag)
    and the build installs ``resolution.install_spec``. The source-shaping that
    used to live here (local-path HEAD re-resolution, PEP 508 url@ref) moved
    into the pure resolver upstream; ``version_id`` is authoritative.

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
        is_usable_version,
        read_current_sha,
        version_dir,
    )

    # HATS-764: version_id is resolved upstream (edge head sha / stable tag) and
    # is authoritative — names versions/<version_id>/. The caller fails loud on
    # an unresolvable edge sha BEFORE building the resolution, so a None here is
    # a contract violation, not an offline case.
    target_sha = resolution.version_id
    if not target_sha:
        console.print(
            "[red]Update failed[/]: the channel resolution carries no version "
            "id; a versioned install needs one to name versions/<version_id>/."
        )
        sys.exit(2)

    # HATS-655: snapshot whether a complete versioned install already exists
    # BEFORE this update builds one. This distinguishes a stale-launcher dormancy
    # (a prior versioned install this run is still ignoring) from the first
    # migration update (where running from .venv is expected, not a symptom).
    pre_existing_versioned = read_current_sha(project_dir) is not None

    # HATS-650 (R3): the whole acquire critical section — the GC at start, the
    # install / reuse, and the atomic `current` flip — runs under the crash-safe
    # version lock, serialized against a concurrent `self update` and against the
    # opportunistic GC at the create_session chokepoint. The lock is an fcntl
    # advisory lock (filelock); the kernel releases it on process death, so a
    # kill mid-install never wedges future cleanup. Blocking with a generous
    # timeout — a second concurrent update waits its turn rather than racing the
    # `.complete → flip` window (which would otherwise let a peer GC reclaim the
    # just-completed target out from under the flip). VersionLockError is caught
    # at the call site and surfaced as a clean "another update in progress".
    from ..version_lock import INSTALL_LOCK_TIMEOUT, versions_lock
    from ..version_recovery import (
        reclaim_legacy_venv,
        reclaim_orphan_versions,
        sweep_incomplete_versions,
    )

    with versions_lock(project_dir, timeout=INSTALL_LOCK_TIMEOUT):
        # HATS-648 (R1): clean incomplete residue from prior crashed updates
        # before staging the new build. Same idempotent, TTL-guarded sweep as
        # the create_session chokepoint (a *recent* incomplete dir may be a
        # concurrent update in flight, so it is kept). No-silent-caps.
        for _residue in sweep_incomplete_versions(project_dir):
            console.print(f"[dim]Reclaimed incomplete residue: versions/{_residue.name}[/]")

        # HATS-649 (R2): reclaim complete versions orphaned by earlier runs —
        # `self update` is the canonical "next invocation" that converges crash
        # residue. Reclaim-on-certain-death: only versions with no live liveness
        # ref, never `current`. Runs BEFORE the flip below, so the still-
        # `current` version this updater itself runs from is skipped;
        # `target_sha` (the about-to-be-installed/reused dir, not yet `current`)
        # is protected explicitly via keep.
        for _orphan in reclaim_orphan_versions(project_dir, keep_shas={target_sha}):
            console.print(f"[dim]Reclaimed orphaned version: versions/{_orphan.name}[/]")

        # HATS-653 (Phase B): once this updater itself runs from a complete
        # versioned venv (current_run_sha not None), the orphaned pre-versioning
        # legacy .venv is dead weight — reclaim it. The first migration update
        # runs FROM .venv (current_run_sha None) and is correctly skipped; the
        # reclaim converges on a later update / session. Inside the lock is
        # harmless: .venv lives outside versions/ and reclaim_legacy_venv never
        # re-enters the version lock.
        if (_venv := reclaim_legacy_venv(project_dir)) is not None:
            console.print(f"[dim]Reclaimed legacy venv: {_venv}[/]")

        vdir = version_dir(project_dir, target_sha)
        already_current = read_current_sha(project_dir) == target_sha

        if already_current:
            console.print(
                f"[green]Already up to date[/] ({old_version}) "
                f"[dim]— current → {target_sha[:12]}[/]"
            )
            new_python = sys.executable
        elif is_usable_version(project_dir, target_sha):
            # A prior successful install of this exact sha that is still USABLE
            # (sentinel present AND bin/python on disk — HATS-790): trust it
            # and reuse — just (re)flip current below. A blind reinstall would
            # rmtree a dir that may be a LIVE pinned run's frozen env (e.g. run
            # still on sha A while current already moved to B, then a `self update
            # --revision A`), so keying reuse on the sentinel is a safety guard,
            # not only an optimisation (HATS-648). HATS-657: gate on usability,
            # not bare completeness — a complete-but-python-broken dir (host python
            # upgrade dangling bin/python) is NOT reused (that would run the build
            # / bump with a dead interpreter); it falls through to the rebuild
            # branch below, which rmtree+reinstalls it (the proper heal).
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
            _require_uv()  # only here — reuse/no-op above needs no uv
            if vdir.exists():
                shutil.rmtree(
                    vdir, ignore_errors=True
                )  # safe-delete: ok incomplete-venv (crash residue, rebuilt fresh)
            vdir.parent.mkdir(parents=True, exist_ok=True)
            with console.status(
                f"[cyan]Creating versioned venv[/] versions/{target_sha[:12]} …",
                spinner="dots",
            ):
                # uv provisions the interpreter (drops the host-Python precondition).
                venv_proc = subprocess.run(
                    ["uv", "venv", "--python", PINNED_PYTHON, str(vdir)],
                    capture_output=True,
                    text=True,
                )
            if venv_proc.returncode != 0:
                console.print(f"[red]Update failed[/] (venv create): {venv_proc.stderr}")
                # HATS-718: non-zero exit so scripted chains
                # (`self update && self init`), CI, and agents reading exit
                # codes detect the install never completed — mirrors the
                # HATS-549 contract (exit 1 == "failed"). current untouched.
                sys.exit(1)
            new_python = str(vdir / "bin" / "python")
            with console.status(
                "[cyan]Downloading ai-hats from GitHub …[/] "
                "[dim](uv install — may take a minute)[/]",
                spinner="dots",
            ):
                install = subprocess.run(
                    _build_install_cmd(new_python, resolution.install_spec),
                    capture_output=True,
                    text=True,
                )
            if install.returncode != 0:
                # current is untouched → the tool still runs on the old sha. The
                # incomplete dir (no sentinel) is swept by version_recovery.
                console.print(f"[red]Update failed[/]: {install.stderr}")
                sys.exit(1)  # HATS-718: failed install must be machine-detectable
            with console.status("[cyan]Verifying install …[/]", spinner="dots"):
                verify = subprocess.run(
                    [new_python, "-m", "ai_hats._bootstrap", "verify"],
                    capture_output=True,
                    text=True,
                )
            if verify.returncode != 0:
                warning = (verify.stderr or verify.stdout or "").strip() or "see logs"
                console.print(f"[red]Update failed[/] (verify): {warning}")
                # HATS-718: verify runs BEFORE the .complete sentinel + current
                # flip, so a failure means the new version is abandoned (current
                # still old). Exit non-zero so callers know it did not complete.
                sys.exit(1)
            # Sentinel written LAST, only after a fully-successful install+verify
            # — the authoritative completeness marker (HATS-648). Only then is
            # the atomic flip allowed; current never points at a dir lacking
            # .complete.
            complete_sentinel(project_dir, target_sha).write_text("", encoding="utf-8")
            # HATS-650 e2e seam — no-op unless AI_HATS_TEST_PAUSE_AFTER_COMPLETE
            # is set. Lets a test freeze the install here (lock held, .complete
            # written, current not yet flipped) to exercise the corruption window
            # the lock closes.
            _pause_after_complete_for_test()
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
                "reclaimed automatically by GC.[/]"
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
        bump_env = {**os.environ, ENV_AI_HATS_VENV: str(vdir)}
        proc = subprocess.run(
            bump_cmd,
            cwd=str(project_dir),
            env=bump_env,
            check=False,
        )
        if proc.returncode != 0:
            console.print(
                f"  [yellow]Bump (fresh interpreter) exited {proc.returncode} "
                f"— review output above[/]"
            )

    # HATS-655: if a versioned install already existed yet this managed update
    # still ran from the legacy .venv, the host launcher predates versions/current
    # resolution (HATS-647) and the versioned layout is silently dormant — name
    # what's off and advise the one-time host-level launcher refresh. NEVER auto-
    # write the launcher: it is host-global (one entry point for ALL projects); a
    # per-project write risks cross-project breakage and a bootstrap-brick.
    if _versioned_layout_dormant(project_dir, pre_existing_versioned=pre_existing_versioned):
        launcher = _installed_launcher_path()
        sha = read_current_sha(project_dir) or target_sha
        console.print(
            "\n[yellow]Heads up:[/] your host launcher is not using the versioned install."
        )
        console.print(
            f"  [dim]versions/{sha[:12]} is active, but this run came from the "
            f"legacy .venv — {launcher} predates versioned-layout resolution.[/]"
        )
        console.print(
            "  [dim]Inactive: crash-safe blue-green updates, orphan-version GC, "
            "legacy .venv reclaim.[/]"
        )
        console.print(
            "  Refresh the host launcher (one-time): [bold]curl -sSL "
            "https://github.com/muratovv/ai-hats/raw/master/scripts/"
            "install-launcher.sh | bash[/]"
        )


# HATS-497: Install diagnostics for ``ai-hats config status`` Health section.
# Helpers below produce a flat dict of display-key → display-value. Layer
# boundary: install-level (interpreter, venv, source); does NOT touch the
# Assembler (which is project-level).


def _format_install_source() -> str:
    """Format the ``Source:`` line for ``ai-hats config status``.

    Reads PEP 610 ``direct_url.json`` via :func:`_read_direct_url`. Branches
    map to user-visible labels:

    - ``editable @ <url>``                            — local dev install
    - ``pinned @ <ref> → <sha>``                      — ``vcs_info`` has
      ``requested_revision`` AND it's not "HEAD" / branch-tip
    - ``git @ <ref-or-HEAD> → <sha>``                 — plain git install
    - ``installed @ <url>``                           — non-editable, non-vcs
      direct-URL install (wheel FILE / local path — ``direct_url.json`` present)
    - ``stable @ PyPI``                               — installed BY NAME from an
      index (HATS-779): pip/uv write NO ``direct_url.json`` for index-by-name
      installs, so a missing direct_url + a resolvable dist IS the released-wheel
      (stable channel) case

    Falls back to ``"(unknown — direct_url.json missing)"`` only when ai-hats
    has no installed-dist metadata at all (a raw source / PYTHONPATH run).
    Truncates SHA to 7 chars for display; full SHA stays in direct_url.json.
    """
    data = _read_direct_url()
    if data is None:
        # No PEP 610 direct_url. Two sub-cases (HATS-779): ai-hats was installed
        # BY NAME from an index (the stable channel — `uv pip install
        # ai-hats==<tag>`; pip/uv never write direct_url.json for index-by-name
        # installs), or ai-hats has no installed dist at all. The package's own
        # metadata disambiguates: resolvable ⇒ released wheel; absent ⇒ unknown.
        try:
            distribution("ai-hats")
        except PackageNotFoundError:
            return "(unknown — direct_url.json missing)"
        return "stable @ PyPI"
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

        return str(files("ai_hats_library"))
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

    env_val = os.environ.get(ENV_AI_HATS_VENV)
    if env_val:
        env_path = Path(env_val.replace("~", str(Path.home()))).resolve()
        if env_path == venv_real:
            return "AI_HATS_VENV env"

    # ai-hats.yaml venv_path (relative to project_dir, expanded by paths.py).
    try:
        project_dir = _project_dir()
        yaml_path = project_dir / PROJECT_CONFIG
        if yaml_path.is_file():
            # Lightweight grep — matches the launcher's bash-side scan
            # (scripts/ai-hats-launcher) rather than loading the full
            # ProjectConfig (heavier import, fires yaml-load WARNs).
            for line in yaml_path.read_text().splitlines():
                if line.startswith("venv_path:"):
                    candidate = line.split(":", 1)[1].strip().strip("'\"")
                    if candidate:
                        candidate_path = Path(candidate.replace("~", str(Path.home())))
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
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=scrubbed_git_env(),
        )
        if sha.returncode != 0:
            return None
        branch = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=scrubbed_git_env(),
        )
        porcelain = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=scrubbed_git_env(),
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


# HATS-766: anonymous GitHub Commits API read replaces a per-update shallow clone.
_CHANGELOG_API_URL = "https://api.github.com/repos/muratovv/ai-hats/commits"
_CHANGELOG_COUNT = 7


def _get_changelog() -> str:
    """Recent non-merge commit subjects from the public GitHub Commits API (HATS-766).

    Cosmetic "Recent changes" block — any failure (offline, timeout, 403
    rate-limit, bad JSON) fails soft to ``""``. Merge commits dropped
    client-side (``parents`` > 1); over-fetch 15 then slice 7.
    """
    import urllib.error
    import urllib.request

    url = f"{_CHANGELOG_API_URL}?per_page=15"
    # urllib injects a default Python-urllib UA that GitHub accepts (a UA-less
    # request is 403'd); we set an explicit one anyway. URL is a pinned https
    # constant, so the B310 scheme audit is satisfied.
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ai-hats"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — pinned https constant
            commits = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        logger.debug("changelog fetch failed", exc_info=True)
        return ""
    if not isinstance(commits, list):
        return ""

    lines: list[str] = []
    for entry in commits:
        if not isinstance(entry, dict):
            continue
        if len(entry.get("parents") or []) > 1:
            continue  # merge commit — hide (matches old --no-merges)
        sha = (entry.get("sha") or "")[:7]
        message = ((entry.get("commit") or {}).get("message") or "").strip()
        subject = message.splitlines()[0] if message else ""
        if sha and subject:
            lines.append(f"{sha} {subject}")
        if len(lines) >= _CHANGELOG_COUNT:
            break
    return "\n".join(lines)


def _snapshot_dep_versions() -> dict[str, str]:
    """Snapshot ``{distribution_name: version}`` via a fresh ``uv pip list`` subprocess.

    Fresh subprocess avoids importlib cache divergence between pre- and
    post-update — important for HATS-213 activation banner.

    HATS-763 (B2): a ``uv venv`` ships NO ``pip``, so the old
    ``python -m pip list`` returned nothing in a uv-built venv and silently
    blanked the banner. ``uv pip list --python <interp>`` reads any interpreter's
    env without needing pip installed there, and emits the SAME ``{name,version}``
    JSON shape — drop-in. ``--python sys.executable`` pins THIS env (B1).
    """
    import json
    import subprocess

    try:
        result = subprocess.run(
            ["uv", "pip", "list", "--python", sys.executable, "--format=json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        # OSError covers uv-missing (FileNotFoundError); the banner just shows no
        # deltas — non-critical snapshot, so no fail-loud here (uv is required by
        # the install paths that DO fail loud via _require_uv).
        logger.debug("uv pip list snapshot failed", exc_info=True)
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


def _probe_remote_state(
    project_dir: Path,
    *,
    remote_url: str | None = None,
    ref: str = "master",
):
    """Run the ahead/behind probe. Returns the cache entry or ``None``.

    Wrapper around :func:`update_check.checker.run_check` that swallows
    transport errors — a network blip MUST NOT block an explicit
    ``self update`` invocation. Returns ``None`` only when the probe could
    not resolve SHAs (no network, non-git install, malformed remote).

    HATS-766: ``remote_url`` / ``ref`` (bare URL + ``HEAD``) make the guard probe
    the same repo the edge install targets, not hardwired upstream ``master``.
    """
    try:
        from ..update_check.checker import run_check

        return run_check(project_dir, remote_url=remote_url, ref=ref)
    except (ImportError, OSError, ValueError):
        # ImportError: update_check missing (packaging regression) → guard
        # inactive, never brick the recovery path (HATS-987).
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
            f"the edge remote [bold]{latest}[/] by {entry.ahead} commits. "
            f"[red]Refusing to downgrade.[/]\n"
            f"Use [bold]--force-downgrade[/] to override "
            f"(will replace your local install).",
            highlight=False,
        )
    else:  # diverged
        console.print(
            f"[red]Installed version[/] [bold]{installed}[/] has diverged "
            f"from the edge remote [bold]{latest}[/] "
            f"(local ahead: {entry.ahead}, remote ahead: {entry.behind}). "
            f"[red]Refusing to downgrade.[/]\n"
            f"Use [bold]--force-downgrade[/] to override "
            f"(will replace your local install).",
            highlight=False,
        )


# ---------- HATS-764: channel-driven source + per-channel guard ----------


def _read_harness(project_dir: Path):
    """Read ``(channel, repo, path)`` from ai-hats.yaml for the install router.

    HATS-764/581: ``self update`` must self-heal, so resolution degrades:
      - no config file → ``STABLE`` (greenfield, the documented default).
      - present but unparseable by the installed code → ``EDGE`` recovery
        (install from the configured source / upstream, never an unreachable
        PyPI release), so a broken config can't strand the recovery command.
      - otherwise → the configured ``harness`` block.

    Independent of the snapshot-block ``_assembler`` read so the channel is
    available for the guard even on the degraded path. The parse's WARNs
    (deprecated-strip / default_role heal / unknown key) are suppressed here —
    the authoritative copy fires once from that ``_assembler`` read; without
    this they would print twice per ``self update`` (HATS-408 double-WARN).
    """
    import contextlib
    import io

    from ..models import Channel, ProjectConfig, ProjectConfigError

    config_path = project_dir / PROJECT_CONFIG
    if not config_path.exists():
        return Channel.STABLE, None, None  # greenfield → documented default
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            h = ProjectConfig.from_yaml(config_path).harness
    except ProjectConfigError:
        # File EXISTS but the installed code can't parse it → edge recovery
        # (install from the configured source / upstream, NOT an
        # unreachable/unpublished PyPI release — HATS-581 self-heal).
        # DECIDED (HATS-778): the one-cycle edge drift this causes on a stable
        # project is ACCEPTED, not worked around. edge == master HEAD, gated
        # green by the maintainer e2e gate (no-broken-master, HATS-550), so
        # recovering there is low-risk; the scenario is rare and self-heals (the
        # recovery update rewrites a valid yaml → next update restores stable).
        # A stable-aware recovery would have to infer the prior channel from the
        # installed artifact (HATS-779: no direct_url.json + resolvable dist ⇒
        # was stable) AND add a stable→edge fallback for the unreachable-PyPI
        # case — not worth it for this path. See tasks/HATS-778 for the trade-off.
        return Channel.EDGE, None, None
    return h.channel, h.repo, h.path


def _classify_semver_downgrade(installed: str, target: str) -> bool:
    """True if installing ``target`` (a published PyPI version) downgrades
    ``installed`` — the stable channel's semver-monotonic guard (HATS-764).

    Uses ``packaging.version.Version`` (PEP 440), NOT a naive ``vX.Y.Z``
    tuple-parse: ``installed`` is a setuptools-scm string (e.g.
    ``0.8.1.dev105+g589f167``) while ``target`` is a clean release (``0.8.1``);
    a tuple-parse chokes on the dev suffix and mis-orders ``0.8.1.dev105`` vs
    ``0.8.1``. An unparseable ``installed`` (editable ``unknown``) → allow.
    """
    from packaging.version import InvalidVersion, Version

    try:
        return Version(target) < Version(installed)
    except InvalidVersion:
        return False


def _render_semver_downgrade_refusal(installed: str, target: str) -> None:
    console.print(
        f"[red]Installed stable[/] [bold]{installed}[/] is newer than the "
        f"latest published tag [bold]{target}[/]. "
        f"[red]Refusing to downgrade.[/]\n"
        f"Use [bold]--force-downgrade[/] to override.",
        highlight=False,
    )


def _build_managed_resolution(
    channel,
    *,
    revision_repo: str | None,
    revision_sha: str | None,
    harness_repo: str | None,
    latest_stable: str | None,
) -> ChannelResolution:
    """Compose the :class:`ChannelResolution` for the managed versioned install.

    ``--revision`` (``revision_sha`` set) → explicit edge-style pin at that sha
    against ``_git_install_url()`` (channel-agnostic, HATS-496). stable →
    ``ai-hats==<latest_stable>``. edge → the edge repo's actual HEAD via
    ``git ls-remote`` (NOT the upstream-master probe, which is hardwired to a
    possibly-different repo — the probe drives the guard only). Fails loud
    (exit 2) when an edge sha can't resolve.
    """
    from ..channel import fetch_edge_head_sha, resolve_channel, resolve_edge_repo
    from ..models import Channel

    if revision_sha is not None:
        return resolve_channel(Channel.EDGE, repo=revision_repo, head_sha=revision_sha)
    if channel is Channel.STABLE:
        return resolve_channel(Channel.STABLE, latest_version=latest_stable)
    repo = resolve_edge_repo(harness_repo)
    head_sha = fetch_edge_head_sha(repo)
    if not head_sha:
        console.print(
            "[red]Update failed[/]: could not resolve a target revision to "
            "install (offline, or the edge remote is unreachable). A versioned "
            "install needs a resolvable sha to name versions/<version_id>/."
        )
        sys.exit(2)
    return resolve_channel(Channel.EDGE, repo=repo, head_sha=head_sha)


def _run_editable_update(
    project_dir: Path,
    path: str,
    *,
    old_version: str,
    active_role: str | None,
    config_unreadable: bool,
    migrate_force: bool,
    check_branches: bool,
) -> None:
    """channel: local — editable reinstall of the working tree in place.

    No versioned dir, no ``current`` flip: the working tree IS the live source
    (HATS-764). Mirrors the dev dogfooding ``uv pip install -e .``. The on-disk
    code is already current, so the post-install bump runs the standard
    fresh-interpreter subprocess only to refresh composition / migrations.
    """
    _require_uv()
    cmd = ["uv", "pip", "install", "--python", sys.executable, "-e", path]
    run_env = os.environ.copy()
    run_env["PYTHONDONTWRITEBYTECODE"] = "1"
    with console.status(
        f"[cyan]Editable reinstall[/] [dim](uv pip install -e {path})[/]",
        spinner="dots",
    ):
        result = subprocess.run(cmd, capture_output=True, text=True, env=run_env)
    if result.returncode != 0:
        console.print(f"[red]Update failed[/]: {result.stderr}")
        sys.exit(1)  # HATS-718: failed install must be machine-detectable
    console.print(f"[green]Editable reinstall[/]: uv pip install -e {path}")
    if active_role or config_unreadable:
        role_label = active_role or "(config unreadable — healing)"
        console.print(f"\n[bold]Re-assembling:[/] {role_label}")
        bump_cmd = [sys.executable, "-m", "ai_hats._bump_internal"]
        if migrate_force:
            bump_cmd.append("--migrate-force")
        if check_branches:
            bump_cmd.append("--check-branches")
        proc = subprocess.run(bump_cmd, cwd=str(project_dir), check=False)
        if proc.returncode != 0:
            console.print(
                f"  [yellow]Bump (fresh interpreter) exited {proc.returncode} "
                f"— review output above[/]"
            )


_TRIAGE_STYLE = {
    health.Status.OK: ("green", "OK"),
    health.Status.WARN: ("yellow", "WARN"),
    health.Status.BROKEN: ("red", "BROKEN"),
}


def _render_triage(reports: list[health.LayerReport]) -> None:
    """Print the per-layer triage, grouped by layer (HATS-595)."""
    console.print("\n[bold]Layer triage[/]")
    for layer in health.Layer:
        rows = [r for r in reports if r.layer is layer]
        if not rows:
            continue
        console.print(f"[dim]{layer.value}[/]")
        for r in rows:
            color, label = _TRIAGE_STYLE[r.status]
            console.print(f"  [{color}]{label:<6}[/] {r.name:<14} {r.detail}")
            if r.remediation:
                console.print(f"         [dim]→ {r.remediation}[/]")
    console.print()


def _reverify_layers(project_dir: Path, before: list[health.LayerReport]) -> None:
    """Re-run the triage after the update and report what the bump did NOT fix.

    The bump's ``_refresh`` already rebuilds the MANAGED layer, so this only
    confirms it — there is no separate heal step to run (HATS-595).
    """
    was_broken = {r.name for r in before if r.status is health.Status.BROKEN}
    if not was_broken:
        return
    still = {r.name for r in health.triage(project_dir) if r.status is health.Status.BROKEN}
    healed = was_broken - still
    if healed:
        console.print(f"[green]Layers restored:[/] {', '.join(sorted(healed))}")
    remaining = was_broken & still
    if remaining:
        console.print(f"[red]Still broken:[/] {', '.join(sorted(remaining))}")


def _invalidate_update_cache(project_dir: Path) -> None:
    """Drop the update-check cache after a self update (HATS-781).

    The cache is keyed only on ``project_dir`` + a 24h TTL, so without this a
    reinstall within the window leaves the banner reporting the PRE-update SHA
    and a stale ``behind`` count — nagging "update available" the moment the
    user finished updating. Idempotent and best-effort: a missing file or an
    unlink error must never fail the update itself.
    """
    try:
        from ..update_check.cache import cache_path

        cache_path(project_dir).unlink(
            missing_ok=True
        )  # safe-delete: ok update-check cache (ephemeral, re-probed next session)
    except (ImportError, OSError):
        pass  # ImportError: update_check missing → nothing to invalidate (HATS-987)


@click.command()
@click.option(
    "--migrate-force",
    is_flag=True,
    help="Bypass v0.6 → v0.7 user-edit refusal during auto-bump (logs WARN per overwritten file).",
)
@click.option(
    "--check-branches",
    is_flag=True,
    help="Warn if local branches modify any v0.7-migration path slated for deletion.",
)
@click.option(
    "--force-downgrade",
    is_flag=True,
    help="Bypass the ahead/diverged guard. Replaces the local "
    "install with the remote master state — destroys unpushed work in "
    "editable installs.",
)
@click.option(
    "--revision",
    "revision",
    default=None,
    metavar="REF",
    help="Install ai-hats at an explicit tag, branch, or commit SHA "
    "instead of remote master. Bypasses the ahead/diverged "
    "guard; pre-flight 'git ls-remote' validates the ref before any pip "
    "call. Editable target venv requires --force.",
)
@click.option(
    "--force",
    "force",
    is_flag=True,
    help="With --revision: overwrite the editable install in the target "
    "venv. No effect without --revision. Distinct from "
    "--force-downgrade, which only applies to plain master-targeted "
    "updates.",
)
@click.option(
    "--check",
    "check",
    is_flag=True,
    help="Diagnose only: print the per-layer triage and exit "
    "without writing anything. Exit 1 when a layer is broken, 0 when healthy "
    "or warn-only. Refuses the mutating flags.",
)
def update(
    migrate_force: bool,
    check_branches: bool,
    force_downgrade: bool,
    revision: str | None,
    force: bool,
    check: bool,
):
    """Update ai-hats from GitHub.

    Auto-bumps after install. ``bump`` now self-heals v0.6 →
    v0.7 layouts transparently for the common case (no user edits). If
    user edits are detected on the v0.6 canonical files, the bump
    refuses with per-file guidance — re-run with ``--migrate-force``
    after relocating the content (or to overwrite). ``--check-branches``
    surfaces a warning when local branches modify the paths slated for
    deletion.

    ``--revision <REF>`` pins the install to an explicit tag,
    branch, or commit SHA. Skips the downgrade probe / guard (D1). On an
    editable target venv, refuses unless ``--force`` is passed (D2). A
    pre-flight ``git ls-remote`` validates the ref before any pip call.
    """
    from .. import __version__ as old_version
    from ..assembler import AssemblyError
    from ..channel import ChannelResolveError, fetch_latest_stable_version
    from ..models import Channel, ProjectConfigError

    if check and (revision or force_downgrade or force):
        console.print("[red]--check is diagnose-only[/] and cannot combine with mutating flags.")
        sys.exit(2)

    console.print(f"Current version: [bold]{old_version}[/]")
    # HATS-318: surface which interpreter we're updating. When the wrapper has
    # already re-exec'd into <ai_hats_dir>/.venv, the install goes to that env
    # by virtue of sys.executable; this banner makes the target unambiguous.
    if "/.venv/bin/python" in sys.executable or "/versions/" in sys.executable:
        console.print(f"[dim]Target venv:[/] {sys.executable}")

    project_dir = _project_dir()

    # HATS-595: triage before any write, so --check can short-circuit here.
    reports = health.triage(project_dir)
    _render_triage(reports)
    if check:
        sys.exit(1 if health.worst_status(reports) is health.Status.BROKEN else 0)

    # HATS-966: repair a stale surface-plugin editable (e.g. a `cline` `.pth` left
    # dangling by a torn-down worktree) as part of the canonical "fix my env" run.
    from ..self_heal import run_editable_heal

    _render_heal_result(run_editable_heal())

    # HATS-764: the harness channel (config) selects BOTH the install source and
    # the downgrade guard. Read it up front, degrading to the stable default if
    # the installed code can't parse the config (`self update` self-heals).
    channel, harness_repo, harness_path = _read_harness(project_dir)

    # Built per channel / --revision below. `probe` feeds the edge (moving
    # target) git ahead/diverged guard; `latest_stable` the stable semver
    # guard; `revision_url`/`revision_sha` carry an explicit --revision pin.
    probe = None
    latest_stable: str | None = None
    revision_url: str | None = None
    revision_sha: str | None = None
    if revision:
        # HATS-496: --revision is an explicit, channel-agnostic pin. It
        # short-circuits the guard machinery (the user named the ref), refuses
        # an editable target unless --force, and pre-flights `git ls-remote`.
        revision_url = _git_install_url()
        if "://" not in revision_url:
            console.print(
                f"[red]--revision requires a git URL[/] "
                f"(AI_HATS_REPO_URL={revision_url!r} looks like a local path; "
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
            f"[cyan]Resolving ref[/] {revision} on {revision_url} …",
            spinner="dots",
        ):
            revision_sha = _resolve_ref(revision_url, revision)
        if revision_sha is None:
            console.print(f"[red]error:[/] ref '{revision}' not found on remote {revision_url}")
            sys.exit(2)

        console.print(
            "[yellow]Warning:[/] --revision bypasses the ahead/diverged "
            "guard. Installing arbitrary ref may downgrade your install."
        )
        console.print(f"  [dim]Resolved {revision} → {revision_sha}[/]")
    elif channel is Channel.LOCAL:
        # local: editable working-tree install — no remote probe / guard.
        pass
    elif channel is Channel.STABLE:
        # HATS-764: stable is pinned + semver-monotonic. Skip the git
        # ahead/diverged probe (a PyPI release has no master divergence) and
        # instead refuse a published tag whose semver is LOWER than installed.
        try:
            latest_stable = fetch_latest_stable_version()
        except ChannelResolveError as exc:
            console.print(
                f"[red]Update failed[/]: {exc}\n"
                "[dim]stable resolves the target version from PyPI — required "
                "even with --force-downgrade (which bypasses the guard, not the "
                "version fetch). Use [bold]--channel edge[/bold] to install from "
                "git instead.[/]"
            )
            sys.exit(2)
        if force_downgrade:
            console.print(
                "[yellow]Warning:[/] --force-downgrade bypasses the semver-monotonic guard."
            )
        elif _classify_semver_downgrade(old_version, latest_stable):
            _render_semver_downgrade_refusal(old_version, latest_stable)
            sys.exit(DOWNGRADE_REFUSAL_EXIT_CODE)
    else:
        # edge: moving target → keep the HATS-441 git ahead/diverged guard.
        # ``--force-downgrade`` opts back into the destructive replace for
        # callers who know what they're doing (discarding a stale dev branch).
        # HATS-766: probe the edge repo's HEAD (bare url; env > harness.repo >
        # upstream), not hardwired master — else a custom edge repo silently
        # disables the guard.
        from ..channel import resolve_edge_probe_url

        probe_url = resolve_edge_probe_url(harness_repo)
        probe = (
            None
            if force_downgrade
            else _probe_remote_state(project_dir, remote_url=probe_url, ref="HEAD")
        )
        if force_downgrade:
            console.print(
                "[yellow]Warning:[/] --force-downgrade bypasses the "
                "ahead/diverged guard. Your local install (including "
                "editable / unpushed commits) will be replaced by the "
                "edge remote's HEAD."
            )
        else:
            reason = _classify_downgrade(probe)
            if reason is not None:
                _render_downgrade_refusal(reason, probe)
                sys.exit(DOWNGRADE_REFUSAL_EXIT_CODE)

    # 1. Snapshot before update
    before_lib = _snapshot_library()
    before_deps = _snapshot_dep_versions()
    config_path = project_dir / PROJECT_CONFIG
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

    # HATS-764: route by channel.
    #  - local → editable in-place install of the working tree (no versioned
    #    dir, no current flip). --revision overrides the channel (HATS-496).
    if channel is Channel.LOCAL and not revision:
        _run_editable_update(
            project_dir,
            harness_path or ".",
            old_version=old_version,
            active_role=active_role,
            config_unreadable=config_unreadable,
            migrate_force=migrate_force,
            check_branches=check_branches,
        )
        _invalidate_update_cache(project_dir)  # HATS-781
        _reverify_layers(project_dir, reports)
        return

    # HATS-647/764: edge/stable on the managed default venv → blue-green
    # versioned install into versions/<version_id>/ (never the live venv) +
    # atomic current flip, so a concurrently-live run survives. Editable /
    # override venvs fall through to the legacy in-place install path below.
    if _is_managed_install(project_dir):
        from ..version_lock import VersionLockError

        resolution = _build_managed_resolution(
            channel,
            revision_repo=revision_url,
            revision_sha=revision_sha,
            harness_repo=harness_repo,
            latest_stable=latest_stable,
        )
        try:
            _run_managed_versioned_update(
                project_dir,
                resolution,
                old_version=old_version,
                active_role=active_role,
                config_unreadable=config_unreadable,
                migrate_force=migrate_force,
                check_branches=check_branches,
            )
        except VersionLockError as exc:
            # A concurrent `self update` holds the acquire lock past the timeout.
            # current is untouched → the tool still runs on the old sha; the
            # other update converges. Clean exit, no traceback.
            console.print(f"[red]Update failed[/] (another update in progress):\n{exc}")
            sys.exit(2)
        _invalidate_update_cache(project_dir)  # HATS-781
        _reverify_layers(project_dir, reports)
        return

    # 2. Install — short-circuited when the probe confirms the installed SHA
    # matches remote master AND ahead/behind are exactly (0, 0). The double
    # check guards mock environments where SHA detection returns identical
    # garbage on both sides (ahead/behind only hit (0, 0) when `git rev-list`
    # walked real commits). Skips pip's 10-15s re-download for a no-op; bump()
    # below still applies pending migrations.
    # HATS-496: --revision always re-installs — force the pip call so
    # direct_url.json's requested_revision is rewritten to the literal ref the
    # user typed (HATS-497 reads this), even if the SHA already matches.
    skip_install = (
        not force_downgrade
        and not revision
        and probe is not None
        and probe.installed_sha == probe.latest_sha
        and probe.ahead == 0
        and probe.behind == 0
    )
    if skip_install:
        console.print(f"[green]Already up to date[/] ({old_version}) [dim]— skipping reinstall[/]")
        new_version = old_version
    else:
        _require_uv()  # HATS-763: legacy in-place path also runs uv
        # HATS-764: stable on a non-managed (override) venv → install the
        # pinned PyPI release in place; edge / --revision keep the git source.
        if channel is Channel.STABLE and latest_stable:
            cmd = _build_install_cmd(sys.executable, f"ai-hats=={latest_stable}")
        else:
            cmd = _build_update_cmd(ref=revision)
        # Wrapped in a Rich spinner so the terminal isn't silent while uv
        # downloads (can take 30s+ on slow links).
        run_env = os.environ.copy()
        run_env["PYTHONDONTWRITEBYTECODE"] = "1"
        with console.status(
            "[cyan]Downloading ai-hats from GitHub …[/] [dim](uv install — may take a minute)[/]",
            spinner="dots",
        ):
            result = subprocess.run(cmd, capture_output=True, text=True, env=run_env)
        if result.returncode != 0:
            console.print(f"[red]Update failed[/]: {result.stderr}")
            # HATS-718: legacy in-place install failed → exit non-zero so
            # `self update && self init` stops instead of running init against a
            # half-updated env. (The post-install verify below stays non-fatal:
            # it heals via cli.main() layer A on the next invocation.)
            sys.exit(1)

        # 2b. HATS-213 stage-2 verify. Non-fatal here by HATS-213's choice —
        # NB its stated rationale (layer A heals it next run) covers missing
        # deps only, not the integrity failures HATS-1116 added.
        ok, detail = _run_post_install_verify(sys.executable)
        if not ok:
            console.print(f"[yellow]Post-install verify warned[/]: {detail}")

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

    # HATS-781: legacy in-place path reached on success (incl. the "already up
    # to date" no-op). Drop the stale update-check cache so the next session
    # re-probes instead of nagging with the pre-update delta.
    _invalidate_update_cache(project_dir)

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
                        force=migrate_force,
                        check_branches=check_branches,
                    )
                    cfg = asm.project_config
                    role_name = cfg.active_role or cfg.default_role
                    bump_result = compose_for_role(asm, role_name) if role_name else None
                    asm._refresh(install_time=True, result=bump_result)
                    asm._run_diagnostics()
                    # HATS-549 Phase 3: end-of-bump smoke-assert.
                    # Mirrors do_bump's final step. Failure surfaces
                    # via the outer AssemblyError/OSError except handler
                    # which renders "Bump failed:" — composition diff
                    # below shows no changes.
                    assert_runtime_hooks_resolve(
                        project_dir,
                        backup_path=inproc_backup,
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

    _reverify_layers(project_dir, reports)


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
