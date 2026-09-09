"""`go.mod` evidence for the four `dev::go-*` traits `suggest-traits` checks."""

from __future__ import annotations

from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.go_traits import (
    GO_TRAIT_SIGNALS,
    direct_requires,
    suggestion_lines,
    unsupported_traits,
)

# pgx and cobra are direct; grpc is only indirect, so it is not evidence.
GO_MOD_MIXED = """\
module github.com/acme/svc

go 1.22

require (
\tgithub.com/jackc/pgx/v5 v5.5.0
\tgithub.com/spf13/cobra v1.8.0
\tgoogle.golang.org/grpc v1.60.0 // indirect
)

require github.com/samber/lo v1.39.0
"""

# A `replace` without a `require` changes nothing in a Go build, so it is
# not evidence either.
GO_MOD_BARE = """\
module github.com/acme/tiny

go 1.22

replace (
\tgithub.com/samber/lo => ../lo
)
"""

GO_MOD_ALL = """\
module github.com/acme/everything

go 1.22

require (
\tgorm.io/gorm v1.25.0
\tconnectrpc.com/connect v1.16.0
\tgithub.com/urfave/cli/v2 v2.27.0
\tgithub.com/samber/oops v1.13.0
)
"""


def test_direct_requires_reads_both_forms_and_skips_indirect():
    assert direct_requires(GO_MOD_MIXED) == {
        "github.com/jackc/pgx/v5",
        "github.com/spf13/cobra",
        "github.com/samber/lo",
    }


def test_replace_block_is_not_a_require():
    assert direct_requires(GO_MOD_BARE) == set()


def test_versioned_module_path_still_matches_its_signal():
    """`github.com/jackc/pgx/v5` must satisfy the bare `github.com/jackc/pgx`."""
    assert "dev::go-database" not in unsupported_traits(GO_MOD_MIXED)


def test_only_traits_without_direct_evidence_are_suggested():
    assert unsupported_traits(GO_MOD_MIXED) == ["dev::go-grpc"]


def test_go_mod_without_requires_suggests_every_checked_trait():
    assert unsupported_traits(GO_MOD_BARE) == list(GO_TRAIT_SIGNALS)


def test_go_mod_covering_every_domain_suggests_nothing():
    assert unsupported_traits(GO_MOD_ALL) == []


def _command_line(lines: list[str]) -> str:
    [line] = [ln for ln in lines if "config customize" in ln]
    return line.strip()


def test_suggestion_names_only_the_traits_without_evidence():
    line = _command_line(suggestion_lines(GO_MOD_MIXED))
    assert line == "ai-hats config customize go-dev --remove-trait dev::go-grpc"


def test_suggestion_is_one_pasteable_line_even_for_every_trait():
    """Four traits overflow an 80-column console; a wrapped line cannot be pasted."""
    flags = " ".join(f"--remove-trait {t}" for t in GO_TRAIT_SIGNALS)
    assert _command_line(suggestion_lines(GO_MOD_BARE)) == (
        f"ai-hats config customize go-dev {flags}"
    )


def test_suggestion_offers_no_command_when_every_domain_is_used():
    lines = suggestion_lines(GO_MOD_ALL)
    assert not [ln for ln in lines if "--remove-trait" in ln]
    assert "nothing to suggest" in lines[0]


def test_suggest_traits_is_wired_under_config():
    """Registration is the half the pure renderer cannot prove."""
    result = CliRunner().invoke(main, ["config", "--help"])
    assert result.exit_code == 0, result.output
    assert "suggest-traits" in result.output
