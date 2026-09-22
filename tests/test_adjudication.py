import copy
import json

import pytest
from pydantic import ValidationError

from perla import adjudication, rounds, workflow
from perla.cli import main
from perla.store import PerlaError, atomic_json


def submission(store, job_id, answer):
    manifest = store.read(f"jobs/{job_id}.manifest.json")
    path = store.root / "submission.json"
    atomic_json(
        path,
        {
            "project_id": manifest["project_id"],
            "job_id": job_id,
            "actor_id": manifest["actor_id"],
            "kind": manifest["kind"],
            "move": manifest["move"],
            "participant_id": "test-facilitator",
            "answer": answer,
        },
    )
    return path


@pytest.fixture
def committed(ready, edsl, move_answer):
    with ready.transaction():
        jobs = workflow.generate(ready, "moves", move=1)["jobs"]
        for job in jobs:
            path = submission(ready, job["job_id"], move_answer)
            workflow.ingest(ready, "moves", job["job_id"], path, human=True)
    return ready


@pytest.fixture
def ruling():
    return {
        "ruling_id": "r001",
        "basis": "dossier_fact",
        "cite": ["d.home.h1"],
        "rationale": "Shared delivery constraints limit simultaneous expansion",
        "resolves": ["m1.home.a1", "m1.rival.a1", "m1.startup.a1"],
        "affected_actors": ["home", "rival", "startup"],
        "chain": [
            {
                "link": "L1",
                "claim": "The home pilot has only twenty qualified teams",
                "basis": "dossier_fact",
                "cite": ["d.home.h1"],
            },
            {
                "link": "L2",
                "claim": "Simultaneous launches increase recruiting pressure",
                "basis": "judgment",
            },
        ],
        "outcome": {
            "description": "Capacity limits the pilot",
            "confidence": 0.7,
            "board_delta": {"home_qualified_teams": 18},
        },
        "kill_criteria": ["supply"],
    }


def test_open_refuses_missing_and_corrupt_moves(ready, edsl, move_answer):
    with ready.transaction():
        job = workflow.generate(ready, "moves", actor_id="home", move=1)["jobs"][0]
        path = submission(ready, job["job_id"], move_answer)
        workflow.ingest(ready, "moves", job["job_id"], path, human=True)
    with ready.transaction(), pytest.raises(PerlaError) as error:
        adjudication.open_round(ready, 1)
    assert error.value.code == "E_UNCOMMITTED_MOVES"
    assert not ready.records("adjudication")
    assert all(j["kind"] == "moves" for j in ready.read("project.json")["jobs"].values())
    with ready.transaction(), pytest.raises(PerlaError) as error:
        adjudication.show_round(ready, 1)
    assert error.value.code == "E_ADJUDICATION"


def test_open_checks_hashes_before_export(committed):
    record = committed.read("moves/move-1/home.json")
    record["content"]["actions"] = ["Changed after committing"]
    atomic_json(committed.path("moves/move-1/home.json"), record)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        adjudication.open_round(committed, 1)
    assert error.value.code == "E_COMMITMENT"
    assert not committed.records("adjudication")


def test_actual_control_export_and_repeat(committed, edsl):
    with committed.transaction():
        first = adjudication.open_round(committed, 1)
        second = adjudication.open_round(committed, 1)
        assert second["reused"] and first["job_id"] == second["job_id"]
        assert workflow.status(committed)[0]["phase"] == "awaiting_rulings"
        assert workflow.validate(committed)[0]["audited_jobs"] == 4
    package = edsl.Jobs.git.load(first["package"])
    context = json.loads(package.scenarios[0]["context_json"])
    assert not package.models
    assert set(context["dossiers"]) == {"home", "rival", "startup"}
    assert len(context["moves"]) == len(context["actions"]) == len(context["criteria"]) == 3
    assert package.agents[0].traits["role"] == "control"
    assert package.survey.question_names == ["rulings"]


