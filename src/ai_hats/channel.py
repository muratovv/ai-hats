"""Harness channel resolver.

A pure mapping ``channel → ChannelResolution`` that collapses the three
scattered source-selection branches in ``self update`` (``--revision`` ref
pinning, editable-detection, master-HEAD probe) into one typed, unit-testable
function.

The resolver is **pure**: every effect — ``git ls-remote`` (edge head sha),
the PyPI version query (stable), and repo precedence/https-coercion — is run by
the caller (see ``cli/maintenance.py``) and injected as ``head_sha`` /
``latest_version`` / ``repo`` / ``path``.
"""

from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from .models import Channel
from .constants import ENV_REPO_URL

# PyPI JSON API for the latest published ai-hats version (stable channel).
PYPI_JSON_URL = "https://pypi.org/pypi/ai-hats/json"

# Last-resort edge target (env > yaml > this). Homed here, not in update_check,
# so edge resolution never depends on that optional module.
FALLBACK_REMOTE_URL = "https://github.com/muratovv/ai-hats.git"


class ChannelResolveError(RuntimeError):
    """An effectful channel fetch failed loud (PyPI unreachable, offline edge).

    Raised instead of falling back to another channel — the caller surfaces a
    clear message and a non-zero exit (no silent fallback).
    """


@dataclass(frozen=True)
class ChannelResolution:
    """Outcome of resolving a harness channel to a concrete install action.

    - ``version_id`` — names ``versions/<version_id>/`` for managed installs
      (edge sha | stable tag/version). ``None`` for local (editable in-place,
      no versioned dir).
    - ``install_spec`` — the uv install target: edge ``ai-hats @ git+https…@<sha>``
      (or a bare local-path repo for the e2e harness), stable ``ai-hats==<ver>``,
      local the editable source path.
    - ``mutable`` — True for moving-target channels (edge/local), False for the
      pinned stable channel. Selects the downgrade guard (git ahead/diverged vs
      semver-monotonic).
    - ``editable`` — True only for local: install with ``uv pip install -e <path>``
      in place, bypassing the versioned blue-green machinery.
    """

    channel: Channel
    version_id: str | None
    install_spec: str
    mutable: bool
    editable: bool


def _edge_install_spec(repo: str, sha: str) -> str:
    """git URL → PEP 508 pinned spec; local-path repo (e2e harness) → bare path.

    pip builds a local working tree directly and does NOT support an ``@ref``
    suffix on local paths (the same reason ``--revision`` refuses them); the
    version dir is named by ``head_sha`` resolved separately.
    """
    return f"ai-hats @ {repo}@{sha}" if "://" in repo else repo


def resolve_channel(
    channel: Channel,
    *,
    repo: str | None = None,
    path: str | None = None,
    head_sha: str | None = None,
    latest_version: str | None = None,
) -> ChannelResolution:
    """Map a harness ``channel`` plus injected facts to a ``ChannelResolution``.

    Pure. The caller resolves effects first (edge ``head_sha`` via git, stable
    ``latest_version`` via PyPI, ``repo`` precedence/coercion) and passes them
    in. Raises ``ValueError`` on a missing required input (a contract
    violation — the effectful layer fails loud with a user message first).
    """
    if channel is Channel.LOCAL:
        return ChannelResolution(
            channel=channel,
            version_id=None,
            install_spec=path or ".",
            mutable=True,
            editable=True,
        )
    if channel is Channel.EDGE:
        if not repo:
            raise ValueError("edge channel requires a repo URL")
        if not head_sha:
            raise ValueError("edge channel requires a resolved head_sha")
        return ChannelResolution(
            channel=channel,
            version_id=head_sha,
            install_spec=_edge_install_spec(repo, head_sha),
            mutable=True,
            editable=False,
        )
    if channel is Channel.STABLE:
        if not latest_version:
            raise ValueError("stable channel requires a resolved latest_version")
        return ChannelResolution(
            channel=channel,
            version_id=latest_version,
            install_spec=f"ai-hats=={latest_version}",
            mutable=False,
            editable=False,
        )
    raise ValueError(f"unhandled channel {channel!r}")  # pragma: no cover


# ---------- source identity (channel: local, and an edge repo that is a path) ----------

AI_HATS_DIST = "ai-hats"
EDITABLE_INSTALL_ORIGIN = "the editable install this process runs from"
_LOCAL_FIX = "ai-hats config set --channel local --path <checkout>"
_EDGE_FIX = "ai-hats config set --channel edge --repo <git-url-or-checkout>"


