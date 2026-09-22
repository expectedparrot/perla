"""Run a fictional game through lookback using synthetic Results and scripted reality."""

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from edsl import Jobs, Model, Result, Results

from perla.cli import main


def run_demo(root):
    example = Path(__file__).resolve().parent

    def run(*args):
        with redirect_stdout(io.StringIO()) as output:
            code = main(["--project", str(root), *args])
        envelope = json.loads(output.getvalue())
        if code:
            raise RuntimeError(envelope["error"])
        return envelope["data"]

    def synthetic_result(job_id, package_path, answer):
        package = Jobs.git.load(package_path)
        kind = package.scenarios[0]["kind"]
        package.survey.questions[0]._validate_answer({"answer": answer})
        result = Result(
            agent=package.agents[0],
            scenario=package.scenarios[0],
            model=Model("test"),
            iteration=0,
            answer={kind: answer},
        )
        path = root / f"{job_id}.results.ep"
        Results(data=[result], survey=package.survey).git.save(path)
        run("ingest", kind, "--job", job_id, "--source", str(path))

    run(
        "init",
        "--input",
        str(example / "game.json"),
        "--delegate",
        "dossiers",
        "--delegate",
        "rulings",
    )
    policy_path = root / "demo-policy.json"
    policy_path.write_text(json.dumps({"human_budget": 0}))
    run("adjudicate", "policy", "set", "--input", str(policy_path))
    for actor_id in ("home", "rival", "startup"):
        run(
            "actor",
            "add",
            actor_id,
            "--name",
            actor_id,
            "--role",
            "home" if actor_id == "home" else "competitor",
        )
        run(
            "dossier",
            "edit",
            actor_id,
            "--input",
            str(example / f"{actor_id}.json"),
            "--by",
            "scripted-teaching-fixture",
            "--note",
            "Imported fictional assumptions",
        )
    run(
        "testimony",
        "add",
        "--input",
        str(example / "testimony.json"),
        "--by",
        "scripted-teaching-fixture",
    )
    actions = {
        "home": "Launch the fixed-price managed-delivery pilot",
        "rival": "Discount marketplace fees for existing buyers",
        "startup": "Subsidize initial managed-delivery contracts",
    }
    for move in range(1, 4):
        for job in run("job", "generate", "moves", "--move", str(move))["jobs"]:
            synthetic_result(
                job["job_id"],
                job["package"],
                {
                    "actions": [actions[job["actor_id"]]],
                    "rationale": "Scripted illustration of the actor's dossier incentives",
                    "resource_commitments": ["Commit available pilot resources for eight months"],
                    "expected_responses": [
                        "Other actors compete for the same buyers and delivery teams"
                    ],
                },
            )
        job = run("adjudicate", "open", "--move", str(move))
        answer = json.loads((example / "rulings.json").read_text())
        ruling = answer["rulings"][0]
        ruling["resolves"] = [ref.replace("m1.", f"m{move}.") for ref in ruling["resolves"]]
        ruling["outcome"]["board_delta"]["home_qualified_teams"] = 19 - move
        ruling["outcome"]["board_delta"]["calendar_period"] = f"After {8 * move} game-months"
        ruling["chain"][-1]["claim"] = (
            f"The fictional competitive pressure leaves {19 - move} home teams available"
        )
        if move > 1:
            ruling["chain"] = [link for link in ruling["chain"] if link["link"] != "L3"]
        synthetic_result(job["job_id"], job["package"], answer)
        run("adjudicate", "route", "--move", str(move))
        for job in run("job", "generate", "rebuttals", "--move", str(move))["jobs"]:
            synthetic_result(
                job["job_id"],
                job["package"],
                {
                    "rebuttals": [
                        {
                            "ruling_id": "r001",
                            "stance": "accept",
                            "argument": "No objection in this scripted fixture",
                            "link": None,
                            "cite": [],
                        }
                    ]
                },
            )
        job = run("adjudicate", "resolve", "--move", str(move))
        synthetic_result(
            job["job_id"],
            job["package"],
            {
                "resolutions": [
                    {
                        "ruling_id": "r001",
                        "decision": "affirm",
                        "reason": "All scripted actors accepted the proposed mechanism",
                        "responses": [
                            {
                                "actor_id": actor,
                                "reason": "Acceptance recorded; original mechanism retained",
                            }
                            for actor in actions
                        ],
                        "revision": None,
                    }
                ]
            },
        )
        run("adjudicate", "close", "--move", str(move))
    job = run("job", "generate", "novelty")
    synthetic_result(
        job["job_id"],
        job["package"],
        {
            "findings": [
                {
                    "id": "capacity",
                    "text": "A limited pool of qualified delivery teams constrains simultaneous expansion",
                }
            ]
        },
    )
    job = run("job", "generate", "endgame")
    package = Jobs.git.load(job["package"])
    context = json.loads(package.scenarios[0]["context_json"])
    predictions = []
    for source in [*context["moves"], *context["rulings"]]:
        move = int(source.split(":")[1])
        criteria = (
            "Resolve true if dated company records or product announcements confirm all listed actions by the deadline; insufficient evidence remains unresolved."
            if source.startswith("move:")
            else f"Resolve true if dated staffing records show {19 - move} available qualified home delivery teams at the deadline; insufficient evidence remains unresolved."
        )
        predictions.append(
            {
                "source": source,
                "due_date": ["2027-08-31", "2028-04-30", "2028-12-31"][move - 1],
                "resolution_criteria": criteria,
                # Teaching probabilities supplied at registration, independent of control confidence.
                "probability": [0.7, 0.6, 0.8][move - 1],
            }
        )
    synthetic_result(
        job["job_id"],
        job["package"],
        {
            "evaluations": [
                {
                    "criterion_id": "replication",
                    "verdict": "unevaluated",
                    "rationale": "No ruling adjudicated profitable replication; an unchanged initial board value is not evidence of survival.",
                    "ruling_refs": ["ruling:2:r001"],
                    "board_refs": ["board:2"],
                },
                {
                    "criterion_id": "margin",
                    "verdict": "unevaluated",
                    "rationale": "The scripted game never measured contribution margin after guarantee costs.",
                    "ruling_refs": ["ruling:3:r001"],
                    "board_refs": ["board:3"],
                },
                {
                    "criterion_id": "supply",
                    "verdict": "survived",
                    "rationale": "Seventeen qualified teams remain at the move-two deadline, above the fifteen-team threshold.",
                    "ruling_refs": ["ruling:2:r001"],
                    "board_refs": ["board:2"],
                },
            ],
            "insights": [
                {
                    "id": "capacity",
                    "text": "Concurrent competitor moves draw on a shared, constrained delivery workforce.",
                    "ruling_refs": ["ruling:1:r001", "ruling:2:r001"],
                    "owner": "Fictional strategy owner",
                    "due_date": "2027-02-28",
                    "baseline_matches": ["capacity"],
                }
            ],
            "predictions": predictions,
        },
    )
    run(
        "endgame",
        "--accept-gap",
        "replication",
        "--accept-gap",
        "margin",
        "--by",
        "scripted-teaching-fixture",
        "--note",
        "The teaching game deliberately leaves two criteria unresolved; preserve them as explicit report gaps.",
    )
    run(
        "lookback",
        "open",
        "--by",
        "scripted-teaching-fixture",
        "--note",
        "Fictional future observations for the calibration tutorial",
    )
    registry = run("lookback", "show")["registry"]
    by_source = {p["source"]: p for p in registry}
    reality = json.loads((example / "reality.json").read_text())
    events = []
    for index, observation in enumerate(reality["observations"], 1):
        prediction = by_source[observation["source"]]
        events.append(
            {
                "id": f"event_{index:03d}",
                "statement": prediction["statement"],
                "prediction_ids": [prediction["id"]],
                "status": observation["status"],
                "outcome": observation["outcome"],
                "as_of": reality["as_of"],
                "occurred_on": prediction["due_date"] if observation["outcome"] is True else None,
                "rationale": observation["evidence"],
                "sources": [
                    {
                        "reference": f"fixture:reality.json#/{index - 1}",
                        "description": observation["evidence"],
                    }
                ],
            }
        )
    reality_input = root / "lookback-input.json"
    reality_input.write_text(json.dumps({"events": events}, indent=2) + "\n")
    run(
        "lookback",
        "record",
        "--input",
        str(reality_input),
        "--by",
        "scripted-teaching-fixture",
        "--note",
        "Entirely fictional outcomes; not real observations",
    )
    scored = run("lookback", "score", "--as-of", reality["as_of"])
    calibration = run("report", "lookback")
    report = run("report", "html")
    context_report = run("report", "context")
    run("export", "uncertainties")
    validation = run("workflow", "validate")
    return {
        "project": str(root),
        "phase": run("status")["phase"],
        "validation": validation,
        "fictional": True,
        "inference_calls": 0,
        "closed_rounds": 3,
        "board_applied": True,
        "review_waivers": ["dossiers", "rulings"],
        "human_budget": 0,
        "accepted_criterion_gaps": ["replication", "margin"],
        "prediction_count": len(predictions),
        "report": report["path"],
        "report_context": context_report["path"],
        "lookback_report": calibration["path"],
        "scored_predictions": scored["result"]["summary"]["scored"],
        "brier_score": scored["result"]["summary"]["brier_score"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True, help="New output project directory")
    args = parser.parse_args()
    print(json.dumps(run_demo(args.project.resolve()), indent=2))
