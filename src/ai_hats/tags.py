"""Session-level custom tags (HATS-163).

Tags are arbitrary ``k=v`` metadata attached to a session at launch time by the
caller/orchestrator (alert fingerprint, client id, pipeline run, experiment
label). They land in ``metrics.json`` under the ``tags`` key and are queryable
via ``ai-hats session list --tag k=v``.

Validation is strict by design — orchestrators should sanitize upstream. Better
to fail loudly than to silently drop or truncate tags that a downstream query
depends on.
"""

from __future__ import annotations

import re
from typing import Iterable

# Metrics-dict keys that ai-hats owns. Users cannot shadow them via tags —
# otherwise ``metrics.get("role")`` vs ``metrics["tags"]["role"]`` creates a
# confusing two-source-of-truth situation.
RESERVED_TAG_KEYS: frozenset[str] = frozenset({
    "role",
    "provider",
    "exit_code",
    "model",
    "timed_out",
    "error",
    "isolation_mode",
    "turns",
    "tokens",
    "models",
    "tool_calls",
    "session_id",
    "session_dir",
    "started_at",
})

TAG_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.\-]*$")
MAX_TAG_KEY_LEN = 64
MAX_TAG_VALUE_LEN = 256
MAX_TAGS_PER_SESSION = 20


class TagValidationError(ValueError):
    """Raised when a tag violates format, length, reserved-key, or count limits."""


def _validate_pair(key: str, value: str, *, allow_reserved: bool) -> None:
    """Validate a single k=v pair. Raises TagValidationError on any violation."""
    if not key:
        raise TagValidationError("tag key must not be empty")
    if len(key) > MAX_TAG_KEY_LEN:
        raise TagValidationError(
            f"tag key {key!r} exceeds {MAX_TAG_KEY_LEN} chars (got {len(key)})"
        )
    if not TAG_KEY_RE.match(key):
        raise TagValidationError(
            f"tag key {key!r} must match {TAG_KEY_RE.pattern} "
            "(alphanumeric, underscore, dash, dot; start with letter or underscore)"
        )
    if not allow_reserved and key in RESERVED_TAG_KEYS:
        raise TagValidationError(
            f"tag key {key!r} is reserved (shadows built-in metrics field); "
            f"reserved: {sorted(RESERVED_TAG_KEYS)}"
        )
    if not value:
        raise TagValidationError(f"tag value for key {key!r} must not be empty")
    if len(value) > MAX_TAG_VALUE_LEN:
        raise TagValidationError(
            f"tag value for {key!r} exceeds {MAX_TAG_VALUE_LEN} chars "
            f"(got {len(value)})"
        )


def _parse_pairs(raw: Iterable[str]) -> list[tuple[str, str]]:
    """Split each ``k=v`` string on the first ``=``. Raises if no separator."""
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if "=" not in item:
            raise TagValidationError(
                f"tag {item!r} missing '=' separator (expected k=v)"
            )
        key, value = item.split("=", 1)
        pairs.append((key, value))
    return pairs


def parse_tags(raw: Iterable[str]) -> dict[str, str]:
    """Parse ``--tag k=v`` repeats into a validated tag dict.

    Strict: raises :class:`TagValidationError` on any malformed pair, reserved
    key shadow, length overflow, or if the total count exceeds
    :data:`MAX_TAGS_PER_SESSION`. Duplicate keys raise.
    """
    pairs = _parse_pairs(raw)
    if len(pairs) > MAX_TAGS_PER_SESSION:
        raise TagValidationError(
            f"too many tags: {len(pairs)} > max {MAX_TAGS_PER_SESSION}"
        )
    out: dict[str, str] = {}
    for key, value in pairs:
        _validate_pair(key, value, allow_reserved=False)
        if key in out:
            raise TagValidationError(f"duplicate tag key {key!r}")
        out[key] = value
    return out


def parse_tag_filters(raw: Iterable[str]) -> dict[str, str]:
    """Parse ``session list --tag k=v`` filters.

    Same syntax and validation as :func:`parse_tags`. Reserved keys are still
    rejected — filtering on ``role`` goes through the dedicated ``--role`` flag;
    other reserved fields are not meant to be addressed via custom tags.
    """
    return parse_tags(raw)
