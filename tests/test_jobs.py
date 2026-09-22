import json

import pytest

from perla import edsl_adapter, workflow
from perla.store import PerlaError, atomic_json, digest


def generate_moves(store):
    with store.transaction():
        return workflow.generate(store, "moves", move=1)["jobs"]


def human_file(store, job, answer, filename="answer.json"):
    manifest = store.read(f"jobs/{job['job_id']}.manifest.json")
    payload = {
        "project_id": manifest["project_id"],
        "job_id": manifest["id"],
        "actor_id": manifest["actor_id"],
        "kind": manifest["kind"],
        "move": manifest["move"],
        "participant_id": "human-tester",
        "answer": answer,
    }
    path = store.root / filename
    atomic_json(path, payload)
    return path


def test_actual_packages_are_isolated_model_free_and_repeatable(ready, edsl):
    jobs = generate_moves(ready)
    assert len(jobs) == 3
    for item in jobs:
        manifest = ready.read(f"jobs/{item['job_id']}.manifest.json")
        assert set(manifest["context"]) == {"decision", "dossier", "board"}
        package = edsl.Jobs.git.load(item["package"])
        assert len(package.models) == 0
        assert len(package.agents) == len(package.scenarios) == 1
        assert json.loads(package.scenarios[0]["context_json"]) == manifest["context"]
        other_facts = [a for a in ("home", "rival", "startup") if a != item["actor_id"]]
        context = json.dumps(package.to_dict())
        for name in other_facts:
            assert ready.read(f"dossiers/{name}.json")["facts"][0]["claim"] not in context
        assert "kill_criterion" not in context
    with ready.transaction():
        repeated = workflow.generate(ready, "moves", move=1)["jobs"]
        assert all(j["reused"] for j in repeated)
        assert {j["job_id"] for j in jobs} == {j["job_id"] for j in repeated}
        assert workflow.validate(ready)[0] == {
            "valid": True,
            "audited_jobs": 3,
            "audited_commits": 0,
        }


def test_approval_freeze_and_round_gate(ready, edsl):
    with ready.transaction():
        workflow.set_mode(ready, "home", "human")
        with pytest.raises(PerlaError) as error:
            workflow.generate(ready, "moves", move=1)
        assert error.value.code == "E_APPROVAL"
        workflow.approve_dossiers(ready, "tester", "Reviewed mode change")
    generate_moves(ready)
    with ready.transaction():
        for operation in (
            lambda: workflow.set_mode(ready, "rival", "human"),
            lambda: workflow.add_actor(ready, {"id": "late", "name": "Late", "role": "wildcard"}),
            lambda: workflow.save_dossier(
                ready, "home", ready.read("dossiers/home.json"), "tester", "Late edit"
            ),
        ):
            with pytest.raises(PerlaError) as error:
                operation()
            assert error.value.code == "E_SETUP_LOCKED"
        with pytest.raises(PerlaError) as error:
            workflow.generate(ready, "moves", move=2)
        assert error.value.code == "E_OPEN_RULINGS"


def test_move_commitment_and_tamper_detection(ready, edsl, move_answer):
    jobs = generate_moves(ready)
    for job in jobs:
        path = human_file(ready, job, move_answer)
        with ready.transaction():
            result = workflow.ingest(ready, "moves", job["job_id"], path, human=True)
            assert result["content_hash"] == digest(move_answer)
            assert result["provenance"]["origin"] == "human"
            assert "content" not in result
            with pytest.raises(PerlaError) as error:
                workflow.ingest(ready, "moves", job["job_id"], path, human=True)
            assert error.value.code == "E_COMMITTED"
    with ready.transaction():
        data, actions = workflow.status(ready)
        assert data["phase"] == "ready_for_adjudication"
        assert actions[0]["argv"] == ["perla", "adjudicate", "open", "--move", "1"]
        assert workflow.validate(ready)[0]["audited_commits"] == 3
    record = ready.read("moves/move-1/home.json")
    record["content"]["rationale"] = "Retconned after observing a rival"
    atomic_json(ready.path("moves/move-1/home.json"), record)
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.validate(ready)
    assert error.value.code == "E_COMMITMENT"


def test_human_lineage_and_empty_answers_rejected(ready, edsl, move_answer):
    jobs = generate_moves(ready)
    path = human_file(ready, jobs[0], move_answer)
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(ready, "moves", jobs[1]["job_id"], path, human=True)
    assert error.value.code == "E_LINEAGE"
    assert ready.records("moves/move-1") == []


def test_actual_package_tampering_detected(ready, edsl):
    job = generate_moves(ready)[0]
    payload = edsl.Jobs.git.load(job["package"])
    payload.scenarios[0]["private_competitor_rationale"] = "Leaked private plans"
    payload.git.save(job["package"])
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.validate(ready)
    assert error.value.code == "E_ISOLATION"


def test_manifest_tampering_detected(ready, edsl):
    job = generate_moves(ready)[0]
    path = ready.path(f"jobs/{job['job_id']}.manifest.json")
    manifest = ready.read(f"jobs/{job['job_id']}.manifest.json")
    manifest["context"]["private_moves"] = ["Hidden competitor action"]
    atomic_json(path, manifest)
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.validate(ready)
    assert error.value.code == "E_ISOLATION"


def test_failed_export_does_not_freeze_setup(ready, monkeypatch):
    def broken(*args):
        raise OSError("Disk full")

    monkeypatch.setattr(edsl_adapter, "save_job", broken)
    with pytest.raises(OSError), ready.transaction():
        workflow.generate(ready, "moves", move=1)
    with ready.transaction():
        assert workflow.project(ready)["jobs"] == {}
        workflow.set_mode(ready, "home", "human")


