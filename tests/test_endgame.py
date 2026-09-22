import copy
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from perla import adjudication, endgame, reporting, rounds, workflow
from perla.cli import main
from perla.store import PerlaError, Store, atomic_json, read_json


def submit(store, job, answer):
    from edsl import Jobs, Model, Result, Results

    package = Jobs.git.load(job["package"])
    kind = package.scenarios[0]["kind"]
    package.survey.questions[0]._validate_answer({"answer": answer})
    result = Result(
        agent=package.agents[0],
        scenario=package.scenarios[0],
        model=Model("test"),
        iteration=0,
        answer={kind: answer},
    )
    source = store.root / f"{job['job_id']}.results.ep"
    Results(data=[result], survey=package.survey).git.save(source)
    with store.transaction():
        return workflow.ingest(store, kind, job["job_id"], source)


@pytest.fixture(scope="module")
def completed_template(tmp_path_factory):
    pytest.importorskip("edsl")
    example = Path(__file__).resolve().parents[1] / "examples/marketplace-outcomes"
    root = tmp_path_factory.mktemp("endgame-template")
    game = read_json(example / "game.json")
    game["decision"]["moves"] = 1
    for criterion in game["criteria"]:
        criterion["deadline_move"] = 1
    workflow.initialize(root, game, ["dossiers", "rulings"])
    store = Store(root)
    with store.transaction():
        for actor_id in ("home", "rival", "startup"):
            workflow.add_actor(
                store,
                {
                    "id": actor_id,
                    "name": actor_id,
                    "role": "home" if actor_id == "home" else "competitor",
                },
            )
            workflow.save_dossier(
                store,
                actor_id,
                read_json(example / f"{actor_id}.json"),
                "fixture",
                "Fictional assumptions",
            )
        adjudication.add_testimony(store, read_json(example / "testimony.json"), "fixture")
        rounds.set_policy(store, {"human_budget": 0})
        jobs = workflow.generate(store, "moves", move=1)["jobs"]
    for job in jobs:
        submit(
            store,
            job,
            {
                "actions": ["Launch a fictional pilot"],
                "rationale": "Exercise the workflow",
                "resource_commitments": ["A fictional team"],
                "expected_responses": ["Rivals respond"],
            },
        )
    with store.transaction():
        job = adjudication.open_round(store, 1)
    answer = read_json(example / "rulings.json")
    answer["rulings"][0]["branches"] = {
        "uncertainty": "Whether exclusivity spreads",
        "played": "limited",
        "options": [
            {
                "id": "limited",
                "description": "Limited exclusivity",
                "probability": 0.6,
                "board_delta": {"home_qualified_teams": 18},
            },
            {
                "id": "severe",
                "description": "Widespread exclusivity",
                "probability": 0.4,
                "board_delta": {"home_qualified_teams": 10},
            },
        ],
    }
    submit(store, job, answer)
    with store.transaction():
        rounds.route_round(store, 1)
        jobs = rounds.generate_rebuttals(store, 1)["jobs"]
    for job in jobs:
        submit(
            store,
            job,
            {
                "rebuttals": [
                    {
                        "ruling_id": "r001",
                        "stance": "accept",
                        "argument": "Fixture acceptance",
                        "link": None,
                        "cite": [],
                    }
                ]
            },
        )
    with store.transaction():
        job = rounds.generate_resolution(store, 1)
    submit(
        store,
        job,
        {
            "resolutions": [
                {
                    "ruling_id": "r001",
                    "decision": "affirm",
                    "reason": "All responses considered",
                    "responses": [
                        {"actor_id": actor_id, "reason": "Acceptance recorded"}
                        for actor_id in ("home", "rival", "startup")
                    ],
                    "revision": None,
                }
            ]
        },
    )
    with store.transaction():
        rounds.close_round(store, 1)
    return root


@pytest.fixture
def completed(completed_template, tmp_path):
    root = tmp_path / "game"
    shutil.copytree(completed_template, root)
    return Store(root)


