"""Parser and dataclass for runtime role spec expressions (HATS-1456)."""

from __future__ import annotations

from dataclasses import dataclass


class RoleSpecError(ValueError):
    """Raised when a role specification string cannot be parsed."""


@dataclass(frozen=True)
class RoleSpec:
    role: str
    adds: tuple[str, ...]
    removes: tuple[str, ...]
    raw: str


def parse_role_spec(raw: str) -> RoleSpec:
    if not raw or not raw.strip():
        raise RoleSpecError("role spec is empty")

    tokens = raw.replace("+", " + ").split()
    first = tokens[0]
    if first in ("+", "-"):
        raise RoleSpecError(f"role spec must start with a role name, got '{first}'")

    role = first
    adds: list[str] = []
    removes: list[str] = []
    seen: set[str] = set()

    i = 1
    while i < len(tokens):
        op = tokens[i]
        if op not in ("+", "-"):
            raise RoleSpecError(f"expected '+' or '-' before '{op}'")

        if i + 1 >= len(tokens):
            raise RoleSpecError(f"role spec ends with a dangling '{op}'")

        next_token = tokens[i + 1]
        if next_token in ("+", "-"):
            raise RoleSpecError(f"expected a component name after '{op}', got '{next_token}'")

        operand = next_token
        if operand in seen:
            raise RoleSpecError(f"duplicate operand '{operand}' in role spec")

        seen.add(operand)
        if op == "+":
            adds.append(operand)
        else:
            removes.append(operand)

        i += 2

    return RoleSpec(
        role=role,
        adds=tuple(adds),
        removes=tuple(removes),
        raw=raw,
    )


def format_role_spec(role: str, adds: tuple[str, ...] = (), removes: tuple[str, ...] = ()) -> str:
    """The canonical expression for a composed role, readable back by the parser.

    Every operator is spaced because only ``+`` is padded before the split, so
    ``judge -foo`` would parse as a dangling operand (HATS-1594).
    """
    parts = [role]
    parts.extend(f"+ {name}" for name in adds)
    parts.extend(f"- {name}" for name in removes)
    return " ".join(parts)


def has_operators(raw: str) -> bool:
    if not raw or not raw.strip():
        return False
    tokens = raw.replace("+", " + ").split()
    return any(t in ("+", "-") for t in tokens)