def source_problem(path: Path) -> str | None:
    """Why ``path`` is not the ai-hats source, or ``None`` when it is.

    Identity, not installability: uv accepts any dir with a ``pyproject.toml``,
    and installing a consumer's own package into the tool venv "succeeds".
    """
    if not path.exists():
        return f"{path} does not exist"
    if not path.is_dir():
        return f"{path} is not a directory"
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        return f"{path} has no pyproject.toml"
    try:
        name = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("name")
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        return f"{path}: its pyproject.toml is unreadable ({exc})"
    if name != AI_HATS_DIST:
        named = f"names {name!r}" if name else "names nothing"
        return f"{path}: its pyproject.toml {named}, not {AI_HATS_DIST}"
    return None


@dataclass(frozen=True)
class EditableSource:
    """An editable ai-hats source this process can vouch for, and what said so."""

    path: str
    origin: str


def detect_editable_source() -> EditableSource | None:
    """The editable ai-hats source this process runs from, or ``None``.

    Launcher-exported ``AI_HATS_INIT_SRC`` wins (robust to venv-bootstrap
    ordering); else the running interpreter's PEP 610 ``file://`` editable url,
    decoded — uv percent-encodes it, and an undecoded path never exists.
    """
    from .constants import ENV_AI_HATS_INIT_SRC

    env_src = (os.environ.get(ENV_AI_HATS_INIT_SRC) or "").strip()
    if env_src:
        return EditableSource(path=env_src, origin=ENV_AI_HATS_INIT_SRC)
    from .cli.maintenance import _is_editable_install  # lazy: avoid maintenance<->channel cycle

    editable, url = _is_editable_install()
    return editable_source_from_url(url) if editable else None


def editable_source_from_url(url: str | None) -> EditableSource | None:
    """The PEP 610 ``file://`` url of an editable install as a filesystem path —
    decoded, because uv percent-encodes it and an undecoded path never exists."""
    if not url or not url.startswith("file://"):
        return None
    return EditableSource(path=url2pathname(urlparse(url).path), origin=EDITABLE_INSTALL_ORIGIN)


@dataclass(frozen=True)
class LocalSource:
    """Where ``channel: local`` installs from, what chose it, and why it cannot."""

    path: Path
    origin: str
    problem: str | None

    @property
    def fix(self) -> str:
        from .constants import ENV_AI_HATS_INIT_SRC

        if self.origin == ENV_AI_HATS_INIT_SRC:
            return f"unset {ENV_AI_HATS_INIT_SRC} or point it at an ai-hats checkout"
        return _LOCAL_FIX


def resolve_local_source(
    project_root: Path, path: str | None, *, detected: EditableSource | None
) -> LocalSource:
    """Resolve ``harness.path`` for ``channel: local``.

    Precedence: an explicit ``path`` (``~`` expanded; relative against the
    project root — the launcher's rule) → ``detected`` (an editable install
    this process already runs from, never to be clobbered) → the project root
    (the ai-hats checkout dogfooding itself). ``problem`` says why the chosen
    dir is not the ai-hats source, so the caller can heal or refuse instead of
    handing the user uv's refusal.
    """
    if path:
        candidate = Path(path).expanduser()
        chosen = candidate if candidate.is_absolute() else project_root / candidate
        origin = "harness.path"
    elif detected:
        chosen = Path(detected.path).expanduser()
        origin = detected.origin
    else:
        chosen = project_root
        origin = "the project root"
    return LocalSource(path=chosen, origin=origin, problem=source_problem(chosen))


@dataclass(frozen=True)
class EdgeSource:
    """Where ``channel: edge`` installs from: a git url the probe validates, or a
    local path judged here — and why the path cannot serve."""

    spec: str
    origin: str
    problem: str | None

    @property
    def fix(self) -> str:
        if self.origin == ENV_REPO_URL:
            return f"unset {ENV_REPO_URL} or point it at an ai-hats checkout"
        return _EDGE_FIX


def resolve_edge_source(yaml_repo: str | None) -> EdgeSource:
    """The edge repo after precedence (env > yaml > upstream), judged when it is
    a path. A ``://`` url is left to ``git ls-remote``: its identity cannot be
    read offline."""
    if os.environ.get(ENV_REPO_URL):
        origin = ENV_REPO_URL
    elif yaml_repo:
        origin = "harness.repo"
    else:
        origin = "the default upstream"
    spec = resolve_edge_repo(yaml_repo)
    if "://" in spec:
        return EdgeSource(spec=spec, origin=origin, problem=None)
    return EdgeSource(spec=spec, origin=origin, problem=source_problem(Path(spec).expanduser()))


