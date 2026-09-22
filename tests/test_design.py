import copy
import json
import shlex
from html.parser import HTMLParser

import pytest

from perla import design, workflow
from perla.cli import main
from perla.store import PerlaError, Store, digest


def test_incomplete_design_cannot_initialize_and_registration_freezes_it(tmp_path, game):
    result = design.set_decision(tmp_path, game["decision"])
    assert not result["ready_to_initialize"]
    with pytest.raises(PerlaError):
        design.initialize(tmp_path, [])
    assert not (tmp_path / ".perla").exists()
    for criterion in game["criteria"]:
        design.add_criterion(tmp_path, criterion)
    design.board_value(tmp_path, "context", "A public starting situation")
    assert design.show(tmp_path)["ready_to_initialize"]
    design.initialize(tmp_path, [])
    original = design.show(tmp_path)
    with pytest.raises(PerlaError, match="initialized"):
        design.board_value(tmp_path, "context", "Changed")
    with pytest.raises(PerlaError, match="initialized"):
        design.set_decision(tmp_path, game["decision"])
    assert design.show(tmp_path) == original


def test_criterion_conflicts_and_invalid_values_preserve_draft(tmp_path, game):
    design.set_decision(tmp_path, game["decision"])
    original = game["criteria"][0]
    design.add_criterion(tmp_path, original)
    design.add_criterion(tmp_path, original)
    changed = {**original, "threshold": "A deliberately revised threshold"}
    with pytest.raises(PerlaError, match="different content"):
        design.add_criterion(tmp_path, changed)
    assert design.show(tmp_path)["design"]["criteria"] == [original]
    design.add_criterion(tmp_path, changed, replace=True)
    assert design.show(tmp_path)["design"]["criteria"] == [changed]
    design.set_decision(tmp_path, {**game["decision"], "moves": 2})
    with pytest.raises(PerlaError, match="horizon"):
        design.add_criterion(tmp_path, {**original, "deadline_move": 3})
    design.board_value(tmp_path, "period_ends.1", "2027-01-31")
    design.board_value(tmp_path, "samples", 0)
    design.board_value(tmp_path, "wait", None)
    design.board_value(tmp_path, "roster", "Starbucks", append=True)
    design.board_value(tmp_path, "roster", "Starbucks", append=True)
    before = design.show(tmp_path)
    assert before["design"]["board"] == {
        "period_ends": {"1": "2027-01-31"},
        "samples": 0,
        "wait": None,
        "roster": ["Starbucks"],
    }
    for path, entry, append in [
        ("samples.bad", 1, False),
        ("wait", "entry", True),
        ("../bad", 1, False),
    ]:
        with pytest.raises(PerlaError):
            design.board_value(tmp_path, path, entry, append=append)
        assert design.show(tmp_path) == before


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e999", "true", '"50"', "[]"])
def test_non_numbers_rejected(value):
    with pytest.raises((PerlaError, ValueError)):
        design.number(value)


def test_policy_flags_validate_without_overwriting_existing_policy(store, capsys):
    def run(*args):
        exit_code = main(["--project", str(store.root), "adjudicate", "policy", "set", *args])
        result = json.loads(capsys.readouterr().out)
        return exit_code, result

    flags = ["--human-budget", "0", "--auto-confidence", "0.7", "--judgment-share-threshold", "0.4"]
    assert run(*flags)[0] == 0
    before = store.read("project.json")
    for args in [
        [],
        ["--human-budget", "0"],
        [*flags, "--input", "policy.json"],
        [*flags[:-1], "1.5"],
    ]:
        assert run(*args)[0] == 1
        assert store.read("project.json") == before


def test_rendered_tutorial_commands_recreate_complete_case(tmp_path, monkeypatch, capsys):
    pytest.importorskip("pygments")
    from scripts.build_docs import CASE, construction_code, construction_steps, read

    class CodeText(HTMLParser):
        def __init__(self, markup):
            super().__init__()
            self.parts = []
            self.feed(markup)

        def handle_data(self, text):
            self.parts.append(text)

    monkeypatch.chdir(tmp_path)
    # A fresh design must not silently target a selected, unrelated game.
    monkeypatch.setenv("PERLA_PROJECT", str(tmp_path / "unrelated"))
    for step, commands in construction_steps():
        rendered = "".join(CodeText(construction_code(commands)).parts)
        for command in rendered.strip().split("\n\n"):
            args = shlex.split(command.replace("\\\n", ""))
            assert args.pop(0) == "perla"
            # Draft commands and init explicitly use cwd; subsequent commands follow normal selection.
            if step == "initialize" and args[0] == "init":
                monkeypatch.delenv("PERLA_PROJECT")
            exit_code = main(args)
            result = json.loads(capsys.readouterr().out)
            assert exit_code == 0, (args, result)
    store = Store(tmp_path)
    game = read(CASE / "game.json")
    meta = workflow.project(store)
    assert meta["decision"] == game["decision"]
    assert meta["registration_hash"] == digest(
        {"decision": game["decision"], "criteria": game["criteria"]}
    )
    assert store.read("criteria.json") == game["criteria"]
    assert store.read("board/move-0.json") == game["board"]
    assert meta["initial_board_hash"] == digest(game["board"])
    assert meta["escalation_policy"] == read(CASE / "live-policy.json")
    assert design.show(tmp_path)["design"] == game
    for actor in read(CASE / "players.json")["actors"]:
        expected = copy.deepcopy(actor)
        dossier_path = expected.pop("dossier")
        expected["mode"] = "agent"
        assert store.read(f"actors/{actor['id']}.json") == expected
        assert store.read(f"dossiers/{actor['id']}.json") == read(CASE / dossier_path)
    assert result["data"]["phase"] == "move_generation"
    assert result["data"]["missing_dossiers"] == []