@pytest.fixture
def assessment(completed):
    context = endgame.game_context(completed, workflow.project(completed))
    return {
        "evaluations": [
            {
                "criterion_id": c["id"],
                "verdict": "survived",
                "rationale": "Scripted structural validation example",
                "ruling_refs": ["ruling:1:r001"],
                "board_refs": ["board:1"],
            }
            for c in context["criteria"]
        ],
        "insights": [
            {
                "id": "capacity",
                "text": "Concurrent moves put pressure on scarce delivery capacity",
                "ruling_refs": ["ruling:1:r001"],
                "owner": "Strategy team",
                "due_date": "2027-02-28",
                "baseline_matches": [],
            }
        ],
        "predictions": [
            {
                "source": source,
                "due_date": "2027-12-31",
                "resolution_criteria": "Check the named action or outcome against dated documentary evidence",
                "probability": None,
            }
            for source in [*context["moves"], *context["rulings"]]
        ],
    }


def stage_assessment(store, answer):
    with store.transaction():
        job = endgame.generate(store, "endgame")
    submit(store, job, answer)
    return job


def test_open_rounds_block_evaluation_and_reports(ready):
    with ready.transaction(), pytest.raises(PerlaError) as error:
        endgame.generate(ready, "endgame")
    assert error.value.code == "E_OPEN_RULINGS"
    with ready.transaction(), pytest.raises(PerlaError) as error:
        reporting.export_context(ready)
    assert error.value.code == "E_UNEVALUATED_CRITERIA"


def test_actual_baseline_is_inputs_only_and_evaluation_maps_novelty(completed, assessment):
    from edsl import Jobs

    with completed.transaction():
        baseline_job = endgame.generate(completed, "novelty")
    context = json.loads(Jobs.git.load(baseline_job["package"]).scenarios[0]["context_json"])
    assert set(context) == {"decision", "dossiers", "board"}
    assert context["board"]["home_qualified_teams"] == 20
    with completed.transaction(), pytest.raises(PerlaError) as error:
        endgame.generate(completed, "endgame")
    assert error.value.code == "E_BASELINE_PENDING"
    submit(
        completed,
        baseline_job,
        {"findings": [{"id": "capacity_pressure", "text": "Limited teams constrain expansion"}]},
    )
    assessment["insights"][0]["baseline_matches"] = ["capacity_pressure"]
    stage_assessment(completed, assessment)
    with completed.transaction():
        final = endgame.finalize(completed)
        assert completed.read("insights.json")[0]["novelty"] == "derivable"
        assert "W_NO_EMERGENT_INSIGHTS" in {w["code"] for w in final["health"]["warnings"]}
        assert workflow.status(completed)[0]["phase"] == "complete"
        workflow.validate(completed)


def test_endgame_compiles_sources_and_reports_deterministically(completed, assessment):
    stage_assessment(completed, assessment)
    with completed.transaction():
        final = endgame.finalize(completed)
        assert endgame.finalize(completed) == final
        registry = completed.read("predictions.json")
        assert len(registry) == 4
        assert {p["population"] for p in registry} == {"team_moves", "agent_rulings"}
        assert all(p["source_provenance"]["origin"] == "simulation" for p in registry)
        assert all(p["probability"] is None for p in registry)
        assert completed.read("insights.json")[0]["novelty"] == "unassessed"
        first, warnings = reporting.export_context(completed)
        html_result, _ = reporting.export_html(completed)
        export, _ = reporting.export_uncertainties(completed)
    context_bytes = Path(first["path"]).read_bytes()
    html_bytes = Path(html_result["path"]).read_bytes()
    assert {w["code"] for w in warnings} >= {
        "W_HOME_COMFORT",
        "E_JUDGMENT_SHARE",
        "W_ESCALATION_DEBT",
        "W_NOVELTY_UNASSESSED",
        "W_CONTROL_MODEL_OVERLAP",
    }
    assert read_json(export["path"])["uncertainties"][0]["played"] == "limited"
    with completed.transaction():
        reporting.export_context(completed)
        reporting.export_html(completed)
    assert Path(first["path"]).read_bytes() == context_bytes
    assert Path(html_result["path"]).read_bytes() == html_bytes


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing_criterion", "E_UNEVALUATED_CRITERIA"),
        ("unknown_ruling", "E_CITATION"),
        ("missing_prediction", "E_PREDICTIONS"),
        ("duplicate_prediction", "E_ENDGAME"),
        ("unknown_baseline", "E_NOVELTY"),
        ("missing_deadline_board", "E_CITATION"),
    ],
)
def test_invalid_assessments_leave_no_state(completed, assessment, change, code):
    if change == "missing_criterion":
        assessment["evaluations"].pop()
    elif change == "unknown_ruling":
        assessment["evaluations"][0]["ruling_refs"] = ["ruling:1:unknown"]
    elif change == "missing_prediction":
        assessment["predictions"].pop()
    elif change == "duplicate_prediction":
        assessment["predictions"].append(assessment["predictions"][0])
    elif change == "unknown_baseline":
        assessment["insights"][0]["baseline_matches"] = ["invented"]
    else:
        assessment["evaluations"][0]["board_refs"] = ["board:0"]
    with pytest.raises(PerlaError) as error:
        stage_assessment(completed, assessment)
    assert error.value.code == code
    assert completed.read("endgame_assessment.json") is None
    assert completed.read("predictions.json") == []


