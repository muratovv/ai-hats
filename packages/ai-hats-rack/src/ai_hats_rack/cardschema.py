"""Card-field WRITE validation (HATS-1035, ADR-0017 §2).

The read model (:class:`~ai_hats_rack.models.TaskCard`) stays static and
tolerant; the field schema is enforced on writes only — create validates every
field it sets, a transition validates only the fields it touches, reads never
brick. Validators are resolved against an open registry AT COMPOSITION,
fail-closed on an unknown name (never at first use).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Mapping, Sequence

from .definition import BacklogDefinition, load_backlog
from .errors import RackConfigError, RackError
from .models import TaskCard

#: A field validator: called with the value, raises ``ValueError`` on a violation.
Validator = Callable[[Any], None]

_PY_TYPES: dict[str, type] = {"str": str, "int": int, "list": list}
_TYPE_ZERO: dict[str, Any] = {"str": "", "int": 0, "list": [], "any": None}


class FieldValidationError(RackError):
    """A write-strict field violates its declared schema (required/choices/type/
    validator) — a user-facing refusal on create/transition. ``details`` carries
    machine-readable extras (e.g. the allowed choice set)."""

    def __init__(self, field: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        self.field_name = field
        self.details = dict(details or {})
        super().__init__(f"field {field!r}: {message}")


class RequiredFieldError(FieldValidationError):
    """A required field was neither provided nor defaulted on a write."""

    def __init__(self, field: str, message: str | None = None) -> None:
        super().__init__(field, message or "is required")


class ExtrasForbiddenError(RackError):
    """A write targets an undeclared key on a backlog with ``extras: forbid`` — a
    user-facing refusal. Reads stay tolerant; only writes are gated (ADR-0017 §1)."""

    def __init__(self, field: str) -> None:
        self.field_name = field
        super().__init__(
            f"field {field!r} is undeclared and this backlog forbids unknown keys (extras: forbid)"
        )


class UnknownValidatorError(RackConfigError):
    """A field references a validator with no registered factory — fail-closed at
    composition (ADR-0017 §4). Structural invariant → the CLI internal marker."""

    def __init__(self, name: str, field: str, known: Sequence[str]) -> None:
        self.validator = name
        self.field_name = field
        super().__init__(
            f"field {field!r} references validator '{name}' but none is registered; "
            f"registered: {sorted(known)}"
        )


@dataclass(frozen=True)
class ResolvedField:
    """A :class:`~ai_hats_rack.definition.FieldSpec` with its ``validator`` name
    bound to a concrete callable (or ``None``)."""

    name: str
    type: str
    has_default: bool
    default: Any
    required: bool
    choices: tuple[Any, ...] | None
    validator: Validator | None
    emit: str

    def default_value(self) -> Any:
        base = self.default if self.has_default else _TYPE_ZERO.get(self.type)
        return copy.deepcopy(base) if isinstance(base, (list, dict)) else base


class CardSchema:
    """The resolved card-field schema of one backlog: field specs with bound
    validators + the extras policy. The WRITE gate — read tolerance lives in
    :class:`~ai_hats_rack.models.TaskCard`, never here."""

    def __init__(self, fields: Sequence[ResolvedField], *, extras_policy: str = "allow") -> None:
        self._fields = tuple(fields)
        self._by_name = {f.name: f for f in self._fields}
        self.extras_policy = extras_policy

    @property
    def fields(self) -> tuple[ResolvedField, ...]:
        return self._fields

    def declares(self, name: str) -> bool:
        return name in self._by_name

    def writable(self, name: str) -> bool:
        """A write to ``name`` is allowed unless the backlog forbids unknown keys
        AND ``name`` is neither a declared field nor a kernel-owned anchor field
        (a name that would otherwise ride the extras passthrough)."""
        if self.extras_policy != "forbid":
            return True
        return name in self._by_name or name in TaskCard._KNOWN_FIELDS

    def validate(self, name: str, value: Any) -> None:
        """Type/choices/validator check for one declared field; a no-op for an
        undeclared name (an undeclared write is governed by the extras policy,
        not by field validation — read tolerance for a foreign field)."""
        f = self._by_name.get(name)
        if f is None:
            return
        py = _PY_TYPES.get(f.type)
        if py is not None and (not isinstance(value, py) or (py is int and isinstance(value, bool))):
            raise FieldValidationError(name, f"expects {f.type}, got {type(value).__name__}")
        if f.choices is not None and value not in f.choices:
            raise FieldValidationError(
                name,
                f"must be one of {list(f.choices)}; got {value!r}",
                {"choices": list(f.choices), "value": value},
            )
        if f.validator is not None:
            try:
                f.validator(value)
            except ValueError as exc:
                raise FieldValidationError(name, str(exc)) from exc

    def emit_filter(self, mapping: Mapping[str, Any]) -> dict[str, Any]:
        """Persist-time emit gate (ADR-0017 §1): drop a declared field with
        ``emit: when-set`` whose value is empty from the persisted mapping —
        generically, whether the field is a TaskCard column or extras-resident
        on a custom backlog. Anchor/undeclared keys are untouched; the packaged
        tasks schema declares when-set exactly where ``to_dict`` already omits
        (resolution/completed_at/final_state), so this is byte-identical there."""
        out = dict(mapping)
        for f in self._fields:
            if f.emit == "when-set" and f.name in out and not out[f.name]:
                del out[f.name]
        return out

    def resolve_create(self, provided: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve create inputs to concrete values: fill schema defaults for
        None/absent exposed inputs, validate each set value, enforce required.
        Only fields with a concrete value are returned — the rest keep the
        model default (create writes a minimal footprint, ADR-0017 §2)."""
        out: dict[str, Any] = {}
        for f in self._fields:
            given = provided.get(f.name)
            if given is not None:
                value = given
            elif f.required:
                raise RequiredFieldError(f.name)
            elif f.name in provided:  # exposed input left unset → schema default
                value = f.default_value()
            else:
                continue
            self.validate(f.name, value)
            out[f.name] = value
        return out


def build_card_schema(
    defn: BacklogDefinition, validators: Mapping[str, Validator] | None = None
) -> CardSchema:
    """Resolve a definition's ``fields[]`` into a :class:`CardSchema`, binding each
    ``validator:`` name against the open registry — fail-closed on an unknown name
    AT COMPOSITION (ADR-0017 §4), never at first use. The packaged default
    declares no validators, so ``validators=None`` is the zero-config path."""
    registry = validators or {}
    resolved = [
        ResolvedField(
            name=f.name,
            type=f.type,
            has_default=f.has_default,
            default=f.default,
            required=f.required,
            choices=f.choices,
            validator=_bind_validator(f.validator, f.name, registry),
            emit=f.emit,
        )
        for f in defn.fields
    ]
    return CardSchema(resolved, extras_policy=defn.extras_policy)


def _bind_validator(
    name: str | None, field: str, registry: Mapping[str, Validator]
) -> Validator | None:
    if name is None:
        return None
    bound = registry.get(name)
    if bound is None:
        raise UnknownValidatorError(name, field, registry.keys())
    return bound


@lru_cache(maxsize=1)
def default_card_schema() -> CardSchema:
    """The packaged tasks schema — the kernel's zero-config fallback when no
    schema is injected (mirrors ``load_topology``/``load_registry``)."""
    return build_card_schema(load_backlog())
