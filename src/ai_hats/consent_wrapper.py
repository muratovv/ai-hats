"""Session command middleware for role-declared consent operations."""

from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ai_hats_library.hooks.consent_gate import Operation, Outcome, Verdict
from ai_hats_library.hooks.consent_gate.issue import DEFAULT_WINDOW_MINUTES


REFUSED = 2
_TICKET_ENV = "AI_HATS_CONSENT_TICKET"
_GLOBAL_ACK = "AI_HATS_CONSENT_ACK"
CONFIG_ENV = "AI_HATS_CONSENT_WRAPPER_CONFIG"
_SURFACES = {"rack.transition": "rack", "wt.merge": "ai-hats"}
_ARROW = "->"
_STATE_CHARS = frozenset(string.ascii_letters + string.digits + "_.-")


class ConsentPolicyError(ValueError):
    """A role declared consent middleware the session cannot enforce."""


def _transition_selector_reason(selector: str) -> str | None:
    """Validate consent_gate's rack.transition command matcher."""
    if any(character.isspace() for character in selector):
        return "a selector carries no whitespace"
    if _ARROW not in selector:
        return "a rack transition selector is an arrow"
    if selector.count(_ARROW) != 1:
        return f"a selector carries exactly one {_ARROW!r}"
    source, _, target = selector.partition(_ARROW)
    if not source and not target:
        return "both halves empty"
    if "NONE" in (source, target):
        return "'NONE' is reserved by HATS-1703"
    if target in ("", "ANY"):
        return "a wide OUTPUT is reserved by HATS-1720"
    if source == "ANY":
        return f"write '{_ARROW}{target}' instead of 'ANY->{target}'"
    for end in (source, target):
        stray = {character for character in end if character not in _STATE_CHARS}
        if stray:
            return "a selector half is not a state name"
        if end.endswith("-"):
            return "a selector half ends in '-'"
    return None


@dataclass(frozen=True)
class WrapperConfig:
    project_dir: Path
    originals: Mapping[str, str]
    policy: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class MatchedOperation:
    operation: Operation
    ticket_subject: str | None
    legacy_flags: tuple[str, ...]


