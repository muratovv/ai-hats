"""What a session IS, carried to the processes it spawns (HATS-1594).

ADR-0019 D9 says a session composes exactly one role and runs exactly one
provider — the session is the key. Until now nothing carried that: four gate
call sites re-derived it from ``ai-hats.yaml``, a file that provably does not
hold it, because ``--role`` / ``-p`` are not persisted when they override
(``composition_seam._maybe_sync_active_role``). A gate then judged a different
role, or a different provider's skill mirror, than the session it guards.

Three of the four consumers are processes ai-hats does not invoke — ``git``
runs ``.githooks/<event>``, the agent runs ``rack transition``, the operator
runs ``ai-hats wt merge``. Their argv is not ours, so the environment is the
only channel across that boundary. In-process callers take the object itself.

**Membership rule.** A field belongs here only if a consumer must be TOLD it,
because it cannot derive it and cannot correctly re-derive it: the composition
inputs (``role``, ``provider``) and the anchors that address everything else
(``id``, ``project_dir``, ``session_dir``, ``skills_root``). Everything else
stays where it is — on disk when it is large, growing or auditable; a named
scalar when it is a resource handle with an existing addressed consumer.
Notably absent, and deliberately: the composition itself. HATS-1540 retired the
channel's frozen binding list so a session predating a binding still resolves
it; freezing it here would reinstate what that task removed.
"""  # comment-length: allow — the membership rule is the contract

from __future__ import annotations

import json
import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

from .constants import ENV_ROLE
from ai_hats_observe.trace import ENV_SESSION_ID

#: The envelope. One key, so the identity cannot be torn: a consumer has all of
#: it or none. The scalars beside it are projections from the same serializer,
#: kept because shell reads them and making bash parse JSON would regress.
ENV_SESSION_IDENTITY = "AI_HATS_SESSION_IDENTITY"

#: Bumped only when a key is removed, retyped, or changes meaning. Adding an
#: optional key does NOT bump it — readers ignore what they do not know — so a
#: mismatch always means something a reader relies on moved under it.
IDENTITY_VERSION = 1

#: Every key ``to_env`` writes, so the identity can be removed the way it is
#: written — as a unit. Pinned against ``to_env`` by the contract guard.
IDENTITY_ENV_KEYS = (ENV_SESSION_IDENTITY, ENV_SESSION_ID, ENV_ROLE)


def drop_identity(environ: MutableMapping[str, str]) -> list[str]:
    """Remove the session identity from ``environ``; return the keys removed.

    Removing a SUBSET tears it: the envelope without its scalars, or scalars
    without the envelope, is a session that half-exists, and ``from_env`` refuses
    that rather than reading it as absence. Three call sites each hand-rolled
    their own key list and each got a different subset, so the list lives here
    with the writer instead (HATS-1613 review).
    """
    return [key for key in IDENTITY_ENV_KEYS if environ.pop(key, None) is not None]


def identity_for_project(
    project_dir: Path, environ: dict[str, str] | None = None
) -> SessionIdentity | None:
    """The session that governs ``project_dir``, or ``None`` when none does.

    A session of ANOTHER project is not a degraded session here — for this
    project it is no session at all, and outside one the config is the answer
    (HATS-1594). Hence ``None`` and not a refusal: it restores this project's
    own gates rather than blocking the operator who ran the command.

    Foreignness is judged by the envelope's own ``project_dir``, never by the
    scalar pin — without an envelope there is no identity to scope, which is why
    the pin-keyed ``githooks_run._drop_foreign_pin`` walks past a bare one. Both
    sides must already have taken the worktree-hop: a pin naming the main
    checkout while the caller stands in a linked worktree is designed, and
    reading THAT as foreign is what ``test_pin_in_linked_worktree`` forbids.
    """  # comment-length: allow — why None rather than a refusal is the contract
    identity = SessionIdentity.from_env(environ)
    if identity is None:
        return None
    here = project_dir.expanduser().resolve()
    return identity if identity.project_dir.expanduser().resolve() == here else None


class SessionIdentityError(Exception):
    """The envelope is present but cannot be trusted — never a skip.

    Its own type rather than ``CheckResolutionError``: this module is a leaf the
    check channel imports, not the other way round. Consumers wrap it in
    whatever refusal their own surface speaks.
    """