def test_gaps_and_ownership_require_explicit_attributed_waivers(completed, assessment):
    criterion_id = assessment["evaluations"][0]["criterion_id"]
    assessment["evaluations"][0].update(
        verdict="unevaluated",
        ruling_refs=[],
        board_refs=[],
        rationale="The game did not resolve this observable",
    )
    assessment["insights"][0]["owner"] = None
    stage_assessment(completed, assessment)
    with completed.transaction(), pytest.raises(PerlaError) as error:
        endgame.finalize(completed)
    assert error.value.code == "E_UNEVALUATED_CRITERIA"
    with completed.transaction(), pytest.raises(PerlaError) as error:
        endgame.finalize(completed, accepted_gaps=[criterion_id])
    assert error.value.code == "E_UNOWNED_INSIGHTS"
    with completed.transaction(), pytest.raises(PerlaError) as error:
        endgame.finalize(completed, [criterion_id], ["capacity"])
    assert error.value.code == "E_WAIVER"
    with completed.transaction(), pytest.raises(PerlaError) as error:
        reporting.export_context(completed)
    assert error.value.code == "E_UNEVALUATED_CRITERIA"
    with completed.transaction():
        final = endgame.finalize(
            completed,
            [criterion_id],
            ["capacity"],
            "facilitator",
            "Accepted explicitly in the fixture",
        )
        assert final["waivers"]["accepted_gaps"] == [criterion_id]
        data = reporting.bundle(completed)
        assert data["criteria"][0]["evaluation"]["verdict"] == "unevaluated"
        assert data["insights"][0]["owner"] is None
        assert "W_ACCEPTED_GAPS" in {w["code"] for w in final["health"]["warnings"]}


def test_human_revision_fills_gaps_without_erasing_model_provenance(completed, assessment):
    desired = copy.deepcopy(assessment)
    assessment["insights"][0].update(owner=None, due_date=None)
    stage_assessment(completed, assessment)
    source = completed.root / "revision.json"
    atomic_json(source, desired)
    with completed.transaction():
        endgame.revise(completed, source, desired, "alice", "Assigned follow-up ownership")
        record = completed.read("endgame_assessment.json")
        assert record["history"][0]["provenance"]["origin"] == "simulation"
        assert record["provenance"]["origin"] == "human"
        assert record["provenance"]["operation"] == "human_revision"
        endgame.finalize(completed)
        insight = completed.read("insights.json")[0]
        assert insight["provenance"]["origin"] == "human"
        assert insight["source_provenance"]["ruling:1:r001"]["origin"] == "simulation"
        workflow.validate(completed)
    with completed.transaction(), pytest.raises(PerlaError) as error:
        endgame.revise(completed, source, desired, "alice", "Attempt a later rewrite")
    assert error.value.code == "E_ENDGAME_LOCKED"


