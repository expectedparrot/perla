"""Commit-before-reveal control jobs and evidence-checked, pending rulings."""

from collections import Counter
from pathlib import Path
from uuid import uuid4

from . import edsl_adapter, workflow
from .models import RulingBatch, Testimony
from .store import digest, now
from .workflow import require


def add_testimony(store, payload, recorder):
    meta = workflow.project(store)
    require(
        not meta.get("endgame_jobs", {}).get("endgame"),
        "E_ENDGAME_LOCKED",
        "The endgame evidence snapshot is frozen.",
    )
    require(bool(recorder.strip()), "E_INPUT", "Record who supplied the testimony.")
    require(
        not any(
            j["kind"] == "rulings" and j["move"] > meta["closed_move"]
            for j in meta["jobs"].values()
        ),
        "E_EVIDENCE_LOCKED",
        "The open control job has already frozen its evidence.",
        "Supply testimony before adjudicate open; later evidence revisions are a future milestone.",
    )
    testimony = Testimony.model_validate(payload).model_dump()
    path = f"testimony/{testimony['id']}.json"
    require(
        store.read(path) is None, "E_EXISTS", "Testimony ids are immutable and cannot be reused."
    )
    record = {**testimony, "recorded_by": recorder.strip(), "recorded_at": now(), "origin": "human"}
    store.write(path, record)
    meta.setdefault("testimony", {})[testimony["id"]] = digest(record)
    store.write("project.json", meta)
    return record


def testimony_records(store, meta):
    records = store.records("testimony")
    hashes = meta.get("testimony", {})
    require(
        {r["id"]: digest(r) for r in records} == hashes,
        "E_EVIDENCE",
        "Registered testimony is missing, edited, or has unregistered additions.",
    )
    return records


def committed_moves(store, meta, move):
    """Validate every actor's commitment before constructing any revealed context."""
    require(
        1 <= move <= meta["decision"]["moves"], "E_MOVE", "Round is outside the registered horizon."
    )
    expected = {a["id"] for a in store.records("actors")}
    records = store.records(f"moves/move-{move}")
    require(
        len(expected) >= 3
        and {r["actor_id"] for r in records} == expected
        and len(records) == len(expected),
        "E_UNCOMMITTED_MOVES",
        "All expected actors must commit before control can see the round.",
    )
    for record in records:
        job_id = record["provenance"]["job_id"]
        job = meta["jobs"].get(job_id, {})
        require(
            job.get("ingested")
            and job.get("kind") == "moves"
            and job.get("actor_id") == record["actor_id"]
            and job.get("move") == move
            and record["move"] == move,
            "E_LINEAGE",
            "Move commitment has invalid job lineage.",
        )
        require(
            record["content_hash"] == digest(record["content"])
            and record["content_hash"] == record["provenance"]["answer_hash"],
            "E_COMMITMENT",
            "Committed move differs from its recorded hash.",
        )
        workflow.checked_manifest(store, meta, job_id)
    return records


def control_context(store, meta, move, testimony_ids=None):
    moves = committed_moves(store, meta, move)
    testimony = testimony_records(store, meta)
    if testimony_ids is not None:
        testimony = [t for t in testimony if t["id"] in testimony_ids]
    return {
        "decision": meta["decision"],
        "criteria": store.read("criteria.json"),
        "board": store.read(f"board/move-{move - 1}.json"),
        "actors": store.records("actors"),
        "dossiers": workflow.dossier_snapshot(store)["dossiers"],
        "moves": moves,
        "actions": {
            f"m{move}.{r['actor_id']}.a{i}": {"actor_id": r["actor_id"], "action": text}
            for r in moves
            for i, text in enumerate(r["content"]["actions"], start=1)
        },
        "testimony": testimony,
        "models": [],
    }


