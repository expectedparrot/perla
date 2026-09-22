import json

import pytest
from pydantic import ValidationError

from perla import workflow
from perla.cli import main
from perla.store import PerlaError, Store, atomic_json


def test_init_validates_before_writing(tmp_path, game):
    game["decision"]["statement"] = "Win in AI"
    with pytest.raises(PerlaError, match="Decision|statement") as error:
        workflow.initialize(tmp_path, game, [])
    assert error.value.code == "E_VAGUE_DECISION"
    assert not (tmp_path / ".perla").exists()


def test_duplicate_and_late_criteria_rejected(tmp_path, game):
    game["criteria"][1]["id"] = game["criteria"][0]["id"]
    with pytest.raises(PerlaError, match="unique"):
        workflow.initialize(tmp_path, game, [])
    game["criteria"][1]["id"] = "margin"
    game["decision"]["moves"] = 1
    with pytest.raises(PerlaError, match="deadline"):
        workflow.initialize(tmp_path, game, [])


def test_registration_integrity(store):
    criteria = store.read("criteria.json")
    criteria[0]["threshold"] = "A friendlier threshold"
    atomic_json(store.path("criteria.json"), criteria)
    with store.transaction(), pytest.raises(PerlaError) as error:
        workflow.status(store)
    assert error.value.code == "E_CRITERIA_LOCKED"


def test_setup_and_review_are_content_bound(ready):
    with ready.transaction():
        assert workflow.status(ready)[0]["phase"] == "move_generation"
        updated = ready.read("dossiers/home.json")
        updated["constraints"] = ["Only ten teams now available"]
        workflow.save_dossier(ready, "home", updated, "operator", "Corrected capacity")
        assert workflow.status(ready)[0]["phase"] == "dossier_review"
        edits = ready.records("ingest")
        assert len(edits) == 4
        assert any(e["before"] is not None for e in edits)


def test_actor_mode_invalidates_approval(ready):
    with ready.transaction():
        workflow.set_mode(ready, "home", "human")
        assert not workflow.is_approved(ready, workflow.project(ready))


def test_mirror_imaging_is_blocked(ready):
    with ready.transaction():
        workflow.save_dossier(
            ready, "rival", ready.read("dossiers/home.json"), "tester", "Mirror fixture"
        )
        with pytest.raises(PerlaError) as error:
            workflow.approve_dossiers(ready, "reviewer", "Review")
        assert error.value.code == "E_MIRROR_IMAGING"


def test_sourceless_dossier_and_traversal_rejected(store):
    with store.transaction():
        with pytest.raises(ValidationError):
            workflow.add_actor(store, {"id": "../../escape", "name": "Bad", "role": "home"})
        workflow.add_actor(store, {"id": "home", "name": "Home", "role": "home"})
        with pytest.raises(ValidationError):
            workflow.save_dossier(store, "home", {"facts": []}, "tester", "Invalid")


def test_rollback_and_journal_recovery(store):
    with pytest.raises(RuntimeError), store.transaction():
        store.write("actors/uncommitted.json", {"id": "uncommitted"})
        raise RuntimeError("Abort")
    assert not store.path("actors/uncommitted.json").exists()
    atomic_json(store.path(".transaction.json"), {"recovered.json": {"ok": True}})
    with store.transaction():
        assert store.read("recovered.json") == {"ok": True}
    assert not store.path(".transaction.json").exists()


def test_discovery_from_subdirectory_and_reinit(store, game):
    nested = store.root / "nested"
    nested.mkdir()
    assert Store.discover(nested).root == store.root
    with pytest.raises(PerlaError) as error:
        workflow.initialize(store.root, game, [])
    assert error.value.code == "E_EXISTS"


@pytest.mark.parametrize(
    "arguments,ok",
    [
        (["--help"], True),
        (["--version"], True),
        (["wat"], False),
        (["init"], False),
        (["actor", "add", "home", "--role", "invalid"], False),
        (["agent-start"], True),
        (["status"], False),
    ],
)
def test_cli_envelopes(tmp_path, capsys, arguments, ok):
    code = main(["--project", str(tmp_path), *arguments])
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "1.0"
    assert result["ok"] is ok
    assert code == (0 if ok else 1)
    assert ("data" in result) is ok
    if not ok:
        assert set(result["error"]) == {"code", "message", "remediation"}


def test_invalid_json_envelope(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"board": NaN}')
    assert main(["--project", str(tmp_path), "init", "--input", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_INVALID_JSON"
