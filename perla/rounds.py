"""Routing, one rebuttal round, attributed review, and deterministic board closure."""

from collections import Counter
from pathlib import Path
from uuid import uuid4

from . import adjudication, edsl_adapter, workflow
from .models import BoardChoices, EscalationPolicy, RebuttalBatch, ResolutionBatch, RoutingOptions
from .store import digest, now
from .workflow import require


def read_review(store, meta, move):
    review = store.read(f"review/move-{move}.json")
    expected = meta.get("reviews", {}).get(str(move))
    require(
        (review is None and expected is None)
        or (review is not None and digest(review) == expected),
        "E_REVIEW",
        "Round review state is missing or modified.",
    )
    return review


def save_review(store, meta, review, action, attribution):
    review.setdefault("events", []).append(
        {"action": action, "attribution": attribution, "recorded_at": now()}
    )
    move = review["move"]
    store.write(f"review/move-{move}.json", review)
    meta.setdefault("reviews", {})[str(move)] = digest(review)
    store.write("project.json", meta)


def open_review(store, move, required=True):
    meta = workflow.project(store)
    require(
        move == meta["closed_move"] + 1,
        "E_ROUND_CLOSED",
        "Only the current open round can be changed.",
    )
    manifest = adjudication.audit_round(store, meta, move)
    require(
        meta["jobs"][manifest["id"]]["ingested"], "E_OPEN_RULINGS", "Ingest initial rulings first."
    )
    review = read_review(store, meta, move)
    if required:
        require(review is not None, "E_ROUTING", "Route the round before generating rebuttals.")
    if review:
        audit_review(store, meta, review)
    return meta, manifest, review


def initial_rulings(store, move):
    return {r["content"]["ruling_id"]: r for r in store.records(f"rulings/move-{move}")}


def final_rulings(store, review):
    return review.get("final_rulings") or initial_rulings(store, review["move"])


def set_policy(store, payload):
    meta = workflow.project(store)
    policy = EscalationPolicy.model_validate(payload).model_dump()
    meta["escalation_policy"] = policy
    store.write("project.json", meta)
    return {"policy": policy, "applies_to": "rounds not yet routed"}


def make_routing(records, policy, options):
    candidates, routes = [], {}
    for ruling_id, record in records.items():
        content = record["content"]
        reasons = []
        if content["basis"] == "judgment" or any(
            link["basis"] == "judgment" for link in content["chain"]
        ):
            reasons.append("judgment")
        if content["kill_criteria"]:
            reasons.append("kill_criterion")
        if content.get("branches"):
            reasons.append("declared_branch")
        if record.get("revised"):
            reasons.append("revised")
        if ruling_id in options["flagged"]:
            reasons.append("user_flagged")
        impact = options["impact_scores"].get(
            ruling_id, float(len(content["outcome"]["board_delta"]))
        )
        route = {
            "reasons": reasons,
            "content_hash": digest(content),
            "impact_score": impact,
            "blocking": False,
            "over_budget": False,
            "assessment": None,
        }
        if reasons:
            candidates.append((not bool(content["kill_criteria"]), -impact, ruling_id))
            route["route"] = "escalated"
        elif (
            content["basis"] == "dossier_fact"
            and content["outcome"]["confidence"] >= policy["auto_confidence"]
        ):
            route["route"] = "auto_eligible"
        else:
            route["route"] = "review"
        routes[ruling_id] = route
    for index, (_, _, ruling_id) in enumerate(sorted(candidates)):
        route = routes[ruling_id]
        if index >= policy["human_budget"]:
            route.update(route="provisional", over_budget=True)
        else:
            route["blocking"] = bool(records[ruling_id]["content"]["kill_criteria"])
            if not route["blocking"]:
                route["route"] = "provisional"
    return routes


