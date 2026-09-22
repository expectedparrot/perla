from pathlib import Path

import pytest

from perla import actor_bundle, dossiers, workflow
from perla.store import PerlaError, atomic_json, read_json

CASE = Path(__file__).resolve().parents[1] / "docs/case/starbucks-queue"


def test_load_case_and_repeat_without_claiming_review(store):
    with store.transaction():
        result = actor_bundle.load(store, CASE / "players.json")
        assert result["loaded"] == ["home", "dutch", "dunkin", "buyer"]
        assert workflow.status(store)[0]["phase"] == "dossier_review"
    for actor in result["loaded"]:
        assert store.read(f"dossiers/{actor}.json") == read_json(CASE / f"{actor}.json")
    assert {e["origin"] for e in store.records("ingest")} == {"operator"}
    with store.transaction():
        assert actor_bundle.load(store, CASE / "players.json")["already_loaded"] == result["loaded"]
    assert len(store.records("ingest")) == 4


def test_load_preserves_existing_edits(store):
    with store.transaction():
        actor_bundle.load(store, CASE / "players.json")
        dossiers.add_entry(store, "dutch", "constraint", "A new local constraint")
    with pytest.raises(PerlaError, match="different settings"), store.transaction():
        actor_bundle.load(store, CASE / "players.json")
    assert "A new local constraint" in store.read("dossiers/dutch.json")["constraints"]


@pytest.mark.parametrize("fault", ["duplicate", "missing", "escape", "second_home"])
def test_bad_cast_rolls_back_all_actors(store, tmp_path, fault):
    cast = read_json(CASE / "players.json")
    for actor in cast["actors"]:
        atomic_json(tmp_path / actor["dossier"], read_json(CASE / actor["dossier"]))
    if fault == "duplicate":
        cast["actors"][-1]["id"] = "home"
    elif fault == "missing":
        cast["actors"][-1]["dossier"] = "missing.json"
    elif fault == "escape":
        cast["actors"][-1]["dossier"] = "../elsewhere.json"
    else:
        cast["actors"][-1]["role"] = "home"
    atomic_json(tmp_path / "players.json", cast)
    with pytest.raises((PerlaError, OSError)), store.transaction():
        actor_bundle.load(store, tmp_path / "players.json")
    assert store.records("actors") == []
    assert store.records("ingest") == []