@dataclass(frozen=True)
class SessionIdentity:
    """The session, as the processes it spawns must be told it."""

    id: str
    role: str
    provider: str
    project_dir: Path
    session_dir: Path
    #: Where the surface mirrored this session's skills — the root a bound check
    #: runs its bytes from. Empty when it mirrors none: a refusal decided once at
    #: launch, not re-resolved through the provider registry on every firing.
    skills_root: str = ""

    def to_env(self) -> dict[str, str]:
        """The envelope plus its scalar projections — one writer, so no drift."""
        return {
            ENV_SESSION_IDENTITY: json.dumps(
                {
                    "v": IDENTITY_VERSION,
                    "id": self.id,
                    "role": self.role,
                    "provider": self.provider,
                    "project_dir": str(self.project_dir),
                    "session_dir": str(self.session_dir),
                    "skills_root": self.skills_root,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            ENV_SESSION_ID: self.id,
            # Nothing in this repo reads it — it is published for shells and
            # user hooks, so "unused" here is not evidence it is dead.
            ENV_ROLE: self.role,
        }

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> SessionIdentity | None:
        """The identity this process was launched under, or ``None`` outside one.

        ``None`` means exactly one thing — no session — and that is a legitimate
        state (an operator running ``rack transition`` in a plain terminal), not
        a degraded one. Every other trouble raises: an envelope that is present
        and unreadable must never read as absence, because absence resolves
        against the config and the config is what this type exists to stop
        being asked.
        """
        env = os.environ if environ is None else environ
        raw = env.get(ENV_SESSION_IDENTITY, "")
        if not raw:
            if env.get(ENV_SESSION_ID):
                raise SessionIdentityError(
                    f"session {env[ENV_SESSION_ID]!r} carries no {ENV_SESSION_IDENTITY} — it was "
                    f"launched by an ai-hats too old to say what it is, so which role and "
                    f"provider a gate belongs to cannot be known. Restart the session"
                )
            return None
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise SessionIdentityError(
                f"{ENV_SESSION_IDENTITY} is not readable JSON ({exc})"
            ) from exc
        if not isinstance(data, dict):
            raise SessionIdentityError(
                f"{ENV_SESSION_IDENTITY} holds {type(data).__name__}, not an object"
            )
        cls._check_version(data)
        try:
            return cls(
                id=cls._required(data, "id"),
                role=cls._required(data, "role"),
                provider=cls._required(data, "provider"),
                project_dir=Path(cls._required(data, "project_dir")),
                session_dir=Path(cls._required(data, "session_dir")),
                # Unknown keys are ignored on purpose — that is what lets a key
                # be added without bumping the version.
                skills_root=str(data.get("skills_root", "")),
            )
        except SessionIdentityError:
            raise
        except Exception as exc:
            raise SessionIdentityError(f"{ENV_SESSION_IDENTITY} is malformed: {exc}") from exc

    @staticmethod
    def _check_version(data: dict) -> None:
        version = data.get("v")
        if version == IDENTITY_VERSION:
            return
        if not isinstance(version, int):
            raise SessionIdentityError(
                f"{ENV_SESSION_IDENTITY} carries no integer 'v' — its version cannot be told, "
                f"so no field in it can be trusted"
            )
        # Named in both directions: which side is behind decides what to do.
        moved = "newer than" if version > IDENTITY_VERSION else "older than"
        raise SessionIdentityError(
            f"{ENV_SESSION_IDENTITY} speaks v{version}, {moved} the v{IDENTITY_VERSION} this "
            f"ai-hats knows — the session and this build disagree about what the identity "
            f"means. Run 'ai-hats self update' and restart the session"
        )

    @staticmethod
    def _required(data: dict, key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise SessionIdentityError(f"{ENV_SESSION_IDENTITY} carries no non-empty {key!r}")
        return value


__all__ = [
    "ENV_SESSION_IDENTITY",
    "IDENTITY_ENV_KEYS",
    "IDENTITY_VERSION",
    "SessionIdentity",
    "SessionIdentityError",
    "drop_identity",
    "identity_for_project",
]
