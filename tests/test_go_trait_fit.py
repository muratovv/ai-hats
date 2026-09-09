"""`go-trait-fit`: what its script reads out of a `go.mod`, and its wiring.

Fail-under-revert: drop the `go-trait-fit` line from `initial-wizard`'s
composition and the wiring test goes red.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
SKILL_DIR = LIB / "usage/skills/go-trait-fit"
WIZARD_CFG = LIB / "core/roles/initial-wizard/config.yaml"


def _load_script():
    path = SKILL_DIR / "scripts/suggest.py"
    spec = importlib.util.spec_from_file_location("go_trait_fit_suggest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


suggest = _load_script()

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

# A `replace` without a `require` changes nothing in a Go build, so it is not
# evidence either.
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


def _command_line(lines: list[str]) -> str:
    [line] = [ln for ln in lines if "config customize" in ln]
    return line.strip()


def test_direct_requires_reads_both_forms_and_skips_indirect():
    assert suggest.direct_requires(GO_MOD_MIXED) == {
        "github.com/jackc/pgx/v5",
        "github.com/spf13/cobra",
        "github.com/samber/lo",
    }


def test_replace_block_is_not_a_require():
    assert suggest.direct_requires(GO_MOD_BARE) == set()


def test_versioned_module_path_still_matches_its_signal():
    """`github.com/jackc/pgx/v5` must satisfy the bare `github.com/jackc/pgx`."""
    assert "dev::go-database" not in suggest.unsupported_traits(GO_MOD_MIXED)


def test_only_traits_without_direct_evidence_are_suggested():
    assert suggest.unsupported_traits(GO_MOD_MIXED) == ["dev::go-grpc"]


def test_go_mod_without_requires_suggests_every_checked_trait():
    assert suggest.unsupported_traits(GO_MOD_BARE) == list(suggest.GO_TRAIT_SIGNALS)


def test_suggestion_names_only_the_traits_without_evidence():
    line = _command_line(suggest.suggestion_lines(GO_MOD_MIXED, "go-dev"))
    assert line == "ai-hats config customize go-dev --remove-trait dev::go-grpc"


def test_suggestion_is_one_pasteable_line_even_for_every_trait():
    """Four traits overflow an 80-column console; a wrapped line cannot be pasted."""
    flags = " ".join(f"--remove-trait {t}" for t in suggest.GO_TRAIT_SIGNALS)
    assert _command_line(suggest.suggestion_lines(GO_MOD_BARE, "go-dev")) == (
        f"ai-hats config customize go-dev {flags}"
    )


def test_the_role_in_the_command_is_the_one_asked_for():
    """The wizard names the role it just set; the script never assumes it."""
    line = _command_line(suggest.suggestion_lines(GO_MOD_BARE, "go-dev-custom"))
    assert line.startswith("ai-hats config customize go-dev-custom ")


def test_suggestion_offers_no_command_when_every_domain_is_used():
    lines = suggest.suggestion_lines(GO_MOD_ALL, "go-dev")
    assert not [ln for ln in lines if "--remove-trait" in ln]
    assert "nothing to suggest" in lines[0]


def test_missing_go_mod_suggests_nothing(tmp_path, capsys):
    """Acceptance: no go.mod → the full role stands, no questions asked."""
    exit_code = suggest.main(["--go-mod", str(tmp_path / "go.mod")])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "nothing to suggest" in out
    assert "--remove-trait" not in out


def test_wizard_composes_the_skill():
    """Registration is the half the script cannot prove."""
    cfg = yaml.safe_load(WIZARD_CFG.read_text())
    assert "go-trait-fit" in cfg["composition"]["skills"]


def test_wizard_injection_names_no_stack():
    """The Go instruction lives in the skill; the prompt only says "a stack"."""
    injection = yaml.safe_load(WIZARD_CFG.read_text())["injection"]
    step4 = injection.split("### Step 4")[1].split("### Step 5")[0]
    for stack_word in ("go.mod", "go-dev", "pyproject", "Cargo"):
        assert stack_word not in step4, f"Step 4 hardcodes {stack_word!r}"