def route_round(store, move, payload=None):
    meta, manifest, review = open_review(store, move, required=False)
    if review:
        if payload is not None:
            require(
                RoutingOptions.model_validate(payload).model_dump() == review["options"],
                "E_ROUTING",
                "Routing options are fixed for this round.",
            )
        return {
            "move": move,
            "policy": review["policy"],
            "routing": review["routing"],
            "reused": True,
        }
    options = RoutingOptions.model_validate(payload or {}).model_dump()
    records = initial_rulings(store, move)
    require(
        set(options["flagged"]) <= records.keys()
        and options["impact_scores"].keys() <= records.keys(),
        "E_RULING_REFERENCE",
        "Routing options name an unknown ruling.",
    )
    policy = EscalationPolicy.model_validate(meta.get("escalation_policy", {})).model_dump()
    review = {
        "move": move,
        "control_job": manifest["id"],
        "policy": policy,
        "options": options,
        "routing": make_routing(records, policy, options),
        "rebuttal_jobs": {},
        "rebuttals": {},
        "resolution_job": None,
        "resolutions": None,
        "final_rulings": None,
        "board_choices": {},
        "approval": None,
        "closed_at": None,
    }
    save_review(store, meta, review, "route", "operator")
    return {"move": move, "policy": policy, "routing": review["routing"], "reused": False}


def export_job(store, meta, kind, actor, move, context, schema):
    job_id = "job-" + uuid4().hex
    manifest = {
        "id": job_id,
        "project_id": meta["id"],
        "kind": kind,
        "actor_id": actor["id"],
        "actor_name": actor["name"],
        "actor_role": actor["role"],
        "mode": actor["mode"],
        "move": move,
        "created_at": now(),
        "context": context,
        "response_schema": schema,
        "included": sorted(context),
        "package": f"jobs/{job_id}.jobs.ep",
    }
    path = store.path(manifest["package"])
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["job_hash"] = digest(edsl_adapter.save_job(manifest, path))
    manifest_path = f"jobs/{job_id}.manifest.json"
    store.write(manifest_path, manifest)
    meta["jobs"][job_id] = {
        "id": job_id,
        "kind": kind,
        "actor_id": actor["id"],
        "move": move,
        "manifest": manifest_path,
        "manifest_hash": digest(manifest),
        "ingested": False,
    }
    return {"job_id": job_id, "actor_id": actor["id"], "package": str(path), "reused": False}


def rebuttal_context(store, meta, actor_id, move):
    records = initial_rulings(store, move)
    return {
        "decision": meta["decision"],
        "board": store.read(f"board/move-{move - 1}.json"),
        "dossier": store.read(f"dossiers/{actor_id}.json"),
        "rulings": {
            key: r["content"]
            for key, r in records.items()
            if actor_id in r["content"]["affected_actors"]
        },
    }


def generate_rebuttals(store, move, actor_id=None):
    meta, _, review = open_review(store, move)
    actors = [workflow.actor(store, actor_id)] if actor_id else store.records("actors")
    jobs = []
    for actor in actors:
        context = rebuttal_context(store, meta, actor["id"], move)
        if not context["rulings"]:
            continue
        existing = review["rebuttal_jobs"].get(actor["id"])
        if existing:
            manifest = workflow.checked_manifest(store, meta, existing)
            jobs.append(
                {
                    "job_id": existing,
                    "actor_id": actor["id"],
                    "package": str(store.path(manifest["package"])),
                    "reused": True,
                }
            )
        else:
            job = export_job(
                store, meta, "rebuttals", actor, move, context, RebuttalBatch.model_json_schema()
            )
            review["rebuttal_jobs"][actor["id"]] = job["job_id"]
            jobs.append(job)
    save_review(store, meta, review, "generate_rebuttals", "operator")
    return {"jobs": jobs}


def provenance_for(meta, manifest, source, provenance, answer):
    return {
        **provenance,
        "source_package": str(Path(source).resolve()),
        "job_id": manifest["id"],
        "manifest_hash": meta["jobs"][manifest["id"]]["manifest_hash"],
        "ingested_at": now(),
        "answer_hash": digest(answer),
    }