# ---------- effectful fetchers (run by the caller, injected into the resolver) ----------


def _coerce_to_https(url: str) -> str:
    """Map a git+ssh URL form to *bare* https so ``git ls-remote`` needs no keys.

    Default is git+https; an ``AI_HATS_REPO_URL`` override may still
    carry ``git+ssh://`` — the probe only needs the bare https.
    Relocated here from ``update_check.checker`` (a channel/install
    primitive) so edge resolution never depends on that optional module.
    """
    prefixes = ("git+ssh://git@", "git+https://", "git+")
    for p in prefixes:
        if url.startswith(p):
            url = url[len(p) :]
            break
    if url.startswith("git@github.com:"):  # ai-hats: allow-secret (ssh git url, not email)
        url = "https://github.com/" + url[len("git@github.com:") :]  # ai-hats: allow-secret
    if url.startswith("github.com/"):
        url = "https://" + url
    return url


def _git_https_repo(raw: str) -> str:
    """Coerce a repo URL to the ``git+https://`` form pip needs for a VCS spec.

    Reuses :func:`_coerce_to_https` (bare https for ``git ls-remote``) and
    re-adds the ``git+`` prefix so the edge install spec
    (``ai-hats @ git+https://…@<sha>``) is a valid pip VCS URL. A local-path repo
    (no scheme — the e2e harness) is left bare.
    """
    https = _coerce_to_https(raw)
    if "://" not in https:
        return https  # local path (e2e harness) — pip builds the working tree
    return https if https.startswith("git+") else "git+" + https


def _raw_edge_repo(yaml_repo: str | None = None) -> str:
    """Edge repo precedence: ``AI_HATS_REPO_URL`` env > yaml ``harness.repo`` >
    :data:`FALLBACK_REMOTE_URL`. Uncoerced — the caller shapes the URL."""
    return os.environ.get(ENV_REPO_URL) or yaml_repo or FALLBACK_REMOTE_URL


def resolve_edge_repo(yaml_repo: str | None = None) -> str:
    """Edge install-spec repo, coerced to ``git+https`` (pip VCS URL)."""
    return _git_https_repo(_raw_edge_repo(yaml_repo))


def resolve_edge_probe_url(yaml_repo: str | None = None) -> str:
    """Bare-https edge URL for the ``git ls-remote`` ahead/behind guard probe.

    Same precedence as :func:`resolve_edge_repo` but coerced to *bare* https (the
    probe needs no ``git+``). Centralises the URL build that ``cli/maintenance``'s
    edge guard used to duplicate.
    """
    return _coerce_to_https(_raw_edge_repo(yaml_repo))


def fetch_edge_head_sha(repo: str) -> str | None:
    """Resolve the edge repo's default-branch HEAD sha via ``git ls-remote``.

    ``None`` on offline / unreachable remote — the caller fails loud (the same
    "could not resolve a target revision" path the legacy install used). Edge
    has no ``branch`` field: it tracks default-branch HEAD, the same call the
    local-source path uses.
    """
    from .cli.maintenance import _resolve_ref  # lazy: avoid maintenance<->channel cycle

    return _resolve_ref(repo, "HEAD")


def fetch_latest_stable_version(url: str = PYPI_JSON_URL, *, timeout: int = 10) -> str:
    """Latest published ai-hats version from the PyPI JSON API (``info.version``).

    Fails LOUD via :class:`ChannelResolveError` when PyPI is unreachable or the
    package is not yet published (404) — NO silent fallback to edge.
    The ``ai-hats`` PyPI name is still free right now; live publish and live
    e2e are tracked separately. Unit-tested here with a stub.
    """
    if not url.startswith("https://"):  # defense: only the pinned https endpoint
        raise ChannelResolveError(f"refusing non-https PyPI URL: {url!r}")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ChannelResolveError(
            f"could not resolve latest stable version from PyPI ({url}): {exc}"
        ) from exc
    version = (payload.get("info") or {}).get("version")
    if not version:
        raise ChannelResolveError(f"PyPI response for ai-hats has no info.version ({url})")
    return version
