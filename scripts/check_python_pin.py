#!/usr/bin/env python3
"""Every copy of the Python pin agrees with `PINNED_PYTHON`, and CI runs it.

HATS-1521: the launcher cannot import the constant (fetched standalone by curl,
must run when the venv is broken), so the value is re-spelled by hand; a partial
bump leaves the rest lying and nothing goes red. A pin outside the CI matrix is
how HATS-1519 shipped to every fresh install.

The site list is explicit on purpose — a repo-wide literal sweep also hits
CHANGELOG, the migration note and the role_baselines fixtures, which do not move.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# The one declaration; every other site is a copy that must agree with it.
PIN_SOURCE = "src/ai_hats/constants.py"
PIN_RE = re.compile(r'^PINNED_PYTHON\s*=\s*"(\d+\.\d+)"', re.M)

# Files that provision an interpreter, in their two spellings.
PROVISION_FILES = ("scripts/ai-hats-launcher", ".github/workflows/release-packages.yml")
PROVISION_GLOB = "tests/e2e/**/*.py"
SHELL_PROVISION_RE = re.compile(r"uv venv --python (\d+\.\d+)")
ARGV_PROVISION_RE = re.compile(r'"--python",\s*"(\d+\.\d+)"')

CI_WORKFLOW = ".github/workflows/ci.yml"
CI_MATRIX_JOB = "test"

RUFF_TARGET_RE = re.compile(r"^py(\d)(\d+)$")
CLASSIFIER_PREFIX = "Programming Language :: Python :: "


@dataclass(frozen=True)
class Violation:
    site: str  # repo-relative path, with :line where a line is meaningful
    found: str
    expected: str
    why: str

    def __str__(self) -> str:
        return f"{self.site}: {self.why} — found {self.found}, expected {self.expected}"


def _as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def read_pin(root: Path) -> str:
    """The canonical pin, or raise — a gate that cannot find it must not pass."""
    text = (root / PIN_SOURCE).read_text()
    match = PIN_RE.search(text)
    if match is None:
        raise LookupError(f'{PIN_SOURCE}: no `PINNED_PYTHON = "X.Y"` declaration found')
    return match.group(1)


def _matches(text: str) -> list[tuple[int, str]]:
    """(1-indexed line, version) for every provisioning spelling in a file."""
    out = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for regex in (SHELL_PROVISION_RE, ARGV_PROVISION_RE):
            out.extend((lineno, m.group(1)) for m in regex.finditer(line))
    return out


def provisioning_violations(root: Path, pin: str) -> list[Violation]:
    """Every `uv venv --python X` — launcher, release verify, e2e venv builds."""
    paths = [root / name for name in PROVISION_FILES]
    paths.extend(sorted(root.glob(PROVISION_GLOB)))
    out = []
    for path in paths:
        if not path.is_file():
            continue
        for lineno, found in _matches(path.read_text()):
            if found != pin:
                out.append(
                    Violation(
                        f"{path.relative_to(root)}:{lineno}",
                        found,
                        pin,
                        "provisions an interpreter other than the pin",
                    )
                )
    return out


def pyproject_paths(root: Path) -> list[Path]:
    """The root pyproject plus every workspace member's (7 at HATS-1521)."""
    return [root / "pyproject.toml", *sorted((root / "packages").rglob("pyproject.toml"))]


def metadata_violations(root: Path, pin: str) -> list[Violation]:
    """`requires-python` floors and `:: Python :: X.Y` classifiers, per pyproject."""
    out = []
    for path in pyproject_paths(root):
        if not path.is_file():
            continue
        site = str(path.relative_to(root))
        project = tomllib.loads(path.read_text()).get("project", {})

        floor = (project.get("requires-python") or "").strip()
        if floor != f">={pin}":
            out.append(
                Violation(
                    f"{site} [requires-python]",
                    floor or "<absent>",
                    f">={pin}",
                    "declared floor disagrees with the pin",
                )
            )

        declared = [
            c.removeprefix(CLASSIFIER_PREFIX)
            for c in project.get("classifiers", [])
            if c.startswith(CLASSIFIER_PREFIX) and c != f"{CLASSIFIER_PREFIX}3"
        ]
        below = [v for v in declared if _as_tuple(v) < _as_tuple(pin)]
        if below:
            out.append(
                Violation(
                    f"{site} [classifiers]",
                    ", ".join(below),
                    f">={pin}",
                    "claims support for a version below the pin",
                )
            )
        elif pin not in declared:
            out.append(
                Violation(
                    f"{site} [classifiers]",
                    ", ".join(declared) or "<none>",
                    pin,
                    "does not claim the version it pins",
                )
            )
    return out


def ruff_target_violations(root: Path, pin: str) -> list[Violation]:
    """ruff's `target-version` decides which syntax it lets through."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    target = (tomllib.loads(path.read_text()).get("tool", {}).get("ruff", {})).get("target-version")
    if target is None:
        return []
    match = RUFF_TARGET_RE.match(target)
    found = f"{match.group(1)}.{match.group(2)}" if match else target
    if found != pin:
        return [
            Violation(
                "pyproject.toml [tool.ruff.target-version]",
                target,
                "py" + pin.replace(".", ""),
                "lints against a different language level than the pin",
            )
        ]
    return []


def ci_matrix(root: Path) -> list[str] | None:
    """The test job's python-version matrix, or None when it cannot be read."""
    path = root / CI_WORKFLOW
    if not path.is_file():
        return None
    jobs = (yaml.safe_load(path.read_text()) or {}).get("jobs") or {}
    matrix = ((jobs.get(CI_MATRIX_JOB) or {}).get("strategy") or {}).get("matrix") or {}
    versions = matrix.get("python-version")
    return [str(v) for v in versions] if versions else None


def matrix_violations(root: Path, pin: str) -> list[Violation]:
    """Invariant 2 — the pin must be a version CI actually runs."""
    versions = ci_matrix(root)
    if versions is None:
        return [
            Violation(
                f"{CI_WORKFLOW} [{CI_MATRIX_JOB}.strategy.matrix]",
                "<unreadable>",
                f"a list containing {pin}",
                "the pin cannot be proven to be tested",
            )
        ]
    if pin not in versions:
        return [
            Violation(
                f"{CI_WORKFLOW} [{CI_MATRIX_JOB}.strategy.matrix]",
                ", ".join(versions),
                f"a list containing {pin}",
                "the pinned interpreter is never exercised by CI",
            )
        ]
    return []


def violations(root: Path, pin: str) -> list[Violation]:
    return [
        *provisioning_violations(root, pin),
        *metadata_violations(root, pin),
        *ruff_target_violations(root, pin),
        *matrix_violations(root, pin),
    ]


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    try:
        pin = read_pin(root)
    except (OSError, LookupError) as exc:
        print(f"[python-pin] FAIL: {exc}", file=sys.stderr)
        return 1
    found = violations(root, pin)
    for v in found:
        print(f"[python-pin] FAIL: {v}", file=sys.stderr)
    if not found:
        print(f"[python-pin] ok: every site agrees on {pin}, and CI runs it", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
