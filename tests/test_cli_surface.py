"""The surface rules of metalm cli.md: descriptions, examples, help, parity, boards, exit codes."""

import json
import subprocess
from pathlib import Path

from corpuslm import clikit

from mailerlm import api, cli

ROOT = Path(__file__).resolve().parent.parent


def leaves(parser):
    return [verb for verb, _ in clikit.verb_summaries(parser)]


def leaf_parsers(parser, node=None):
    node = node or parser
    action = clikit.subparsers(node)
    if action is None:
        return [node]
    return [leaf for sub in action.choices.values() for leaf in leaf_parsers(parser, sub)]


def test_every_leaf_has_a_description_and_an_example():
    for leaf in leaf_parsers(cli.build_parser()):
        assert leaf.description and "example:" in (leaf.epilog or ""), leaf.prog


def test_help_lists_every_leaf(capsys):
    assert cli.main(["help"]) == 0
    out = capsys.readouterr().out
    for verb in leaves(cli.build_parser()):
        assert verb in out


def test_every_leaf_resolves_to_an_api_function_or_a_surface_verb():
    surface = {"help", "completion", "serve"}
    for leaf in leaf_parsers(cli.build_parser()):
        name = leaf.get_default("api")
        assert name in surface or callable(getattr(api, name)), leaf.prog


def test_a_usage_error_exits_one(capsys):
    assert cli.main(["setup", "--nope"]) == 1


def test_an_unknown_verb_exits_one(capsys):
    assert cli.main(["help", "nope"]) == 1


def test_a_failing_doctor_exits_one_and_the_json_board_has_next(capsys):
    assert cli.main(["doctor", "--json"]) == 1
    board = json.loads(capsys.readouterr().out)
    assert board["next"] and board["ok"] is False


def test_completion_offers_a_subverb(capsys):
    assert cli.main(["completion", "bash"]) == 0
    assert "connect" in capsys.readouterr().out


def test_the_repo_skill_names_every_leaf():
    skill = (ROOT / ".claude" / "skills" / "mailerlm" / "SKILL.md").read_text()
    for verb in leaves(cli.build_parser()):
        assert f"mailerlm {verb}" in skill, verb


def test_the_console_script_help_runs():
    out = subprocess.run([str(ROOT / ".venv/bin/mailerlm"), "help"], capture_output=True, text=True)
    assert out.returncode == 0 and "setup" in out.stdout