def test_deadline_and_calendar_date_validation(completed, assessment):
    context = endgame.game_context(completed, workflow.project(completed))
    context["boards"]["board:2"] = context["boards"]["board:1"]
    assessment["evaluations"][0]["board_refs"] = ["board:1", "board:2"]
    with pytest.raises(PerlaError) as error:
        endgame.validate_assessment(assessment, context)
    assert error.value.code == "E_CITATION"
    assessment["evaluations"][0]["board_refs"] = ["board:1"]
    assessment["predictions"][0]["due_date"] = "2027-02-30"
    with pytest.raises(ValidationError):
        endgame.validate_assessment(assessment, context)


@pytest.mark.parametrize("filename", ["predictions.json", "insights.json", "endgame.json"])
def test_finalized_artifact_tampering_blocks_reports(completed, assessment, filename):
    stage_assessment(completed, assessment)
    with completed.transaction():
        endgame.finalize(completed)
    value = completed.read(filename)
    if isinstance(value, list):
        value.pop()
    else:
        value["waivers"]["reviewer"] = "Forged reviewer"
    atomic_json(completed.path(filename), value)
    with completed.transaction(), pytest.raises(PerlaError) as error:
        reporting.export_html(completed)
    assert error.value.code == "E_ENDGAME"


def test_html_escapes_text_and_exports_cannot_overwrite_canonical_state(completed, assessment):
    assessment["insights"][0]["text"] = '<script>alert("untrusted")</script>'
    stage_assessment(completed, assessment)
    with completed.transaction():
        endgame.finalize(completed)
        data, _ = reporting.export_html(completed)
    rendered = Path(data["path"]).read_text()
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    project_bytes = completed.path("project.json").read_bytes()
    with completed.transaction(), pytest.raises(PerlaError) as error:
        reporting.export_context(completed, completed.path("project.json"))
    assert error.value.code == "E_OUTPUT"
    assert completed.path("project.json").read_bytes() == project_bytes


def test_cli_endgame_and_reports(completed, assessment, capsys):
    stage_assessment(completed, assessment)
    source = completed.root / "reviewed.json"
    atomic_json(source, assessment)

    def run(*args):
        code = main(["--project", str(completed.root), *args])
        envelope = json.loads(capsys.readouterr().out)
        assert code == 0, envelope
        return envelope

    response = run(
        "endgame",
        "revise",
        "--input",
        str(source),
        "--by",
        "reviewer",
        "--note",
        "Reviewed the proposed assessment",
    )
    assert response["command"] == ["endgame", "revise"]
    assert run("endgame")["data"]["finalized_at"]
    assert run("agent-start")["data"]["phase"] == "complete"
    assert Path(run("report", "context")["data"]["path"]).exists()
    assert Path(run("report", "html")["data"]["path"]).exists()
    assert run("export", "uncertainties")["data"]["uncertainty_count"] == 1


