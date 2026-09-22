import json

import pytest

from perla import selection, workflow
from perla.cli import main
from perla.store import PerlaError


def test_selected_project_is_used_by_cli_and_children(tmp_path, game, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "a game with spaces"
    workflow.initialize(target, game, [])
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("PERLA_PROJECT", raising=False)
    assert main(["project", "set", str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["project"] == str(target)
    child = workspace / "notes"
    child.mkdir()
    monkeypatch.chdir(child)
    assert main(["actor", "add", "home", "--name", "Selected home", "--role", "home"]) == 0
    capsys.readouterr()
    assert (target / ".perla/actors/home.json").is_file()
    assert main(["project", "show"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["selected_by"] == str(
        workspace / selection.FILENAME
    )


def test_explicit_environment_and_local_project_precedence(tmp_path, game, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    projects = [tmp_path / name for name in ["selected", "environment", "explicit"]]
    for p in projects:
        workflow.initialize(p, game, [])
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("PERLA_PROJECT", raising=False)
    selection.select(projects[0])
    monkeypatch.setenv("PERLA_PROJECT", str(projects[1]))
    assert selection.resolve()[0] == projects[1]
    assert selection.resolve(projects[2])[0] == projects[2]
    monkeypatch.delenv("PERLA_PROJECT")
    local = workspace / "local"
    workflow.initialize(local, game, [])
    assert selection.resolve(start=local)[0] == local


def test_invalid_set_does_not_replace_selection_and_stale_pointer_fails(
    tmp_path, game, monkeypatch
):
    target = tmp_path / "game"
    workflow.initialize(target, game, [])
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERLA_PROJECT", raising=False)
    selection.select(target)
    pointer = tmp_path / selection.FILENAME
    original = pointer.read_bytes()
    with pytest.raises(PerlaError):
        selection.select(tmp_path / "missing")
    assert pointer.read_bytes() == original
    target.rename(tmp_path / "moved")
    with pytest.raises(PerlaError) as error:
        selection.resolve()
    assert error.value.code == "E_PROJECT_SELECTION"
    assert selection.unset()["removed"]
    assert selection.resolve()[0] == tmp_path