def open_round(store, move, mode="agent"):
    meta = workflow.project(store)
    require(mode in {"agent", "human"}, "E_INPUT", "Control mode must be agent or human.")
    require(move == meta["closed_move"] + 1, "E_OPEN_RULINGS", "Close the previous round first.")
    context = control_context(store, meta, move)
    path = f"adjudication/move-{move}.json"
    existing = store.read(path)
    if existing:
        manifest = audit_round(store, meta, move)
        require(manifest["mode"] == mode, "E_COMMITTED", "Control mode is fixed at round opening.")
        return {
            "job_id": manifest["id"],
            "package": str(store.path(manifest["package"])),
            "move": move,
            "mode": mode,
            "reused": True,
        }
    job_id = "job-" + uuid4().hex
    manifest = {
        "id": job_id,
        "project_id": meta["id"],
        "kind": "rulings",
        "actor_id": "control",
        "actor_name": "Neutral control",
        "actor_role": "control",
        "move": move,
        "mode": mode,
        "created_at": now(),
        "context": context,
        "response_schema": RulingBatch.model_json_schema(),
        "package": f"jobs/{job_id}.jobs.ep",
        "included": [
            "project.json#decision",
            "criteria.json",
            f"board/move-{move - 1}.json",
            *[f"actors/{a['id']}.json" for a in context["actors"]],
            *[f"dossiers/{a['id']}.json" for a in context["actors"]],
            *[f"moves/move-{move}/{a['id']}.json" for a in context["actors"]],
            *[f"testimony/{t['id']}.json" for t in context["testimony"]],
        ],
    }
    package = store.path(manifest["package"])
    package.parent.mkdir(parents=True, exist_ok=True)
    manifest["job_hash"] = digest(edsl_adapter.save_job(manifest, package))
    manifest_path = f"jobs/{job_id}.manifest.json"
    store.write(manifest_path, manifest)
    meta["jobs"][job_id] = {
        "id": job_id,
        "kind": "rulings",
        "actor_id": "control",
        "move": move,
        "manifest": manifest_path,
        "manifest_hash": digest(manifest),
        "ingested": False,
    }
    store.write(
        path,
        {
            "move": move,
            "job_id": job_id,
            "opened_at": manifest["created_at"],
            "context_hash": digest(context),
            "status": "awaiting_rulings",
            "rulings": {},
        },
    )
    store.write("project.json", meta)
    return {"job_id": job_id, "package": str(package), "move": move, "mode": mode, "reused": False}


def validate_evidence(evidence, context):
    basis, cites, cases = evidence.basis, evidence.cite, evidence.cases
    require(
        basis not in {"model", "panel"},
        "E_UNSUPPORTED_BASIS",
        f"{basis} basis requires a registered computation or panel aggregation, not yet implemented.",
        "Use a supported basis only when the evidence warrants it; otherwise defer this ruling.",
    )
    facts = {
        f"d.{actor_id}.{fact['id']}"
        for actor_id, dossier in context["dossiers"].items()
        for fact in dossier["facts"]
    }
    testimony = {f"t.{t['id']}" for t in context["testimony"]}
    require(len(cites) == len(set(cites)), "E_CITATION", "Evidence citations must be unique.")
    require(
        set(cites) <= facts | testimony,
        "E_CITATION",
        "Citation is absent from the control job's evidence snapshot.",
    )
    if basis == "dossier_fact":
        require(
            bool(cites) and set(cites) <= facts,
            "E_CITATION",
            "Dossier-fact basis requires qualified dossier fact ids.",
        )
    if basis == "testimony":
        require(
            bool(cites) and set(cites) <= testimony,
            "E_CITATION",
            "Testimony basis requires registered testimony ids.",
        )
    if basis == "reference_class":
        require(
            len({case.name.casefold() for case in cases}) >= 2,
            "E_CITATION",
            "Reference-class basis requires at least two distinct named, sourced cases with mappings and disanalogies.",
        )
    else:
        require(not cases, "E_CITATION", "Historical cases belong to reference_class evidence.")


def validate_batch(answer, context):
    batch = RulingBatch.model_validate(answer)
    actions = context["actions"]
    actors = {a["id"] for a in context["actors"]}
    criteria = {c["id"] for c in context["criteria"]}
    covered = set()
    for ruling in batch.rulings:
        require(
            set(ruling.resolves) <= actions.keys(),
            "E_RULING_REFERENCE",
            "Ruling cites an unknown committed action.",
        )
        require(
            set(ruling.affected_actors) <= actors
            and {actions[key]["actor_id"] for key in ruling.resolves}
            <= set(ruling.affected_actors),
            "E_RULING_REFERENCE",
            "Affected actors must include each actor whose actions are resolved.",
        )
        require(
            set(ruling.kill_criteria) <= criteria,
            "E_RULING_REFERENCE",
            "Ruling cites an unknown kill criterion.",
        )
        validate_evidence(ruling, context)
        for link in ruling.chain:
            validate_evidence(link, context)
        covered.update(ruling.resolves)
    require(
        covered == set(actions),
        "E_UNRESOLVED_ACTIONS",
        "Every committed action must be addressed by a ruling.",
        "Add explicit no-effect rulings or declared branches for remaining actions.",
    )
    return batch.model_dump()


def audit_round(store, meta, move):
    record = store.read(f"adjudication/move-{move}.json")
    require(
        record is not None,
        "E_ADJUDICATION",
        "Open adjudication before inspecting or ingesting rulings.",
    )
    manifest = workflow.checked_manifest(store, meta, record["job_id"])
    require(
        manifest["kind"] == "rulings" and manifest["move"] == move and record["move"] == move,
        "E_LINEAGE",
        "Adjudication round has invalid job lineage.",
    )
    context = control_context(
        store, meta, move, {t["id"] for t in manifest["context"]["testimony"]}
    )
    require(
        record["context_hash"] == digest(context) and manifest["context"] == context,
        "E_ADJUDICATION",
        "The revealed control snapshot changed after opening.",
    )
    job = meta["jobs"][manifest["id"]]
    expected_status = "awaiting_rebuttals" if job["ingested"] else "awaiting_rulings"
    require(
        record["status"] == expected_status,
        "E_ADJUDICATION",
        "Round status does not match its ingestion record.",
    )
    rulings = store.records(f"rulings/move-{move}")
    hashes = {r["content"]["ruling_id"]: digest(r) for r in rulings}
    require(
        hashes == record["rulings"] == job.get("ruling_hashes", {}) and len(hashes) == len(rulings),
        "E_RULING",
        "Ruling records are missing, modified, or unregistered.",
    )
    if job["ingested"]:
        validate_batch({"rulings": [r["content"] for r in rulings]}, context)
    return manifest