def test_dossier_job_preserves_supplied_facts(ready, edsl):
    facts = ready.read("dossiers/home.json")["facts"]
    with ready.transaction():
        item = workflow.generate(ready, "dossiers", "home", facts={"facts": facts})["jobs"][0]
    answer = {
        k: ["Revised grounded account citing h1"]
        for k in ("incentives", "constraints", "capabilities", "red_lines")
    }
    path = human_file(ready, item, answer)
    with ready.transaction():
        workflow.ingest(ready, "dossiers", item["job_id"], path, human=True)
        assert ready.read("dossiers/home.json")["facts"] == facts
        assert workflow.status(ready)[0]["phase"] == "dossier_review"


def test_actual_edsl_results_ingest_without_inference(ready, edsl, move_answer):
    jobs = generate_moves(ready)
    for job in jobs:
        package = edsl.Jobs.git.load(job["package"])
        result = edsl.Result(
            agent=package.agents[0],
            scenario=package.scenarios[0],
            model=edsl.Model("test"),
            iteration=0,
            answer={"moves": move_answer},
        )
        results = edsl.Results(data=[result], survey=package.survey)
        path = ready.root / f"{job['actor_id']}.results.ep"
        results.git.save(path)
        with ready.transaction():
            data = workflow.ingest(ready, "moves", job["job_id"], path)
            assert data["provenance"]["origin"] == "simulation"
            assert data["provenance"]["model"] == "test"
    with ready.transaction():
        _, warnings = workflow.validate(ready)
        assert warnings[0]["code"] == "W_SAME_MODEL"
        with pytest.raises(PerlaError):
            workflow.validate(ready, strict=True)


def test_result_context_and_multiple_samples_rejected(ready, edsl, move_answer):
    job = generate_moves(ready)[0]
    package = edsl.Jobs.git.load(job["package"])
    result = edsl.Result(
        agent=package.agents[0],
        scenario=package.scenarios[0],
        model=edsl.Model("test"),
        iteration=0,
        answer={"moves": move_answer},
    )
    path = ready.root / "multiple.results.ep"
    edsl.Results(data=[result, result], survey=package.survey).git.save(path)
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(ready, "moves", job["job_id"], path)
    assert error.value.code == "E_RESULTS"
    result.scenario["context_json"] = "{}"
    wrong = ready.root / "wrong.results.ep"
    edsl.Results(data=[result], survey=package.survey).git.save(wrong)
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(ready, "moves", job["job_id"], wrong)
    assert error.value.code == "E_LINEAGE"


def test_all_actor_inputs_freeze_at_first_export(ready, edsl):
    with ready.transaction():
        workflow.generate(ready, "moves", actor_id="home", move=1)
    rival = ready.read("dossiers/rival.json")
    rival["constraints"].append("An unapproved constraint added on disk")
    atomic_json(ready.path("dossiers/rival.json"), rival)
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.generate(ready, "moves", actor_id="rival", move=1)
    assert error.value.code == "E_SETUP_LOCKED"


def test_deleted_commitment_fails_audit(ready, edsl, move_answer):
    job = generate_moves(ready)[0]
    path = human_file(ready, job, move_answer)
    with ready.transaction():
        workflow.ingest(ready, "moves", job["job_id"], path, human=True)
    ready.path(f"moves/move-1/{job['actor_id']}.json").unlink()
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.validate(ready)
    assert error.value.code == "E_COMMITMENT"


def test_human_mode_refuses_simulation(ready, edsl):
    with ready.transaction():
        workflow.set_mode(ready, "home", "human")
        workflow.approve_dossiers(ready, "tester", "Reviewed human mode")
    job = next(j for j in generate_moves(ready) if j["actor_id"] == "home")
    with ready.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(ready, "moves", job["job_id"], ready.root / "unused.ep")
    assert error.value.code == "E_PROVENANCE"


def test_cli_end_to_end(tmp_path, capsys, edsl, move_answer):
    from pathlib import Path

    from perla.cli import main
    from perla.store import Store

    example = Path(__file__).resolve().parents[1] / "examples/marketplace-outcomes"
    root = tmp_path / "cli-game"

    def run(*args):
        code = main(["--project", str(root), *args])
        envelope = json.loads(capsys.readouterr().out)
        assert code == 0, envelope
        assert envelope["ok"], envelope
        return envelope

    run("init", "--input", str(example / "game.json"), "--delegate", "dossiers")
    for name in ("home", "rival", "startup"):
        run(
            "actor",
            "add",
            name,
            "--name",
            name,
            "--role",
            "home" if name == "home" else "competitor",
        )
        run(
            "dossier",
            "edit",
            name,
            "--input",
            str(example / f"{name}.json"),
            "--by",
            "scripted-test",
            "--note",
            "Fictional test fixture",
        )
    generated = run("job", "generate", "moves", "--move", "1")
    assert generated["command"] == ["job", "generate", "moves"]
    store = Store(root)
    for job in generated["data"]["jobs"]:
        source = human_file(store, job, move_answer)
        response = run(
            "ingest", "moves", "--job", job["job_id"], "--source", str(source), "--human-input"
        )
        assert response["command"] == ["ingest", "moves"]
    assert run("agent-start")["data"]["phase"] == "ready_for_adjudication"
    assert run("workflow", "validate")["data"]["audited_commits"] == 3
