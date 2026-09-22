"""Evidence-linked criterion evaluation, insight ownership, and forecast registration."""

from collections import Counter

from . import adjudication, rounds, workflow
from .models import EndgameAssessment, NoveltyBaseline
from .store import digest, now
from .workflow import require


def require_closed(meta):
    require(
        meta["closed_move"] == meta["decision"]["moves"],
        "E_OPEN_RULINGS",
        "Close every registered round before endgame evaluation.",
    )


def checked_record(store, meta, name):
    record = store.read(f"{name}.json")
    expected = meta.get("endgame_records", {}).get(name)
    require(
        (expected is None and record is None)
        or (record is not None and digest(record) == expected),
        "E_ENDGAME",
        f"{name} is missing, modified, or unregistered.",
    )
    return record


def save_record(store, meta, name, record):
    store.write(f"{name}.json", record)
    meta.setdefault("endgame_records", {})[name] = digest(record)
    store.write("project.json", meta)


def baseline_context(store, meta):
    return {
        "decision": meta["decision"],
        "dossiers": workflow.dossier_snapshot(store)["dossiers"],
        "board": store.read("board/move-0.json"),
    }


def game_context(store, meta):
    moves, rulings, reviews, originals = {}, {}, {}, {}
    for move in range(1, meta["closed_move"] + 1):
        review = rounds.read_review(store, meta, move)
        reviews[str(move)] = review
        for record in store.records(f"moves/move-{move}"):
            moves[f"move:{move}:{record['actor_id']}"] = record
        for ruling_id, record in review["final_rulings"].items():
            rulings[f"ruling:{move}:{ruling_id}"] = record
        for ruling_id, record in rounds.initial_rulings(store, move).items():
            originals[f"ruling:{move}:{ruling_id}"] = record
    return {
        "decision": meta["decision"],
        "criteria": store.read("criteria.json"),
        "actors": store.records("actors"),
        "dossiers": workflow.dossier_snapshot(store)["dossiers"],
        "boards": {
            f"board:{move}": store.read(f"board/move-{move}.json")
            for move in range(meta["closed_move"] + 1)
        },
        "moves": moves,
        "rulings": rulings,
        "initial_rulings": originals,
        "round_reviews": reviews,
        "testimony": adjudication.testimony_records(store, meta),
        "delegations": meta["delegations"],
        "dossier_approval": meta["approvals"].get("dossiers"),
        "novelty_baseline": checked_record(store, meta, "novelty"),
    }


def generate(store, kind, mode="agent"):
    meta = workflow.project(store)
    require_closed(meta)
    require(mode in {"agent", "human"}, "E_INPUT", "Choose agent or human mode.")
    require(kind in {"novelty", "endgame"}, "E_INPUT", "Unknown endgame job kind.")
    existing = meta.get("endgame_jobs", {}).get(kind)
    if existing:
        manifest = workflow.checked_manifest(store, meta, existing)
        require(manifest["mode"] == mode, "E_COMMITTED", "The exported job's mode is fixed.")
        return {"job_id": existing, "package": str(store.path(manifest["package"])), "reused": True}
    require(
        not meta.get("endgame_jobs", {}).get("endgame"),
        "E_ENDGAME_LOCKED",
        "The endgame evidence snapshot is already frozen; the novelty baseline must precede it.",
    )
    workflow.validate(store)
    context = baseline_context(store, meta) if kind == "novelty" else game_context(store, meta)
    if kind == "endgame":
        baseline_job = meta.get("endgame_jobs", {}).get("novelty")
        require(
            not baseline_job or meta["jobs"][baseline_job]["ingested"],
            "E_BASELINE_PENDING",
            "Ingest the pending novelty baseline before generating endgame evaluation.",
        )
    schema = NoveltyBaseline if kind == "novelty" else EndgameAssessment
    actor = {"id": "scribe", "name": "Neutral endgame scribe", "role": "scribe", "mode": mode}
    job = rounds.export_job(
        store,
        meta,
        kind,
        actor,
        0 if kind == "novelty" else meta["closed_move"],
        context,
        schema.model_json_schema(),
    )
    meta.setdefault("endgame_jobs", {})[kind] = job["job_id"]
    store.write("project.json", meta)
    return job


