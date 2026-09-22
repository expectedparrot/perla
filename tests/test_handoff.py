import json

import pytest

from perla import handoff, workflow
from perla.cli import main
from perla.store import PerlaError, atomic_json


def export(ready, tmp_path):
    folder = tmp_path / "round one" / "moves"
    with ready.transaction():
        jobs = workflow.generate(ready, "moves", move=1)
        batch = handoff.export_batch(ready, jobs, folder)
    return folder, jobs, batch


def result_file(edsl, folder, entry, answer, wrong_context=False):
    job = edsl.Jobs.git.load(folder / entry["package"])
    result = edsl.Result(
        agent=job.agents[0],
        scenario=job.scenarios[0],
        model=edsl.Model("test"),
        iteration=0,
        answer={"moves": answer},
    )
    if wrong_context:
        result.scenario["context_json"] = "{}"
    edsl.Results(data=[result], survey=job.survey).git.save(folder / entry["results"])


def test_cli_named_export_and_import(ready, tmp_path, edsl, move_answer, capsys):
    folder = tmp_path / "export with spaces"
    args = ["--project", str(ready.root)]
    assert main([*args, "job", "generate", "moves", "--move", "1", "--output", str(folder)]) == 0
    batch = json.loads(capsys.readouterr().out)["data"]["export"]
    assert {p.name for p in folder.glob("*.jobs.ep")} == {
        "home.jobs.ep",
        "rival.jobs.ep",
        "startup.jobs.ep",
    }
    for entry in batch["jobs"]:
        assert len(edsl.Jobs.git.load(folder / entry["package"]).models) == 0
        result_file(edsl, folder, entry, move_answer)
    assert main([*args, "ingest", "moves", "--from", str(folder)]) == 0
    assert len(json.loads(capsys.readouterr().out)["data"]["imported"]) == 3
    assert main([*args, "ingest", "moves", "--from", str(folder)]) == 0
    repeated = json.loads(capsys.readouterr().out)["data"]
    assert repeated["imported"] == [] and len(repeated["already_ingested"]) == 3
    with ready.transaction():
        assert workflow.validate(ready)[0]["audited_commits"] == 3
    assert (
        main([*args, "adjudicate", "open", "--move", "1", "--output", str(tmp_path / "control")])
        == 0
    )
    capsys.readouterr()
    assert (tmp_path / "control/control.jobs.ep").is_file()


@pytest.mark.parametrize("failure", ["missing", "invalid", "wrong_context"])
def test_import_rolls_back_whole_batch(ready, tmp_path, edsl, move_answer, failure):
    folder, jobs, batch = export(ready, tmp_path)
    for index, entry in enumerate(batch["jobs"]):
        last = index == len(batch["jobs"]) - 1
        if last and failure == "missing":
            continue
        result_file(
            edsl,
            folder,
            entry,
            {} if last and failure == "invalid" else move_answer,
            wrong_context=last and failure == "wrong_context",
        )
    with pytest.raises((PerlaError, ValueError)), ready.transaction():
        handoff.ingest_batch(ready, "moves", folder)
    assert ready.records("moves/move-1") == []
    assert not any(j["ingested"] for j in workflow.project(ready)["jobs"].values())


def test_export_reuse_preserves_results_and_refuses_conflicts(ready, tmp_path, edsl):
    folder, jobs, batch = export(ready, tmp_path)
    results = folder / "home.results.ep"
    results.write_text("Already downloaded")
    with ready.transaction():
        assert handoff.export_batch(ready, jobs, folder) == batch
    assert results.read_text() == "Already downloaded"
    (folder / "home.jobs.ep").write_text("Changed")
    with pytest.raises(PerlaError, match="changed"), ready.transaction():
        handoff.export_batch(ready, jobs, folder)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(PerlaError, match="different batch"), ready.transaction():
        handoff.export_batch(ready, jobs, occupied)


@pytest.mark.parametrize("tamper", ["project", "kind", "path", "duplicate"])
def test_batch_receipt_cannot_override_registration(ready, tmp_path, edsl, tamper):
    folder, _, _ = export(ready, tmp_path)
    batch = json.loads((folder / "batch.json").read_text())
    if tamper == "project":
        batch["project_id"] = "other-project"
    elif tamper == "duplicate":
        batch["jobs"].append(batch["jobs"][0])
    elif tamper == "kind":
        batch["jobs"][0]["kind"] = "rulings"
    else:
        batch["jobs"][0]["results"] = "../elsewhere.ep"
    # Files exist so validation reaches every entry, including a duplicate.
    for entry in batch["jobs"]:
        (folder / f"{entry['actor_id']}.results.ep").write_text("unused")
    atomic_json(folder / "batch.json", batch)
    with pytest.raises(PerlaError) as error, ready.transaction():
        handoff.ingest_batch(ready, "moves", folder)
    assert error.value.code == "E_LINEAGE"
