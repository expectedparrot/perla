import json

import pytest

from perla import dossiers, workflow
from perla.cli import main
from perla.store import PerlaError


def test_direct_authoring_builds_valid_dossier(store, capsys):
    def run(*args):
        code = main(["--project", str(store.root), *args])
        response = json.loads(capsys.readouterr().out)
        assert code == 0, response
        return response["data"]

    run("actor", "add", "home", "--name", "Home", "--role", "home")
    result = run(
        "dossier",
        "fact",
        "home",
        "h1",
        "Assumed budget: $30,000.",
        "--source",
        "Game design; not a disclosed financial figure",
    )
    assert result["draft"]
    assert store.read("dossiers/home.json") is None
    draft = run("dossier", "show", "home")
    assert draft["missing"] == list(dossiers.FIELDS.values())
    for kind in dossiers.FIELDS:
        result = run("dossier", kind, "home", f"Specific {kind} from h1")
    assert not result["draft"]
    assert store.read("dossier-drafts/home.json") is None
    assert run("dossier", "show", "home")["facts"][0]["claim"] == "Assumed budget: $30,000."
    events = store.records("ingest")
    assert len(events) == 5
    assert all(e["origin"] == "operator" and e["participant_id"] for e in events)
    assert not workflow.project(store)["approvals"]
    assert not run("dossier", "incentive", "home", "Specific incentive from h1")["changed"]
    assert len(store.records("ingest")) == 5


def test_fact_revision_and_approval_invalidation(ready):
    original = ready.read("dossiers/home.json")["facts"][0]
    with ready.transaction():
        result = dossiers.add_fact(
            ready, "home", original["id"], original["claim"], original["sources"]
        )
        assert not result["changed"]
        assert workflow.is_approved(ready, workflow.project(ready))
    with pytest.raises(PerlaError, match="different claim"), ready.transaction():
        dossiers.add_fact(ready, "home", original["id"], "Revised claim", original["sources"])
    with ready.transaction():
        dossiers.add_fact(
            ready, "home", original["id"], "Revised claim", original["sources"], replace=True
        )
        assert not workflow.is_approved(ready, workflow.project(ready))
    assert ready.read("dossiers/home.json")["facts"][0]["claim"] == "Revised claim"


def test_authoring_respects_setup_freeze(ready, edsl):
    with ready.transaction():
        workflow.generate(ready, "moves", move=1)
    with pytest.raises(PerlaError) as exc, ready.transaction():
        dossiers.add_entry(ready, "home", "constraint", "A late change")
    assert exc.value.code == "E_SETUP_LOCKED"


def test_whole_dossier_import_replaces_unfinished_draft(ready):
    full = ready.read("dossiers/home.json")
    with ready.transaction():
        workflow.add_actor(ready, {"id": "fourth", "name": "Fourth", "role": "competitor"})
        dossiers.add_fact(ready, "fourth", "a1", "Unfinished research", ["Source"])
        workflow.save_dossier(ready, "fourth", full, "tester", "Import a complete dossier")
        assert dossiers.show(ready, "fourth") == full
        assert ready.read("dossier-drafts/fourth.json") is None


@pytest.mark.parametrize(
    "kind,text,sources", [("fact", "Claim", []), ("fact", " ", ["Source"]), ("incentive", " ", [])]
)
def test_empty_inputs_do_not_write(ready, kind, text, sources):
    before = ready.read("dossiers/home.json")
    with pytest.raises(ValueError), ready.transaction():
        if kind == "fact":
            dossiers.add_fact(ready, "home", "new", text, sources)
        else:
            dossiers.add_entry(ready, "home", kind, text)
    assert ready.read("dossiers/home.json") == before