def unique(items, label):
    require(len(items) == len(set(items)), "E_ENDGAME", f"Duplicate {label}.")


def validate_assessment(answer, context):
    answer = EndgameAssessment.model_validate(answer).model_dump()
    evaluations = answer["evaluations"]
    ids = [e["criterion_id"] for e in evaluations]
    unique(ids, "criterion evaluations")
    criteria = {c["id"]: c for c in context["criteria"]}
    require(
        set(ids) == set(criteria),
        "E_UNEVALUATED_CRITERIA",
        "Every registered criterion needs a verdict, including explicit unevaluated gaps.",
    )
    for evaluation in evaluations:
        unique(evaluation["ruling_refs"], "ruling citations")
        unique(evaluation["board_refs"], "board citations")
        require(
            set(evaluation["ruling_refs"]) <= context["rulings"].keys()
            and set(evaluation["board_refs"]) <= context["boards"].keys(),
            "E_CITATION",
            "Evaluation references an unknown final ruling or board.",
        )
        deadline = criteria[evaluation["criterion_id"]]["deadline_move"]
        require(
            all(
                int(ref.split(":")[1]) <= deadline
                for ref in evaluation["ruling_refs"] + evaluation["board_refs"]
            ),
            "E_CITATION",
            "A criterion cannot be evaluated using outcomes after its registered deadline.",
        )
        if evaluation["verdict"] != "unevaluated":
            require(
                bool(evaluation["ruling_refs"]) and bool(evaluation["board_refs"]),
                "E_CITATION",
                "A verdict needs both final-ruling and board citations.",
            )
            if evaluation["verdict"] in {"survived", "grazed"}:
                require(
                    f"board:{deadline}" in evaluation["board_refs"],
                    "E_CITATION",
                    "A survived or grazed verdict must cite the criterion's deadline board.",
                )
    unique([i["id"] for i in answer["insights"]], "insight ids")
    baseline = context["novelty_baseline"]
    finding_ids = {f["id"] for f in baseline["answer"]["findings"]} if baseline else set()
    for insight in answer["insights"]:
        require(
            set(insight["ruling_refs"]) <= context["rulings"].keys(),
            "E_CITATION",
            "Insight cites an unknown final ruling.",
        )
        unique(insight["ruling_refs"], "insight citations")
        unique(insight["baseline_matches"], "baseline matches")
        require(
            set(insight["baseline_matches"]) <= finding_ids,
            "E_NOVELTY",
            "Insight cites an unknown baseline finding.",
        )
    sources = [p["source"] for p in answer["predictions"]]
    unique(sources, "prediction sources")
    require(
        set(sources) == context["moves"].keys() | context["rulings"].keys(),
        "E_PREDICTIONS",
        "Register exactly one dated, resolvable prediction for every committed move and final ruling.",
    )
    return answer


def ingest(store, kind, job_id, source, human=False):
    meta = workflow.project(store)
    require_closed(meta)
    manifest = workflow.checked_manifest(store, meta, job_id)
    require(
        kind in {"novelty", "endgame"}
        and manifest["kind"] == kind
        and meta.get("endgame_jobs", {}).get(kind) == job_id,
        "E_LINEAGE",
        "This is not the registered evaluation job.",
    )
    require(
        not meta["jobs"][job_id]["ingested"],
        "E_COMMITTED",
        "This evaluation job already has an answer.",
    )
    require(
        not checked_record(store, meta, "endgame"),
        "E_ENDGAME_LOCKED",
        "Endgame is already finalized.",
    )
    if kind == "novelty":
        require(
            not meta.get("endgame_jobs", {}).get("endgame"),
            "E_ENDGAME_LOCKED",
            "Endgame already froze its baseline.",
        )
    answer, provenance = workflow.read_answer(meta, manifest, source, human)
    if kind == "novelty":
        answer = NoveltyBaseline.model_validate(answer).model_dump()
        unique([f["id"] for f in answer["findings"]], "baseline finding ids")
    else:
        answer = validate_assessment(answer, manifest["context"])
    provenance = rounds.provenance_for(meta, manifest, source, provenance, answer)
    record = {
        "answer": answer,
        "provenance": provenance,
        "context_hash": digest(manifest["context"]),
    }
    meta["jobs"][job_id]["ingested"] = True
    save_record(store, meta, "novelty" if kind == "novelty" else "endgame_assessment", record)
    return {"kind": kind, "provenance": provenance, "finalized": False}