def test_lookback_cli_revisions_scores_reports_and_frozen_registry(completed, assessment, capsys):
    from perla import lookback

    for prediction in assessment["predictions"]:
        prediction["probability"] = 0.8
    stage_assessment(completed, assessment)
    with completed.transaction():
        endgame.finalize(completed)
    registry_before = completed.path("predictions.json").read_bytes()
    final_before = completed.path("endgame.json").read_bytes()

    def run(*args, expected=0):
        code = main(["--project", str(completed.root), *args])
        response = json.loads(capsys.readouterr().out)
        assert code == expected, response
        return response.get("data") if not expected else response["error"]

    assert run("lookback", "show", expected=1)["code"] == "E_LOOKBACK_REQUIRED"
    opened = run("lookback", "open", "--by", "reviewer", "--note", "Scheduled review")
    assert run("lookback", "open", "--by", "reviewer", "--note", "Retry") == opened
    registry = completed.read("predictions.json")
    observed = {
        "id": "pilot",
        "statement": registry[0]["statement"],
        "prediction_ids": [registry[0]["id"]],
        "status": "resolved",
        "outcome": True,
        "as_of": "2028-01-01",
        "occurred_on": "2027-10-01",
        "rationale": "<script>Fictional launch confirmed</script>",
        "sources": [{"reference": "fixture:launch", "description": "Scripted evidence"}],
    }
    source = completed.root / "outcomes.json"
    atomic_json(source, {"events": [observed]})
    run(
        "lookback",
        "record",
        "--input",
        str(source),
        "--by",
        "alice",
        "--note",
        "Initial observation",
    )
    first = run("lookback", "score", "--as-of", "2028-01-01")
    assert first["result"]["summary"]["brier_score"] == pytest.approx(0.04)
    assert run("lookback", "score", "--as-of", "2028-01-01") == first
    exported = run("report", "lookback")
    first_bytes = Path(exported["path"]).read_bytes()
    run("report", "lookback")
    assert Path(exported["path"]).read_bytes() == first_bytes
    observed.update(
        outcome=False, occurred_on=None, rationale="The announcement was a plan, not a launch"
    )
    atomic_json(source, {"events": [observed]})
    revised = run(
        "lookback", "record", "--input", str(source), "--by", "bob", "--note", "Corrected evidence"
    )
    assert revised["score_stale"] is True
    assert revised["events"]["pilot"]["history"][0]["provenance"]["author"] == "alice"
    assert run("report", "lookback", expected=1)["code"] == "E_LOOKBACK_SCORE"
    html_result = run("report", "html")
    rendered = Path(html_result["path"]).read_text()
    assert "Historical score" in rendered and "<script>" not in rendered
    assert "<svg" in rendered and "&lt;script&gt;" in rendered
    second = run("lookback", "score", "--as-of", "2028-01-01")
    assert second["result"]["summary"]["brier_score"] == pytest.approx(0.64)
    assert second["id"] == "s002"
    run("report", "lookback")
    context = run("report", "context")
    assert read_json(context["path"])["lookback"]["score_stale"] is False
    assert run("status")["lookback"]["score_count"] == 2
    assert run("workflow", "validate")["valid"]
    assert completed.path("predictions.json").read_bytes() == registry_before
    assert completed.path("endgame.json").read_bytes() == final_before
    with completed.transaction():
        record = lookback.show(completed)
        assert record["events"]["pilot"]["history"][0]["content"]["outcome"] is True
    tampered = completed.read("lookback.json")
    tampered["scores"][0]["result"]["summary"]["brier_score"] = 0
    atomic_json(completed.path("lookback.json"), tampered)
    assert run("workflow", "validate", expected=1)["code"] == "E_LOOKBACK"
    assert run("report", "context", expected=1)["code"] == "E_LOOKBACK"


def test_lookback_batch_validation_is_atomic_and_mapping_is_locked(completed, assessment):
    from perla import lookback

    stage_assessment(completed, assessment)
    with completed.transaction():
        endgame.finalize(completed)
        lookback.open_lookback(completed, "reviewer", "Test observations")
    observed = {
        "id": "pilot",
        "statement": "Pilot launched",
        "prediction_ids": ["p001"],
        "status": "unresolved",
        "outcome": None,
        "as_of": "2028-01-01",
        "rationale": "Insufficient evidence",
        "sources": [{"reference": "fixture:evidence", "description": "Missing launch records"}],
    }
    before = completed.path("lookback.json").read_bytes()
    invalid = copy.deepcopy(observed)
    invalid.update(id="invalid", prediction_ids=["p999"])
    with pytest.raises(PerlaError), completed.transaction():
        lookback.record_outcomes(completed, {"events": [observed, invalid]}, "alice", "Batch")
    assert completed.path("lookback.json").read_bytes() == before
    with completed.transaction():
        lookback.record_outcomes(completed, {"events": [observed]}, "alice", "Initial")
    before = completed.path("lookback.json").read_bytes()
    observed["prediction_ids"] = ["p002"]
    with pytest.raises(PerlaError) as exc, completed.transaction():
        lookback.record_outcomes(completed, {"events": [observed]}, "bob", "Remap")
    assert exc.value.code == "E_LOOKBACK_EVENT"
    assert completed.path("lookback.json").read_bytes() == before
    joint = copy.deepcopy(observed)
    joint.update(id="joint", prediction_ids=["p003", "p002"])
    last = copy.deepcopy(observed)
    last.update(id="last", prediction_ids=["p004"])
    with completed.transaction():
        result = lookback.record_outcomes(
            completed, {"events": [joint, last]}, "alice", "Shared submission"
        )
    assert (
        result["events"]["joint"]["provenance"]["submission_hash"]
        == result["events"]["last"]["provenance"]["submission_hash"]
    )