def test_human_ingest_pending_without_board_application(committed, ruling):
    before = committed.read("board/move-0.json")
    with committed.transaction():
        job = adjudication.open_round(committed, 1, mode="human")
        path = submission(committed, job["job_id"], {"rulings": [ruling]})
        result = workflow.ingest(committed, "rulings", job["job_id"], path, human=True)
        assert result["basis_distribution"] == {"dossier_fact": 1}
        show = adjudication.show_round(committed, 1)
        assert not show["board_applied"]
        record = show["rulings"][0]
        assert record["resolution_mode"] == "human"
        assert record["status"] == "pending_rebuttal"
        assert record["escalation_candidates"] == ["judgment", "kill_criterion"]
        assert workflow.status(committed)[0]["phase"] == "adjudication_routing"
        workflow.validate(committed)
    assert committed.read("board/move-0.json") == before
    assert not committed.path("board/move-1.json").exists()
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(committed, "rulings", job["job_id"], path, human=True)
    assert error.value.code == "E_COMMITTED"
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.generate(committed, "moves", move=2)
    assert error.value.code == "E_OPEN_RULINGS"


def test_actual_control_results_roundtrip(committed, ruling, edsl):
    with committed.transaction():
        job = adjudication.open_round(committed, 1)
    package = edsl.Jobs.git.load(job["package"])
    answer = {"rulings": [ruling]}
    # Exercise the question's actual response validator, including nested dictionaries.
    package.survey.questions[0]._validate_answer({"answer": answer})
    result = edsl.Result(
        agent=package.agents[0],
        scenario=package.scenarios[0],
        model=edsl.Model("test"),
        iteration=0,
        answer={"rulings": answer},
    )
    path = committed.root / "control.results.ep"
    edsl.Results(data=[result], survey=package.survey).git.save(path)
    with committed.transaction():
        workflow.ingest(committed, "rulings", job["job_id"], path)
        record = adjudication.show_round(committed, 1)["rulings"][0]
        assert record["provenance"]["model"] == "test"
        assert record["resolution_mode"] == "agent"
        workflow.validate(committed)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("resolves", ["m1.home.missing"], "E_RULING_REFERENCE"),
        ("resolves", ["m1.home.a1"], "E_UNRESOLVED_ACTIONS"),
        ("affected_actors", ["home"], "E_RULING_REFERENCE"),
        ("kill_criteria", ["invented"], "E_RULING_REFERENCE"),
        ("cite", ["d.rival.h1"], "E_CITATION"),
        ("cite", [], "E_CITATION"),
        ("basis", "panel", "E_UNSUPPORTED_BASIS"),
        ("basis", "model", "E_UNSUPPORTED_BASIS"),
    ],
)
def test_invalid_rulings_are_atomic(committed, ruling, field, value, code):
    with committed.transaction():
        job = adjudication.open_round(committed, 1)
    invalid = copy.deepcopy(ruling)
    invalid[field] = value
    path = submission(committed, job["job_id"], {"rulings": [invalid]})
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(committed, "rulings", job["job_id"], path, human=True)
    assert error.value.code == code
    assert committed.records("rulings/move-1") == []
    assert not committed.read("project.json")["jobs"][job["job_id"]]["ingested"]


def test_testimony_snapshot_and_citation(committed, ruling):
    testimony = {
        "id": "procurement",
        "claim": "Security review takes at least nine months",
        "scope": "Enterprise procurement in 2027",
        "attribution": "Fictional buyer",
        "sources": ["Scripted tutorial interview"],
    }
    with committed.transaction():
        adjudication.add_testimony(committed, testimony, "facilitator")
        job = adjudication.open_round(committed, 1)
        ruling.update({"basis": "testimony", "cite": ["t.procurement"]})
        path = submission(committed, job["job_id"], {"rulings": [ruling]})
        workflow.ingest(committed, "rulings", job["job_id"], path, human=True)
        assert len(adjudication.testimony_records(committed, workflow.project(committed))) == 1
        with pytest.raises(PerlaError) as error:
            adjudication.add_testimony(committed, {**testimony, "id": "late"}, "facilitator")
        assert error.value.code == "E_EVIDENCE_LOCKED"
    record = committed.read("testimony/procurement.json")
    record["claim"] = "Silently revised evidence"
    atomic_json(committed.path("testimony/procurement.json"), record)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.validate(committed)
    assert error.value.code == "E_EVIDENCE"