def ingest_rebuttals(store, job_id, source, human=False):
    meta = workflow.project(store)
    manifest = workflow.checked_manifest(store, meta, job_id)
    require(manifest["kind"] == "rebuttals", "E_LINEAGE", "Expected an actor rebuttal job.")
    meta, _, review = open_review(store, manifest["move"])
    actor_id = manifest["actor_id"]
    require(
        review["rebuttal_jobs"].get(actor_id) == job_id,
        "E_LINEAGE",
        "This job is not the actor's rebuttal job.",
    )
    require(
        actor_id not in review["rebuttals"] and not meta["jobs"][job_id]["ingested"],
        "E_COMMITTED",
        "Each actor may submit only one rebuttal per ruling.",
    )
    answer, provenance = workflow.read_answer(meta, manifest, source, human)
    answer = RebuttalBatch.model_validate(answer).model_dump()
    expected = manifest["context"]["rulings"]
    ids = [r["ruling_id"] for r in answer["rebuttals"]]
    require(
        set(ids) == set(expected) and len(ids) == len(set(ids)),
        "E_REBUTTAL",
        "Submit exactly one acceptance or challenge for every assigned ruling.",
    )
    facts = {f"d.{actor_id}.{f['id']}" for f in manifest["context"]["dossier"]["facts"]}
    for rebuttal in answer["rebuttals"]:
        if rebuttal["stance"] == "challenge":
            links = {link["link"] for link in expected[rebuttal["ruling_id"]]["chain"]}
            require(
                rebuttal["link"] in links and set(rebuttal["cite"]) <= facts,
                "E_REBUTTAL",
                "Challenge a named link using only your actor's dossier fact ids.",
            )
    provenance = provenance_for(meta, manifest, source, provenance, answer)
    review["rebuttals"][actor_id] = {"answer": answer, "provenance": provenance}
    meta["jobs"][job_id]["ingested"] = True
    save_review(store, meta, review, "ingest_rebuttals", provenance)
    return {"actor_id": actor_id, "move": manifest["move"], "provenance": provenance}


def expected_actors(store, move):
    return {
        actor
        for r in initial_rulings(store, move).values()
        for actor in r["content"]["affected_actors"]
    }


def resolution_context(store, control_manifest, review):
    return {
        "control": control_manifest["context"],
        "rulings": {key: r["content"] for key, r in initial_rulings(store, review["move"]).items()},
        "rebuttals": review["rebuttals"],
    }


def generate_resolution(store, move):
    meta, control, review = open_review(store, move)
    require(
        set(review["rebuttals"]) == expected_actors(store, move),
        "E_REBUTTALS_PENDING",
        "Every affected actor must respond before control resolves the round.",
    )
    if review["resolution_job"]:
        manifest = workflow.checked_manifest(store, meta, review["resolution_job"])
        return {
            "job_id": manifest["id"],
            "package": str(store.path(manifest["package"])),
            "reused": True,
        }
    actor = {"id": "control", "name": "Neutral control", "role": "control", "mode": control["mode"]}
    job = export_job(
        store,
        meta,
        "resolutions",
        actor,
        move,
        resolution_context(store, control, review),
        ResolutionBatch.model_json_schema(),
    )
    review["resolution_job"] = job["job_id"]
    save_review(store, meta, review, "generate_resolution", "operator")
    return job


def ingest_resolutions(store, job_id, source, human=False):
    meta = workflow.project(store)
    manifest = workflow.checked_manifest(store, meta, job_id)
    require(manifest["kind"] == "resolutions", "E_LINEAGE", "Expected a control resolution job.")
    meta, control, review = open_review(store, manifest["move"])
    require(
        review["resolution_job"] == job_id, "E_LINEAGE", "This is not the round's resolution job."
    )
    require(
        review["resolutions"] is None and not meta["jobs"][job_id]["ingested"],
        "E_COMMITTED",
        "Control has already resolved this rebuttal round.",
    )
    answer, provenance = workflow.read_answer(meta, manifest, source, human)
    answer = ResolutionBatch.model_validate(answer).model_dump()
    initial = initial_rulings(store, manifest["move"])
    ids = [r["ruling_id"] for r in answer["resolutions"]]
    require(
        set(ids) == set(initial) and len(ids) == len(set(ids)),
        "E_RESOLUTION",
        "Control must resolve every ruling exactly once.",
    )
    provenance = provenance_for(meta, manifest, source, provenance, answer)
    finals = {}
    for resolution in answer["resolutions"]:
        ruling_id = resolution["ruling_id"]
        original = initial[ruling_id]["content"]
        responders = [r["actor_id"] for r in resolution["responses"]]
        require(
            set(responders) == set(original["affected_actors"])
            and len(responders) == len(set(responders)),
            "E_RESOLUTION",
            "Control must respond with reasons to every affected actor.",
        )
        current = resolution["revision"] if resolution["decision"] == "revise" else original
        require(
            current["ruling_id"] == ruling_id
            and set(current["resolves"]) == set(original["resolves"])
            and set(current["affected_actors"]) == set(original["affected_actors"])
            and set(current["kill_criteria"]) >= set(original["kill_criteria"]),
            "E_RESOLUTION",
            "Revisions must preserve ruling identity, action/actor scope, and existing criterion links.",
        )
        finals[ruling_id] = {
            "content": current,
            "content_hash": digest(current),
            "move": manifest["move"],
            "revised": resolution["decision"] == "revise",
            "resolution": resolution,
            "provenance": provenance,
            "original_provenance": initial[ruling_id]["provenance"],
            "resolution_mode": "human" if provenance["origin"] == "human" else "agent",
        }
    adjudication.validate_batch(
        {"rulings": [r["content"] for r in finals.values()]}, control["context"]
    )
    review["initial_routing"] = review["routing"]
    review["final_rulings"] = finals
    review["resolutions"] = {"answer": answer, "provenance": provenance}
    review["routing"] = make_routing(finals, review["policy"], review["options"])
    meta["jobs"][job_id]["ingested"] = True
    save_review(store, meta, review, "ingest_resolutions", provenance)
    return {
        "move": manifest["move"],
        "ruling_ids": list(finals),
        "routing": review["routing"],
        "provenance": provenance,
    }