def ingest_rulings(store, job_id, source, human=False):
    meta = workflow.project(store)
    manifest = workflow.checked_manifest(store, meta, job_id)
    require(manifest["kind"] == "rulings", "E_LINEAGE", "Rulings must answer a control job.")
    require(
        not meta["jobs"][job_id]["ingested"],
        "E_COMMITTED",
        "Control rulings are already ingested; replacement is not allowed.",
    )
    move = manifest["move"]
    require(
        move == meta["closed_move"] + 1,
        "E_OPEN_RULINGS",
        "Rulings must answer the current open round.",
    )
    require(
        audit_round(store, meta, move)["id"] == job_id,
        "E_LINEAGE",
        "This is not the open round's control job.",
    )
    answer, provenance = workflow.read_answer(meta, manifest, source, human)
    answer = validate_batch(answer, manifest["context"])
    provenance.update(
        {
            "source_package": str(Path(source).resolve()),
            "job_id": job_id,
            "manifest_hash": meta["jobs"][job_id]["manifest_hash"],
            "ingested_at": now(),
            "answer_hash": digest(answer),
        }
    )
    # Resolution provenance is derived from ingestion, never from a model's answer.
    resolution_mode = "human" if provenance["origin"] == "human" else "agent"
    hashes = {}
    for ruling in answer["rulings"]:
        escalation = []
        if ruling["basis"] == "judgment" or any(
            link["basis"] == "judgment" for link in ruling["chain"]
        ):
            escalation.append("judgment")
        if ruling["kill_criteria"]:
            escalation.append("kill_criterion")
        if ruling["branches"]:
            escalation.append("declared_branch")
        record = {
            "content": ruling,
            "content_hash": digest(ruling),
            "move": move,
            "provenance": provenance,
            "resolution_mode": resolution_mode,
            "status": "pending_rebuttal",
            "rebuttals": [],
            "escalation_candidates": escalation,
        }
        hashes[ruling["ruling_id"]] = digest(record)
        store.write(f"rulings/move-{move}/{ruling['ruling_id']}.json", record)
    event_id = "ingest-" + uuid4().hex
    store.write(
        f"ingest/{event_id}.json",
        {
            "id": event_id,
            "kind": "rulings",
            "actor_id": "control",
            "move": move,
            "answer": answer,
            "provenance": provenance,
        },
    )
    round_record = store.read(f"adjudication/move-{move}.json")
    round_record.update({"status": "awaiting_rebuttals", "rulings": hashes})
    meta["jobs"][job_id].update({"ingested": True, "ruling_hashes": hashes})
    store.write(f"adjudication/move-{move}.json", round_record)
    store.write("project.json", meta)
    return {
        "move": move,
        "ruling_ids": list(hashes),
        "status": "adjudication_routing",
        "basis_distribution": dict(Counter(r["basis"] for r in answer["rulings"])),
        "provenance": provenance,
    }


def show_round(store, move):
    from . import rounds

    meta = workflow.project(store)
    manifest = audit_round(store, meta, move)
    rulings = store.records(f"rulings/move-{move}")
    review = rounds.read_review(store, meta, move)
    round_record = dict(store.read(f"adjudication/move-{move}.json"))
    if meta["jobs"][manifest["id"]]["ingested"]:
        round_record["status"] = rounds.phase(store, meta, move)
    initial = rulings
    if review:
        rounds.audit_review(store, meta, review)
        rulings = list(rounds.final_rulings(store, review).values())
    proposal, conflicts = (
        rounds.board_proposal(store, review) if review and review["resolutions"] else (None, {})
    )
    return {
        "round": round_record,
        "actions": manifest["context"]["actions"],
        "rulings": rulings,
        "initial_rulings": initial,
        "review": review,
        "proposed_board": proposal,
        "board_conflicts": conflicts,
        "basis_distribution": dict(Counter(r["content"]["basis"] for r in rulings)),
        "board_applied": move <= meta["closed_move"],
    }


def audit_all(store, meta):
    testimony_records(store, meta)
    expected = {j["move"] for j in meta["jobs"].values() if j["kind"] == "rulings"}
    require(
        {r["move"] for r in store.records("adjudication")} == expected,
        "E_ADJUDICATION",
        "Adjudication round records are missing or unregistered.",
    )
    for move in expected:
        audit_round(store, meta, move)