def test_reference_cases_and_branches(committed, ruling):
    with committed.transaction():
        context = adjudication.control_context(committed, workflow.project(committed), 1)
    ruling.update(
        {
            "basis": "reference_class",
            "cite": [],
            "cases": [
                {
                    "name": "Fictional launch A",
                    "sources": ["Teaching case A"],
                    "mapping": "Same delivery bottleneck",
                    "disanalogies": "Different buyer segment",
                },
                {
                    "name": "Fictional launch B",
                    "sources": ["Teaching case B"],
                    "mapping": "Concurrent recruiting pressure",
                    "disanalogies": "Different financing",
                },
            ],
        }
    )
    ruling["branches"] = {
        "uncertainty": "Whether recruiting contracts become exclusive",
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
    adjudication.validate_batch({"rulings": [ruling]}, context)
    invalid = copy.deepcopy(ruling)
    invalid["cases"][1]["name"] = invalid["cases"][0]["name"]
    with pytest.raises(PerlaError) as error:
        adjudication.validate_batch({"rulings": [invalid]}, context)
    assert error.value.code == "E_CITATION"
    for mutate in (
        lambda r: r["branches"]["options"][0].update(probability=0.8),
        lambda r: r["branches"].update(played="unknown"),
        lambda r: r["outcome"].update(board_delta={"home_qualified_teams": 5}),
        lambda r: r["outcome"].update(confidence=float("nan")),
        lambda r: r["chain"].append(r["chain"][0]),
        lambda r: r.update(resolution_mode="human_panel"),
    ):
        invalid = copy.deepcopy(ruling)
        mutate(invalid)
        with pytest.raises(ValidationError):
            adjudication.validate_batch({"rulings": [invalid]}, context)


def test_link_evidence_and_invalid_second_ruling_rollback(committed, ruling):
    with committed.transaction():
        job = adjudication.open_round(committed, 1)
    invalid = copy.deepcopy(ruling)
    invalid["ruling_id"] = "r002"
    invalid["chain"][0]["cite"] = ["t.unknown"]
    path = submission(committed, job["job_id"], {"rulings": [ruling, invalid]})
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(committed, "rulings", job["job_id"], path, human=True)
    assert error.value.code == "E_CITATION"
    assert not committed.records("rulings/move-1")
    assert committed.read("adjudication/move-1.json")["status"] == "awaiting_rulings"


@pytest.mark.parametrize(
    "tamper", ["delete_ruling", "edit_ruling", "delete_round", "edit_snapshot"]
)
def test_adjudication_audit_detects_tampering(committed, ruling, tamper):
    with committed.transaction():
        job = adjudication.open_round(committed, 1)
        path = submission(committed, job["job_id"], {"rulings": [ruling]})
        workflow.ingest(committed, "rulings", job["job_id"], path, human=True)
    if tamper == "delete_ruling":
        committed.path("rulings/move-1/r001.json").unlink()
    elif tamper == "delete_round":
        committed.path("adjudication/move-1.json").unlink()
    elif tamper == "edit_ruling":
        record = committed.read("rulings/move-1/r001.json")
        record["provenance"]["origin"] = "simulation"
        atomic_json(committed.path("rulings/move-1/r001.json"), record)
    else:
        record = committed.read("adjudication/move-1.json")
        record["context_hash"] = "wrong"
        atomic_json(committed.path("adjudication/move-1.json"), record)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.validate(committed)
    assert error.value.code in {"E_RULING", "E_ADJUDICATION"}


def test_cli_control_workflow(committed, ruling, capsys):
    def run(*args):
        code = main(["--project", str(committed.root), *args])
        envelope = json.loads(capsys.readouterr().out)
        assert code == 0, envelope
        return envelope

    opening = run("adjudicate", "open", "--move", "1")
    assert opening["command"] == ["adjudicate", "open"]
    job_id = opening["data"]["job_id"]
    source = submission(committed, job_id, {"rulings": [ruling]})
    response = run("ingest", "rulings", "--job", job_id, "--source", str(source), "--human-input")
    assert response["command"] == ["ingest", "rulings"]
    assert (
        run("adjudicate", "show", "--move", "1")["data"]["rulings"][0]["content"]["ruling_id"]
        == "r001"
    )
    assert run("agent-start")["data"]["phase"] == "adjudication_routing"
    assert run("workflow", "validate")["data"]["audited_jobs"] == 4


def prepare_review(store, ruling, budget=5):
    with store.transaction():
        job = adjudication.open_round(store, 1)
        workflow.ingest(
            store,
            "rulings",
            job["job_id"],
            submission(store, job["job_id"], {"rulings": [ruling]}),
            human=True,
        )
        rounds.set_policy(store, {"human_budget": budget})
        rounds.route_round(store, 1)


def collect_rebuttals(store, challenge=False):
    with store.transaction():
        jobs = rounds.generate_rebuttals(store, 1)["jobs"]
    for job in jobs:
        manifest = store.read(f"jobs/{job['job_id']}.manifest.json")
        actor_id = job["actor_id"]
        fact = manifest["context"]["dossier"]["facts"][0]["id"]
        answer = {
            "rebuttals": [
                {
                    "ruling_id": key,
                    "stance": "challenge" if challenge else "accept",
                    "argument": "The named constraint supports a narrower claim"
                    if challenge
                    else "No objection",
                    "link": "L1" if challenge else None,
                    "cite": [f"d.{actor_id}.{fact}"] if challenge else [],
                }
                for key in manifest["context"]["rulings"]
            ]
        }
        with store.transaction():
            workflow.ingest(
                store,
                "rebuttals",
                job["job_id"],
                submission(store, job["job_id"], answer),
                human=True,
            )
    return jobs


def resolve_review(store, revision=None):
    with store.transaction():
        job = rounds.generate_resolution(store, 1)
        original = rounds.initial_rulings(store, 1)
        answer = {
            "resolutions": [
                {
                    "ruling_id": key,
                    "decision": "revise" if revision else "affirm",
                    "reason": "Addressed the single rebuttal round",
                    "responses": [
                        {"actor_id": actor, "reason": "The evidence supports this final mechanism"}
                        for actor in record["content"]["affected_actors"]
                    ],
                    "revision": revision,
                }
                for key, record in original.items()
            ]
        }
        workflow.ingest(
            store,
            "resolutions",
            job["job_id"],
            submission(store, job["job_id"], answer),
            human=True,
        )
    return job


def test_round_rebuttals_approval_and_closure(committed, ruling, edsl):
    prepare_review(committed, ruling)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        rounds.close_round(committed, 1)
    assert error.value.code == "E_OPEN_RULINGS"
    jobs = collect_rebuttals(committed, challenge=True)
    for job in jobs:
        package = edsl.Jobs.git.load(job["package"])
        context = json.loads(package.scenarios[0]["context_json"])
        assert set(context) == {"decision", "board", "dossier", "rulings"}
        assert "rebuttals" not in context
        assert len(context["dossier"]["facts"]) == 1
    revised = copy.deepcopy(ruling)
    revised["outcome"]["board_delta"]["home_qualified_teams"] = 19
    resolve_review(committed, revision=revised)
    with committed.transaction():
        assert rounds.phase(committed, workflow.project(committed), 1) == "escalation_review"
        assert (
            "revised"
            in rounds.read_review(committed, workflow.project(committed), 1)["routing"]["r001"][
                "reasons"
            ]
        )
    with committed.transaction(), pytest.raises(PerlaError) as error:
        rounds.approve(committed, 1, "reviewer", "Premature approval")
    assert error.value.code == "E_OPEN_RULINGS"
    with committed.transaction():
        rounds.assess(
            committed,
            1,
            "r001",
            "domain-expert",
            "The revised mechanism matches the supplied constraints",
        )
    with committed.transaction(), pytest.raises(PerlaError) as error:
        rounds.close_round(committed, 1)
    assert error.value.code == "E_APPROVAL"
    with committed.transaction():
        rounds.approve(committed, 1, "facilitator", "Reviewed the final outcomes and causal chain")
        closed = rounds.close_round(committed, 1)
        assert closed["board"]["home_qualified_teams"] == 19
        assert (
            rounds.initial_rulings(committed, 1)["r001"]["content"]["outcome"]["board_delta"][
                "home_qualified_teams"
            ]
            == 18
        )
        assert adjudication.show_round(committed, 1)["board_applied"]
        next_jobs = workflow.generate(committed, "moves", move=2)["jobs"]
        assert len(next_jobs) == 3
        adjudication.add_testimony(
            committed,
            {
                "id": "later",
                "claim": "A new constraint for later rounds",
                "attribution": "Test expert",
                "scope": "Moves two and three",
                "sources": ["Fictional follow-up interview"],
            },
            "tester",
        )
        workflow.validate(committed)
    next_package = edsl.Jobs.git.load(next_jobs[0]["package"])
    assert (
        json.loads(next_package.scenarios[0]["context_json"])["board"]["home_qualified_teams"] == 19
    )
    with committed.transaction(), pytest.raises(PerlaError) as error:
        rounds.assess(committed, 1, "r001", "reviewer", "Try reopening")
    assert error.value.code == "E_ROUND_CLOSED"


def test_rebuttal_gates_and_duplicate_commitments(committed, ruling):
    prepare_review(committed, ruling)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        rounds.generate_resolution(committed, 1)
    assert error.value.code == "E_REBUTTALS_PENDING"
    with committed.transaction():
        job = rounds.generate_rebuttals(committed, 1, "home")["jobs"][0]
    invalid = {
        "rebuttals": [
            {
                "ruling_id": "r001",
                "stance": "challenge",
                "argument": "Unsupported",
                "link": "missing",
                "cite": ["d.rival.r1"],
            }
        ]
    }
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(
            committed,
            "rebuttals",
            job["job_id"],
            submission(committed, job["job_id"], invalid),
            human=True,
        )
    assert error.value.code == "E_REBUTTAL"
    assert not committed.read("review/move-1.json")["rebuttals"]
    collect_rebuttals(committed)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.ingest(
            committed, "rebuttals", job["job_id"], committed.root / "unused", human=True
        )
    assert error.value.code == "E_COMMITTED"


def test_budget_ranking_and_frozen_policy(ruling):
    records = {}
    for name, criterion, impact in (
        ("important", True, 1),
        ("large", False, 10),
        ("small", False, 0),
    ):
        content = copy.deepcopy(ruling)
        content["ruling_id"] = name
        content["kill_criteria"] = ["supply"] if criterion else []
        records[name] = {"content": content}
    policy = {"human_budget": 1, "auto_confidence": 0.7, "judgment_share_threshold": 0.4}
    routing = rounds.make_routing(
        records, policy, {"flagged": [], "impact_scores": {"large": 10.0, "small": 0.0}}
    )
    assert routing["important"]["blocking"]
    assert routing["large"]["over_budget"] and not routing["large"]["blocking"]
    assert routing["small"]["over_budget"]


def test_budget_overflow_is_explicit_debt(committed, ruling):
    ruling["basis"] = "judgment"
    prepare_review(committed, ruling, budget=0)
    with committed.transaction():
        rounds.set_policy(committed, {"human_budget": 10})
        assert rounds.route_round(committed, 1)["policy"]["human_budget"] == 0
    collect_rebuttals(committed)
    resolve_review(committed)
    with committed.transaction():
        rounds.approve(committed, 1, "reviewer", "Explicitly reviewed provisional escalation debt")
        closed = rounds.close_round(committed, 1)
        assert closed["escalation_debt"] == ["r001"]
        _, warnings = workflow.validate(committed)
        assert {w["code"] for w in warnings} >= {"E_JUDGMENT_SHARE", "W_ESCALATION_DEBT"}
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.validate(committed, strict=True)
    assert error.value.code == "E_JUDGMENT_SHARE"


def test_conflicting_board_values_require_content_bound_choices(committed, ruling, edsl):
    second = copy.deepcopy(ruling)
    ruling.update(resolves=["m1.home.a1", "m1.rival.a1"], affected_actors=["home", "rival"])
    second.update(ruling_id="r002", resolves=["m1.startup.a1"], affected_actors=["startup"])
    second["outcome"]["board_delta"]["home_qualified_teams"] = 10
    with committed.transaction():
        job = adjudication.open_round(committed, 1)
        workflow.ingest(
            committed,
            "rulings",
            job["job_id"],
            submission(committed, job["job_id"], {"rulings": [ruling, second]}),
            human=True,
        )
        rounds.set_policy(committed, {"human_budget": 0})
        rounds.route_round(committed, 1)
    jobs = collect_rebuttals(committed)
    for job in jobs:
        package = edsl.Jobs.git.load(job["package"])
        assigned = json.loads(package.scenarios[0]["context_json"])["rulings"]
        assert set(assigned) == ({"r002"} if job["actor_id"] == "startup" else {"r001"})
    resolve_review(committed)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        rounds.approve(committed, 1, "reviewer", "Cannot silently order conflicting effects")
    assert error.value.code == "E_BOARD_CONFLICT"
    with committed.transaction():
        rounds.reconcile(
            committed,
            1,
            {
                "choices": {
                    "home_qualified_teams": {
                        "ruling_id": "r002",
                        "reason": "The startup's talent pressure dominates this field",
                    }
                }
            },
            "reviewer",
        )
        rounds.approve(committed, 1, "reviewer", "Reviewed the reconciled board")
        rounds.reconcile(
            committed,
            1,
            {
                "choices": {
                    "home_qualified_teams": {
                        "ruling_id": "r001",
                        "reason": "Corrected priority after reviewing the mechanisms",
                    }
                }
            },
            "reviewer",
        )
    with committed.transaction(), pytest.raises(PerlaError) as error:
        rounds.close_round(committed, 1)
    assert error.value.code == "E_APPROVAL"
    with committed.transaction():
        rounds.approve(committed, 1, "reviewer", "Approved the corrected reconciliation")
        assert rounds.close_round(committed, 1)["board"]["home_qualified_teams"] == 18


def test_failed_close_rolls_back_and_board_tampering_blocks_next_round(
    committed, ruling, monkeypatch
):
    prepare_review(committed, ruling, budget=0)
    collect_rebuttals(committed)
    resolve_review(committed)
    with committed.transaction():
        rounds.approve(committed, 1, "reviewer", "Approved fixture")
    original_write = committed.write

    def fail_review_write(path, value):
        if path == "review/move-1.json":
            raise OSError("Simulated interruption after staging the board")
        return original_write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(committed, "write", fail_review_write)
        with pytest.raises(OSError), committed.transaction():
            rounds.close_round(committed, 1)
    assert committed.read("project.json")["closed_move"] == 0
    assert not committed.path("board/move-1.json").exists()
    with committed.transaction():
        rounds.close_round(committed, 1)
    board = committed.read("board/move-1.json")
    board["home_qualified_teams"] = 100
    atomic_json(committed.path("board/move-1.json"), board)
    with committed.transaction(), pytest.raises(PerlaError) as error:
        workflow.generate(committed, "moves", move=2)
    assert error.value.code == "E_BOARD"


def test_resolution_cannot_remove_criterion_links_or_actor_scope(committed, ruling):
    prepare_review(committed, ruling)
    collect_rebuttals(committed, challenge=True)
    revision = copy.deepcopy(ruling)
    revision["kill_criteria"] = []
    with pytest.raises(PerlaError) as error:
        resolve_review(committed, revision)
    assert error.value.code == "E_RESOLUTION"
    assert committed.read("review/move-1.json")["resolutions"] is None