def compile_insights(assessment, context):
    return [
        {
            **insight,
            "novelty": (
                "unassessed"
                if context["novelty_baseline"] is None
                else "derivable"
                if insight["baseline_matches"]
                else "emergent"
            ),
            "provenance": assessment["provenance"],
            "source_provenance": {
                ref: context["rulings"][ref]["provenance"] for ref in insight["ruling_refs"]
            },
        }
        for insight in assessment["answer"]["insights"]
    ]


def revise(store, source, payload, reviewer, note):
    meta = workflow.project(store)
    require_closed(meta)
    require(
        not checked_record(store, meta, "endgame"),
        "E_ENDGAME_LOCKED",
        "Finalized endgame cannot be revised.",
    )
    require(
        bool(reviewer.strip()) and bool(note.strip()),
        "E_INPUT",
        "A human revision requires author and rationale.",
    )
    workflow.validate(store)
    original = checked_record(store, meta, "endgame_assessment")
    require(
        original is not None, "E_UNEVALUATED_CRITERIA", "Ingest an evaluation before revising it."
    )
    context = game_context(store, meta)
    answer = validate_assessment(payload, context)
    manifest = workflow.checked_manifest(store, meta, meta["endgame_jobs"]["endgame"])
    provenance = rounds.provenance_for(
        meta,
        manifest,
        source,
        {
            "origin": "human",
            "model": "human",
            "participant_id": reviewer.strip(),
            "operation": "human_revision",
            "rationale": note.strip(),
        },
        answer,
    )
    record = {
        "answer": answer,
        "provenance": provenance,
        "context_hash": digest(context),
        "history": [
            *original.get("history", []),
            {"answer": original["answer"], "provenance": original["provenance"]},
        ],
    }
    save_record(store, meta, "endgame_assessment", record)
    return {"provenance": provenance, "revision_count": len(record["history"])}


def show(store):
    workflow.validate(store)
    meta = workflow.project(store)
    assessment = checked_record(store, meta, "endgame_assessment")
    return {
        "assessment": assessment,
        "finalized": checked_record(store, meta, "endgame"),
        "unevaluated_criteria": [
            e["criterion_id"]
            for e in assessment["answer"]["evaluations"]
            if e["verdict"] == "unevaluated"
        ]
        if assessment
        else [],
        "unowned_insights": [
            i["id"]
            for i in assessment["answer"]["insights"]
            if i["owner"] is None or i["due_date"] is None
        ]
        if assessment
        else [],
    }


def compile_predictions(assessment, context):
    result = []
    for index, prediction in enumerate(
        sorted(assessment["answer"]["predictions"], key=lambda p: p["source"]), 1
    ):
        source = prediction["source"]
        is_move = source in context["moves"]
        record = (context["moves"] if is_move else context["rulings"])[source]
        statement = (
            "; ".join(record["content"]["actions"])
            if is_move
            else record["content"]["outcome"]["description"]
        )
        result.append(
            {
                **prediction,
                "id": f"p{index:03d}",
                "statement": statement,
                "population": "team_moves"
                if is_move
                else "human_rulings"
                if record["provenance"]["origin"] == "human"
                else "agent_rulings",
                "basis": None if is_move else record["content"]["basis"],
                "predicted_by": record["actor_id"] if is_move else "control",
                "source_provenance": record["provenance"],
                "registration_provenance": assessment["provenance"],
                "outcome": None,
            }
        )
    return result