def require_resolved(review):
    require(
        review["resolutions"] is not None,
        "E_OPEN_RULINGS",
        "Resolve all rebuttals before review or closure.",
    )


def assess(store, move, ruling_id, reviewer, note):
    meta, _, review = open_review(store, move)
    require_resolved(review)
    require(
        bool(reviewer.strip()) and bool(note.strip()),
        "E_APPROVAL",
        "Record the human reviewer's name and reasoning.",
    )
    require(ruling_id in review["routing"], "E_RULING_REFERENCE", "Unknown ruling.")
    route = review["routing"][ruling_id]
    require(
        route["assessment"] is None, "E_COMMITTED", "This ruling already has a human assessment."
    )
    route["assessment"] = {
        "reviewer": reviewer.strip(),
        "note": note.strip(),
        "recorded_at": now(),
        "content_hash": route["content_hash"],
        "origin": "human",
        "method": "individual_review",
    }
    review["approval"] = None
    save_review(store, meta, review, "assess", reviewer.strip())
    return {"ruling_id": ruling_id, "assessment": route["assessment"]}


def board_proposal(store, review):
    records = final_rulings(store, review)
    values = {}
    for ruling_id, record in records.items():
        for key, value in record["content"]["outcome"]["board_delta"].items():
            values.setdefault(key, {})[ruling_id] = value
    conflicts = {
        key: candidates
        for key, candidates in values.items()
        if len({digest(value) for value in candidates.values()}) > 1
    }
    choices = review["board_choices"]
    require(
        set(choices) <= set(conflicts),
        "E_BOARD_CONFLICT",
        "Board choices refer to nonconflicting fields.",
    )
    board = dict(store.read(f"board/move-{review['move'] - 1}.json"))
    unresolved = {}
    for key, candidates in values.items():
        if key not in conflicts:
            board[key] = next(iter(candidates.values()))
        elif key in choices:
            winner = choices[key]["ruling_id"]
            require(
                winner in candidates,
                "E_BOARD_CONFLICT",
                "Chosen ruling does not set the conflicting field.",
            )
            board[key] = candidates[winner]
        else:
            unresolved[key] = candidates
    return board, unresolved


def reconcile(store, move, payload, reviewer):
    meta, _, review = open_review(store, move)
    require_resolved(review)
    require(
        bool(reviewer.strip()), "E_APPROVAL", "Record who chose between conflicting board outcomes."
    )
    choices = BoardChoices.model_validate(payload).model_dump()["choices"]
    probe = {**review, "board_choices": {}}
    _, conflicts = board_proposal(store, probe)
    require(
        set(choices) == set(conflicts),
        "E_BOARD_CONFLICT",
        "Choose a ruling for every conflicting field, and no other fields.",
    )
    review["board_choices"] = choices
    board_proposal(store, review)
    review["approval"] = None
    save_review(store, meta, review, "reconcile_board", reviewer.strip())
    return {"move": move, "choices": choices}


