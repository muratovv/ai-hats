#!/usr/bin/env python3
"""Which `dev::go-*` traits a project's `go.mod` shows no evidence for.

The Go role composes every Go domain, so the default is never under-equipped.
The cost of that default is per-project noise, and `go.mod` is the one
machine-readable statement of which domains a repository actually touches.

Stdlib only and read-only: no ai-hats import, and it prints the `customize`
command rather than running it. The human decides.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Prefix-matched against direct module paths, so a `/v5` suffix still hits.
# Deliberately generous: a missed signal only means a trait is kept, while a
# missing entry would tell the user to drop a domain they do use.
GO_TRAIT_SIGNALS: dict[str, tuple[str, ...]] = {
    "dev::go-database": (
        "github.com/jackc/pgx",
        "github.com/jackc/pgconn",
        "github.com/jmoiron/sqlx",
        "gorm.io/gorm",
        "entgo.io/ent",
        "github.com/lib/pq",
        "github.com/go-sql-driver/mysql",
        "github.com/mattn/go-sqlite3",
        "github.com/golang-migrate/migrate",
        "github.com/pressly/goose",
    ),
    "dev::go-grpc": (
        "google.golang.org/grpc",
        "google.golang.org/protobuf",
        "github.com/grpc-ecosystem/go-grpc-middleware",
        "connectrpc.com/connect",
    ),
    "dev::go-cli": (
        "github.com/spf13/cobra",
        "github.com/spf13/viper",
        "github.com/urfave/cli",
        "github.com/alecthomas/kong",
    ),
    "dev::go-samber": (
        "github.com/samber/lo",
        "github.com/samber/mo",
        "github.com/samber/oops",
        "github.com/samber/hot",
        "github.com/samber/ro",
    ),
}


def direct_requires(go_mod: str) -> set[str]:
    """Module paths a `go.mod` requires directly.

    `// indirect` lines are excluded: a transitive dependency on grpc says
    nothing about whether this repository writes gRPC services.
    """
    deps: set[str] = set()
    in_block = False
    for raw in go_mod.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if in_block:
            if line.startswith(")"):
                in_block = False
                continue
        elif line.startswith("require ("):
            in_block = True
            continue
        elif line.startswith("require "):
            line = line[len("require ") :].strip()
        else:
            continue
        if "// indirect" in line:
            continue
        parts = line.split()
        if parts:
            deps.add(parts[0])
    return deps


def _matches(dep: str, signal: str) -> bool:
    return dep == signal or dep.startswith(signal + "/")


def unsupported_traits(go_mod: str) -> list[str]:
    """The `GO_TRAIT_SIGNALS` traits with no direct-dependency evidence, ordered."""
    deps = direct_requires(go_mod)
    return [
        trait
        for trait, signals in GO_TRAIT_SIGNALS.items()
        if not any(_matches(dep, sig) for dep in deps for sig in signals)
    ]


def suggestion_lines(go_mod: str, role: str) -> list[str]:
    """What to print for a `go.mod`'s contents, as lines.

    Pure, so the wording and the one-line shape of the `customize` command are
    testable without a project on disk to stand in.
    """
    unsupported = unsupported_traits(go_mod)
    if not unsupported:
        return ["Every checked Go trait has a direct dependency here — nothing to suggest."]

    lines = ["", "No direct dependency in go.mod for:"]
    for trait in unsupported:
        # Last path segment is the name a Go developer actually says out loud.
        names = ", ".join(sig.rsplit("/", 1)[-1] for sig in GO_TRAIT_SIGNALS[trait])
        lines.append(f"  {trait} — no {names}")
    flags = " ".join(f"--remove-trait {t}" for t in unsupported)
    return lines + [
        "",
        "Drop them from this project only:",
        "",
        f"  ai-hats config customize {role} {flags}",
        "",
        "Undo any of them with the matching --add-trait.",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", default="go-dev", help="role the customize line targets")
    parser.add_argument("--go-mod", default="go.mod", type=Path, help="path to the module file")
    args = parser.parse_args(argv)

    if not args.go_mod.is_file():
        print(f"No {args.go_mod} here — nothing to suggest.")
        print(f"`{args.role}` composes every Go domain; that is the intended default.")
        return 0

    print(f"go.mod: {args.go_mod}")
    for line in suggestion_lines(args.go_mod.read_text(), args.role):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