def health(store, meta, assessment, context, insights, waivers):
    warnings = rounds.health_warnings(store, meta)
    actor_models = {
        a["id"]: sorted(
            {
                r["provenance"]["model"]
                for r in context["moves"].values()
                if r["actor_id"] == a["id"]
            }
        )
        for a in context["actors"]
    }
    simulation_models = {
        model for models in actor_models.values() for model in models if model != "human"
    }
    simulated_actors = sum(
        any(model != "human" for model in models) for models in actor_models.values()
    )
    control_models = sorted({r["provenance"]["model"] for r in context["rulings"].values()})
    if simulated_actors >= 2 and len(simulation_models) == 1:
        warnings.append(
            {"code": "W_SAME_MODEL", "message": "All simulated actors used the same model."}
        )
    if simulation_models & set(control_models):
        warnings.append(
            {
                "code": "W_CONTROL_MODEL_OVERLAP",
                "message": "Control and at least one actor used the same model.",
            }
        )
    if not any(
        e["verdict"] in {"triggered", "grazed"} for e in assessment["answer"]["evaluations"]
    ):
        warnings.append(
            {
                "code": "W_HOME_COMFORT",
                "message": "No registered criterion triggered or grazed; inspect whether the game challenged the strategy.",
            }
        )
    if context["novelty_baseline"] is None:
        warnings.append(
            {
                "code": "W_NOVELTY_UNASSESSED",
                "message": "No inputs-only baseline was supplied; insight novelty is unassessed.",
            }
        )
    elif not any(i["novelty"] == "emergent" for i in insights):
        warnings.append(
            {
                "code": "W_NO_EMERGENT_INSIGHTS",
                "message": "No insight was marked emergent relative to the supplied baseline.",
            }
        )
    if waivers["accepted_gaps"]:
        warnings.append(
            {
                "code": "W_ACCEPTED_GAPS",
                "message": "Unevaluated criteria explicitly accepted: "
                + ", ".join(waivers["accepted_gaps"]),
            }
        )
    if waivers["unowned_insights"]:
        warnings.append(
            {
                "code": "W_INSIGHT_OWNERSHIP_WAIVED",
                "message": "Missing insight owners or dates explicitly waived: "
                + ", ".join(waivers["unowned_insights"]),
            }
        )
    return {
        "warnings": warnings,
        "basis_distribution": dict(
            Counter(r["content"]["basis"] for r in context["rulings"].values())
        ),
        "actor_models": actor_models,
        "control_models": control_models,
        "novelty_distribution": dict(Counter(i["novelty"] for i in insights)),
        "escalation_debt": {
            key: review["closure"]["escalation_debt"]
            for key, review in context["round_reviews"].items()
        },
        "novelty_method": "inputs-only baseline matched by the evaluator; heuristic, not proof",
    }


