"""HATS-764 — harness channel resolver.

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

from dataclasses import dataclass

from .models import Channel


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