def review_snapshot(store, review):
    require_resolved(review)
    blocking = [
        key
        for key, route in review["routing"].items()
        if route["blocking"] and route["assessment"] is None
    ]
    require(
        not blocking,
        "E_OPEN_RULINGS",
        f"Blocking human assessments remain: {', '.join(blocking)}.",
        "Present these rulings for review, then record each with adjudicate assess.",
    )
    board, conflicts = board_proposal(store, review)
    require(
        not conflicts,
        "E_BOARD_CONFLICT",
        f"Conflicting board fields require reconciliation: {', '.join(conflicts)}.",
        "Use adjudicate reconcile to record a chosen ruling and reason for each conflicting field.",
    )
    return {
        "final_rulings": review["final_rulings"],
        "resolutions": review["resolutions"],
        "routing": review["routing"],
        "policy": review["policy"],
        "board_choices": review["board_choices"],
        "board": board,
    }


def approve(store, move, reviewer, note):
    meta, _, review = open_review(store, move)
    require(
        bool(reviewer.strip()) and bool(note.strip()),
        "E_APPROVAL",
        "Record a reviewer and review rationale.",
    )
    snapshot = review_snapshot(store, review)
    review["approval"] = {
        "reviewer": reviewer.strip(),
        "note": note.strip(),
        "recorded_at": now(),
        "snapshot_hash": digest(snapshot),
    }
    save_review(store, meta, review, "approve_rulings", reviewer.strip())
    return review["approval"]


def approved(meta, review, snapshot):
    return any(d["checkpoint"] == "rulings" for d in meta["delegations"]) or (
        review["approval"] is not None and review["approval"]["snapshot_hash"] == digest(snapshot)
    )


def close_round(store, move):
    meta, _, review = open_review(store, move)
    snapshot = review_snapshot(store, review)
    require(
        approved(meta, review, snapshot),
        "E_APPROVAL",
        "The final rulings and proposed board require recorded approval.",
        "Present adjudicate show, then workflow approve rulings --move N --by NAME --note NOTE.",
    )
    require(
        not store.path(f"board/move-{move}.json").exists(),
        "E_STATE",
        "The next board version already exists.",
    )
    review["closed_at"] = now()
    review["closure"] = {
        "snapshot_hash": digest(snapshot),
        "board_hash": digest(snapshot["board"]),
        "approval": review["approval"],
        "delegated": any(d["checkpoint"] == "rulings" for d in meta["delegations"]),
        "escalation_debt": [
            key
            for key, route in review["routing"].items()
            if route["reasons"] and route["assessment"] is None
        ],
    }
    store.write(f"board/move-{move}.json", snapshot["board"])
    meta["closed_move"] = move
    save_review(store, meta, review, "close_round", "operator")
    return {
        "move": move,
        "board": snapshot["board"],
        "closed_at": review["closed_at"],
        "escalation_debt": review["closure"]["escalation_debt"],
    }


def phase(store, meta, move):
    review = read_review(store, meta, move)
    if review is None:
        return "adjudication_routing"
    if review["closed_at"]:
        return "closed"
    if set(review["rebuttal_jobs"]) != expected_actors(store, move):
        return "rebuttal_generation"
    if set(review["rebuttals"]) != expected_actors(store, move):
        return "awaiting_rebuttals"
    if review["resolution_job"] is None:
        return "control_resolution"
    if review["resolutions"] is None:
        return "awaiting_resolution"
    if any(
        route["blocking"] and route["assessment"] is None for route in review["routing"].values()
    ):
        return "escalation_review"
    _, conflicts = board_proposal(store, review)
    if conflicts:
        return "board_reconciliation"
    return (
        "ready_to_close"
        if approved(meta, review, review_snapshot(store, review))
        else "ruling_review"
    )


def audit_boards(store, meta):
    """Cheap deterministic check used before new actor jobs read a closed board."""
    for move in range(1, meta["closed_move"] + 1):
        review = read_review(store, meta, move)
        require(
            review is not None and review["closed_at"] is not None,
            "E_REVIEW",
            "Closed round review is missing.",
        )
        snapshot = review_snapshot(store, review)
        require(
            approved(meta, review, snapshot)
            and digest(snapshot) == review["closure"]["snapshot_hash"],
            "E_APPROVAL",
            "Closed round differs from its approved snapshot.",
        )
        board = store.read(f"board/move-{move}.json")
        require(
            board == snapshot["board"] and digest(board) == review["closure"]["board_hash"],
            "E_BOARD",
            "A closed board no longer matches the approved ruling outcomes.",
        )