def finalize(store, accepted_gaps=(), unowned_insights=(), reviewer=None, note=None):
    meta = workflow.project(store)
    require_closed(meta)
    existing = checked_record(store, meta, "endgame")
    if existing:
        require(
            not accepted_gaps and not unowned_insights and reviewer is None and note is None,
            "E_ENDGAME_LOCKED",
            "Finalized endgame cannot be amended by supplying new waivers.",
        )
        audit(store, meta)
        return existing
    workflow.validate(store)
    assessment = checked_record(store, meta, "endgame_assessment")
    require(
        assessment is not None,
        "E_UNEVALUATED_CRITERIA",
        "Generate and ingest the endgame evaluation before finalizing.",
    )
    context = game_context(store, meta)
    validate_assessment(assessment["answer"], context)
    require(
        digest(context) == assessment["context_hash"],
        "E_ENDGAME",
        "The evaluation evidence snapshot changed.",
    )
    gaps = {
        e["criterion_id"]
        for e in assessment["answer"]["evaluations"]
        if e["verdict"] == "unevaluated"
    }
    unowned = {
        i["id"]
        for i in assessment["answer"]["insights"]
        if i["owner"] is None or i["due_date"] is None
    }
    require(
        set(accepted_gaps) <= gaps and set(unowned_insights) <= unowned,
        "E_WAIVER",
        "Waivers must name actual unevaluated criteria or insights missing ownership/date.",
    )
    require(
        gaps <= set(accepted_gaps),
        "E_UNEVALUATED_CRITERIA",
        "Unevaluated criteria block finalization: " + ", ".join(sorted(gaps - set(accepted_gaps))),
        "Complete the assessment before ingestion, or explicitly accept each recorded gap with --accept-gap ID --by NAME --note REASON.",
    )
    require(
        unowned <= set(unowned_insights),
        "E_UNOWNED_INSIGHTS",
        "Insight owners and due dates are missing: "
        + ", ".join(sorted(unowned - set(unowned_insights))),
        "Supply ownership before ingestion, or explicitly waive each missing assignment with --waive-insight ID --by NAME --note REASON.",
    )
    if gaps or unowned:
        require(
            bool(reviewer and reviewer.strip()) and bool(note and note.strip()),
            "E_WAIVER",
            "Gap and ownership waivers require human attribution and rationale.",
        )
    else:
        require(
            reviewer is None and note is None,
            "E_WAIVER",
            "--by and --note apply only to explicit endgame waivers.",
        )
    require(
        store.read("insights.json") == [] and store.read("predictions.json") == [],
        "E_ENDGAME",
        "Unregistered insight or prediction data would be overwritten.",
    )
    waivers = {
        "accepted_gaps": sorted(gaps),
        "unowned_insights": sorted(unowned),
        "reviewer": reviewer.strip() if reviewer else None,
        "note": note.strip() if note else None,
    }
    insights = compile_insights(assessment, context)
    predictions = compile_predictions(assessment, context)
    record = {
        "finalized_at": now(),
        "context_hash": digest(context),
        "assessment_hash": digest(assessment),
        "insights_hash": digest(insights),
        "predictions_hash": digest(predictions),
        "waivers": waivers,
        "health": health(store, meta, assessment, context, insights, waivers),
    }
    store.write("insights.json", insights)
    store.write("predictions.json", predictions)
    save_record(store, meta, "endgame", record)
    return record


def audit(store, meta):
    baseline = checked_record(store, meta, "novelty")
    assessment = checked_record(store, meta, "endgame_assessment")
    final = checked_record(store, meta, "endgame")
    for kind, record in (("novelty", baseline), ("endgame", assessment)):
        job_id = meta.get("endgame_jobs", {}).get(kind)
        if job_id:
            manifest = workflow.checked_manifest(store, meta, job_id)
            require(
                manifest["kind"] == kind
                and meta["jobs"][job_id]["ingested"] == (record is not None),
                "E_ENDGAME",
                "Evaluation job and ingestion state disagree.",
            )
        require(record is None or job_id is not None, "E_ENDGAME", "Evaluation has no job lineage.")
        if record:
            expected = (
                baseline_context(store, meta) if kind == "novelty" else game_context(store, meta)
            )
            require(
                record["provenance"]["job_id"] == job_id
                and digest(expected) == record["context_hash"]
                and digest(record["answer"]) == record["provenance"]["answer_hash"],
                "E_ENDGAME",
                "Evaluation content, provenance, or evidence snapshot changed.",
            )
    if final:
        require_closed(meta)
        require(
            assessment is not None and digest(assessment) == final["assessment_hash"],
            "E_ENDGAME",
            "Final assessment is missing or modified.",
        )
        context = game_context(store, meta)
        require(
            digest(context) == final["context_hash"], "E_ENDGAME", "Final report evidence changed."
        )
        insights, predictions = (
            compile_insights(assessment, context),
            compile_predictions(assessment, context),
        )
        require(
            store.read("insights.json") == insights
            and digest(insights) == final["insights_hash"]
            and store.read("predictions.json") == predictions
            and digest(predictions) == final["predictions_hash"],
            "E_ENDGAME",
            "Compiled insights or prediction registry changed.",
        )
