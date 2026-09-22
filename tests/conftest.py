import copy
from pathlib import Path

import pytest

from perla import workflow
from perla.store import Store, read_json

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "marketplace-outcomes"


@pytest.fixture
def game():
    return copy.deepcopy(read_json(EXAMPLE / "game.json"))


@pytest.fixture
def store(tmp_path, game):
    workflow.initialize(tmp_path, game, [])
    return Store(tmp_path)


@pytest.fixture
def ready(store):
    with store.transaction():
        for name in ("home", "rival", "startup"):
            workflow.add_actor(
                store,
                {"id": name, "name": name, "role": "home" if name == "home" else "competitor"},
            )
            workflow.save_dossier(
                store, name, read_json(EXAMPLE / f"{name}.json"), "tester", "Fixture data"
            )
        workflow.approve_dossiers(store, "reviewer", "Reviewed fixture incentives and constraints")
    return store


@pytest.fixture
def edsl():
    return pytest.importorskip("edsl")


@pytest.fixture
def move_answer():
    return {
        "actions": ["Launch a bounded delivery pilot"],
        "rationale": "Test buyer demand",
        "resource_commitments": ["Two teams for eight months"],
        "expected_responses": ["Rival cuts its marketplace fee"],
    }