def audit_review(store, meta, review):
    move = review["move"]
    require(
        set(review["rebuttals"]) <= set(review["rebuttal_jobs"]),
        "E_REVIEW",
        "Rebuttal job lineage is incomplete.",
    )
    for actor_id, job_id in review["rebuttal_jobs"].items():
        manifest = workflow.checked_manifest(store, meta, job_id)
        require(
            manifest["kind"] == "rebuttals"
            and manifest["actor_id"] == actor_id
            and manifest["move"] == move,
            "E_LINEAGE",
            "Rebuttal job has invalid role or round lineage.",
        )
        answer = review["rebuttals"].get(actor_id)
        require(
            meta["jobs"][job_id]["ingested"] == (answer is not None),
            "E_REVIEW",
            "Rebuttal ingestion record is inconsistent.",
        )
        if answer:
            require(
                answer["provenance"]["job_id"] == job_id
                and digest(answer["answer"]) == answer["provenance"]["answer_hash"],
                "E_LINEAGE",
                "Rebuttal content or provenance changed.",
            )
    if review["resolution_job"]:
        manifest = workflow.checked_manifest(store, meta, review["resolution_job"])
        require(
            manifest["kind"] == "resolutions" and manifest["move"] == move,
            "E_LINEAGE",
            "Resolution job lineage is invalid.",
        )
        require(
            set(review["rebuttals"]) == expected_actors(store, move),
            "E_REBUTTALS_PENDING",
            "Resolution lacks actor responses.",
        )
        require(
            meta["jobs"][manifest["id"]]["ingested"] == (review["resolutions"] is not None),
            "E_REVIEW",
            "Resolution ingestion record is inconsistent.",
        )
    if review["resolutions"]:
        require(
            review["resolution_job"] is not None and review["final_rulings"] is not None,
            "E_REVIEW",
            "Control resolution state is incomplete.",
        )
        control = workflow.checked_manifest(store, meta, review["control_job"])
        adjudication.validate_batch(
            {"rulings": [r["content"] for r in review["final_rulings"].values()]},
            control["context"],
        )
    expected = make_routing(final_rulings(store, review), review["policy"], review["options"])
    require(set(expected) == set(review["routing"]), "E_ROUTING", "Routing omits or adds a ruling.")
    for key, route in review["routing"].items():
        assessment = route["assessment"]
        require(
            {**route, "assessment": None} == expected[key],
            "E_ROUTING",
            "Routing differs from the frozen policy and rulings.",
        )
        if assessment:
            require(
                assessment["content_hash"] == route["content_hash"],
                "E_APPROVAL",
                "Human assessment covers an older ruling.",
            )


def audit_all(store, meta):
    reviews = store.records("review")
    require(
        {str(r["move"]) for r in reviews} == set(meta.get("reviews", {}))
        and len(reviews) == len(meta.get("reviews", {})),
        "E_REVIEW",
        "Review records are missing or unregistered.",
    )
    for item in reviews:
        review = read_review(store, meta, item["move"])
        audit_review(store, meta, review)
        require(
            (review["closed_at"] is not None) == (review["move"] <= meta["closed_move"]),
            "E_REVIEW",
            "Round closure and project progress disagree.",
        )
    audit_boards(store, meta)


def health_warnings(store, meta):
    warnings = []
    for move in range(1, meta["closed_move"] + 1):
        review = read_review(store, meta, move)
        bases = Counter(r["content"]["basis"] for r in review["final_rulings"].values())
        share = bases["judgment"] / sum(bases.values())
        if share > review["policy"]["judgment_share_threshold"]:
            warnings.append(
                {
                    "code": "E_JUDGMENT_SHARE",
                    "message": f"Round {move} judgment share {share:.0%} exceeds its configured threshold.",
                }
            )
        if review["closure"]["escalation_debt"]:
            warnings.append(
                {
                    "code": "W_ESCALATION_DEBT",
                    "message": f"Round {move} closed with unassessed escalation candidates: {', '.join(review['closure']['escalation_debt'])}.",
                }
            )
    return warnings