def policy_from(points: Sequence[object]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for point in points:
        if getattr(point, "app", "") != "consent_gate":
            raise ConsentPolicyError(
                f"{getattr(point, 'declared_by', '<unknown>')!r}: consent may only be "
                "declared under apps.consent_gate"
            )
        path = tuple(getattr(point, "path", ()))
        selector = str(getattr(point, "selector", ""))
        declared_by = str(getattr(point, "declared_by", "<unknown>"))
        if len(path) != 1:
            raise ConsentPolicyError(
                f"{declared_by!r}: apps.consent_gate requires exactly one operation key"
            )
        operation = str(path[0])
        if operation not in _SURFACES:
            raise ConsentPolicyError(f"unsupported consent operation {operation!r}")
        if operation == "rack.transition":
            reason = _transition_selector_reason(selector)
            if reason is not None:
                raise ConsentPolicyError(
                    f"{declared_by!r}: invalid rack.transition selector {selector!r} — {reason}"
                )
        elif selector != "pre-merge":
            raise ConsentPolicyError(
                f"{declared_by!r}: wt.merge supports only 'pre-merge', got {selector!r}"
            )
        selectors = grouped.setdefault(operation, [])
        if selector not in selectors:
            selectors.append(selector)
    return {operation: tuple(selectors) for operation, selectors in grouped.items()}


def _transition_target(argv: Sequence[str]) -> tuple[str, str] | None:
    if len(argv) < 3 or argv[0] != "transition":
        return None
    task_id = argv[1]
    for index, argument in enumerate(argv[2:], 2):
        if argument == "--state" and index + 1 < len(argv):
            return task_id, argv[index + 1]
        if argument.startswith("--state="):
            return task_id, argument.split("=", 1)[1]
    state = argv[2]
    return None if state.startswith("-") else (task_id, state)


def _protected_targets(selectors: Sequence[str]) -> set[str]:
    return {selector.split("->", 1)[1] for selector in selectors if "->" in selector}


def match_operation(
    surface: str, argv: Sequence[str], policy: Mapping[str, tuple[str, ...]]
) -> MatchedOperation | None:
    if surface == "rack" and "rack.transition" in policy:
        transition = _transition_target(argv)
        if transition is None:
            return None
        task_id, target = transition
        if target not in _protected_targets(policy["rack.transition"]):
            return None
        flags = (_GLOBAL_ACK, "AI_HATS_PLAN_ACK") if target == "execute" else (_GLOBAL_ACK,)
        return MatchedOperation(
            operation=Operation("rack.transition", subject=task_id, label=f"{task_id}: → {target}"),
            ticket_subject=task_id,
            legacy_flags=flags,
        )
    if (
        surface == "ai-hats"
        and "wt.merge" in policy
        and "pre-merge" in policy["wt.merge"]
        and tuple(argv[:2]) == ("wt", "merge")
    ):
        branch = argv[2] if len(argv) > 2 and not argv[2].startswith("-") else "this worktree"
        return MatchedOperation(
            operation=Operation("wt.merge", subject=branch, label=f"merge {branch}"),
            ticket_subject=None,
            legacy_flags=(_GLOBAL_ACK, "AI_HATS_MERGE_ACK"),
        )
    return None


def _spawn(command: list[str], environ: Mapping[str, str]) -> int:
    # The executable was resolved before the session PATH was wrapped.
    return subprocess.run(command, env=dict(environ), check=False).returncode  # noqa: S603


# One spelling of the wrapper's layout. The predicate below recognises what
# `materialize_consent_wrappers` writes, so the two must never drift: a rename
# on one side alone silently disarms BOTH recursion barriers (HATS-1809).
_WRAPPER_DIR_NAME = "consent-wrapper"
_WRAPPER_BIN_NAME = "bin"


def _is_consent_wrapper_path(path: str | Path) -> bool:
    resolved = Path(path).resolve()
    # the bin dir itself, or an executable sitting in it
    return any(
        candidate.name == _WRAPPER_BIN_NAME and candidate.parent.name == _WRAPPER_DIR_NAME
        for candidate in (resolved, resolved.parent)
    )


def _original_lookup_path(search_path: str) -> str:
    return os.pathsep.join(
        entry for entry in search_path.split(os.pathsep) if not _is_consent_wrapper_path(entry)
    )


def _record_grant(answer: Verdict, operation: Operation, project_dir: Path) -> bool:
    from .consent_port import record_use

    return record_use(answer, operation, hook="consent_wrapper.py", project_dir=project_dir)


def _record_legacy(flag: str, operation: Operation, project_dir: Path) -> bool:
    from ai_hats_library.hooks.bypass_journal import journal_bypass

    return journal_bypass(
        "hatch",
        f"{flag} ({operation.type})",
        hook="consent_wrapper.py",
        cmd=" ".join(sys.argv),
        cwd=project_dir,
    )


def run_wrapped(
    surface: str,
    argv: Sequence[str],
    config: WrapperConfig,
    *,
    environ: Mapping[str, str] | None = None,
    check_grant: Callable[[Operation, Path], Verdict],
    peek_ticket: Callable[[str | None, Sequence[str]], bool],
    consume_ticket: Callable[[str | None, Sequence[str]], bool],
    record_grant: Callable[[Verdict, Operation, Path], bool] = _record_grant,
    record_legacy: Callable[[str, Operation, Path], bool] = _record_legacy,
    spawn: Callable[[list[str], Mapping[str, str]], int] = _spawn,
) -> int:
    env = dict(os.environ if environ is None else environ)
    original = config.originals[surface]
    if _is_consent_wrapper_path(original):
        print(
            f"consent: refusing recursive wrapper target for {surface!r}: {original}",
            file=sys.stderr,
        )
        return REFUSED
    matched = match_operation(surface, argv, config.policy)
    if matched is None:
        return spawn([original, *argv], env)

    answer = check_grant(matched.operation, config.project_dir)
    ticket = False
    legacy_flag = next((flag for flag in matched.legacy_flags if env.get(flag) == "1"), None)
    if answer.outcome is not Outcome.GRANTED:
        if legacy_flag is None:
            ticket = peek_ticket(matched.ticket_subject, argv)
            if not ticket:
                detail = answer.reason or "no live authorization covers this operation"
                print(
                    f"consent: requires supervisor approval; {detail}. Human: run "
                    f"`consent {matched.operation.type} {DEFAULT_WINDOW_MINUTES}` in this session",
                    file=sys.stderr,
                )
                return REFUSED

    child_env = dict(env)
    for flag in (*matched.legacy_flags, _TICKET_ENV):
        child_env.pop(flag, None)
    if ticket and not consume_ticket(matched.ticket_subject, argv):
        print("consent: authorization ticket could not be consumed", file=sys.stderr)
        return REFUSED
    recorded = True
    if answer.outcome is Outcome.GRANTED:
        recorded = record_grant(answer, matched.operation, config.project_dir)
    elif legacy_flag is not None:
        recorded = record_legacy(legacy_flag, matched.operation, config.project_dir)
    if not recorded:
        print("consent: authorization use could not be recorded", file=sys.stderr)
        return REFUSED
    return spawn([original, *argv], child_env)


def _wrapper_script(surface: str) -> str:
    return (
        f"#!{sys.executable}\n"
        "from ai_hats.consent_wrapper import main\n"
        f"raise SystemExit(main({surface!r}))\n"
    )


def _consent_script() -> str:
    return (
        f"#!{sys.executable}\n"
        "from ai_hats_library.hooks.consent_gate.cli import main\n"
        "raise SystemExit(main())\n"
    )


def materialize_consent_wrappers(
    project_dir: Path,
    result,
    session_id: str,
    provider,
    artifacts,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[..., str | None] = shutil.which,
) -> None:
    """Put role-declared command middleware first on this HITL session's PATH."""
    policy = policy_from(result.consent)
    if not policy:
        return
    unknown = sorted(set(policy) - set(_SURFACES))
    if unknown:
        raise RuntimeError(f"unsupported consent operations: {', '.join(unknown)}")
    if not provider.supports_session_command_wrappers():
        raise RuntimeError(
            f"provider {provider.name!r} cannot enforce role-declared command consent"
        )

    from .paths import session_cache_dir

    env = os.environ if environ is None else environ
    effective_path = artifacts.extra_env.get("PATH", env.get("PATH", ""))
    lookup_path = _original_lookup_path(effective_path)
    surfaces = sorted({_SURFACES[operation] for operation in policy})
    originals: dict[str, str] = {}
    for surface in surfaces:
        original = which(surface, path=lookup_path)
        if not original:
            raise RuntimeError(f"cannot wrap {surface!r}: executable not found on PATH")
        if _is_consent_wrapper_path(original):
            raise RuntimeError(
                f"cannot wrap {surface!r}: resolved executable is a consent wrapper: {original}"
            )
        originals[surface] = original

    root = session_cache_dir(project_dir, session_id) / _WRAPPER_DIR_NAME
    config_path = root / "config.json"
    artifacts.port.write_text(
        config_path,
        json.dumps(
            {
                "project_dir": str(project_dir.resolve()),
                "originals": originals,
                "policy": {key: list(value) for key, value in policy.items()},
            },
            indent=2,
        )
        + "\n",
    )
    bin_dir = root / _WRAPPER_BIN_NAME
    consent = bin_dir / "consent"
    artifacts.port.write_executable(consent, _consent_script())
    artifacts.materialized.append(consent)
    for surface in surfaces:
        wrapper = bin_dir / surface
        artifacts.port.write_executable(wrapper, _wrapper_script(surface))
        artifacts.materialized.append(wrapper)
    artifacts.materialized.append(config_path)
    artifacts.extra_env["PATH"] = os.pathsep.join(filter(None, (str(bin_dir), effective_path)))
    artifacts.extra_env[CONFIG_ENV] = str(config_path)


def load_config(path: Path) -> WrapperConfig:
    """Load the strict, session-frozen wrapper configuration."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("consent wrapper config must be an object")
    project_dir = data.get("project_dir")
    originals = data.get("originals")
    policy = data.get("policy")
    if not isinstance(project_dir, str) or not project_dir:
        raise ValueError("consent wrapper config has no project_dir")
    if not isinstance(originals, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in originals.items()
    ):
        raise ValueError("consent wrapper config has invalid originals")
    if not isinstance(policy, dict) or not all(
        isinstance(key, str)
        and isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        for key, value in policy.items()
    ):
        raise ValueError("consent wrapper config has invalid policy")
    return WrapperConfig(
        project_dir=Path(project_dir),
        originals=dict(originals),
        policy={key: tuple(value) for key, value in policy.items()},
    )


def main(surface: str) -> int:
    """Run one materialized command wrapper."""
    config_path = os.environ.get(CONFIG_ENV)
    if not config_path:
        print(f"consent: {CONFIG_ENV} is missing", file=sys.stderr)
        return REFUSED
    try:
        config = load_config(Path(config_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"consent: wrapper configuration is unreadable: {exc}", file=sys.stderr)
        return REFUSED

    from ai_hats_library.hooks import consent_ticket

    from .consent_port import verdict

    argv = sys.argv[1:]
    return run_wrapped(
        surface,
        argv,
        config,
        check_grant=lambda operation, target: verdict(operation, target_dir=target),
        peek_ticket=lambda subject, command: consent_ticket.peek(
            subject, start=config.project_dir, argv=command
        ),
        consume_ticket=lambda subject, command: consent_ticket.consume(
            subject, start=config.project_dir, argv=command
        ),
    )


__all__ = [
    "CONFIG_ENV",
    "ConsentPolicyError",
    "MatchedOperation",
    "WrapperConfig",
    "load_config",
    "main",
    "match_operation",
    "materialize_consent_wrappers",
    "policy_from",
    "run_wrapped",
]
