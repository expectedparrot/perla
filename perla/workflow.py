"""Setup, actor jobs, commitments, and shared workflow/provenance contracts."""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from . import edsl_adapter
from .models import Actor, Dossier, HumanSubmission, Identifier, InitInput, Move, Research
from .store import PerlaError, atomic_json, digest, now, read_json


def identifier(value):
    return TypeAdapter(Identifier).validate_python(value)


def require(condition, code, message, remediation="Inspect perla status for the next step."):
    if not condition:
        raise PerlaError(code, message, remediation)


def initialize(root, payload, delegates):
    try:
        data = InitInput.model_validate(payload)
    except ValidationError as exc:
        code = (
            "E_VAGUE_DECISION"
            if any(e["loc"][:1] == ("decision",) for e in exc.errors())
            else "E_INPUT"
        )
        raise PerlaError(
            code,
            str(exc),
            "Provide structured decision, observable criteria, and board; see the example.",
        ) from exc
    require(set(delegates) <= {"dossiers", "rulings"}, "E_INPUT", "Unknown approval checkpoint.")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    require(not (root / ".perla").exists(), "E_EXISTS", "A .perla directory already exists.")
    timestamp = now()
    decision, criteria = data.decision.model_dump(), [c.model_dump() for c in data.criteria]
    project = {
        "schema_version": "1.0",
        "id": str(uuid4()),
        "created_at": timestamp,
        "decision": decision,
        "registration_hash": digest({"decision": decision, "criteria": criteria}),
        "initial_board_hash": digest(data.board),
        "delegations": [
            {"checkpoint": d, "recorded_at": timestamp} for d in sorted(set(delegates))
        ],
        "approvals": {},
        "jobs": {},
        "closed_move": 0,
    }
    with tempfile.TemporaryDirectory(prefix=".perla-init-", dir=root) as temporary:
        stage = Path(temporary) / ".perla"
        stage.mkdir()
        for name, value in {
            "project.json": project,
            "criteria.json": criteria,
            "board/move-0.json": data.board,
            "insights.json": [],
            "predictions.json": [],
        }.items():
            atomic_json(stage / name, value)
        os.rename(stage, root / ".perla")
    return {"project_id": project["id"], "root": str(root), "decision": decision}


def project(store):
    from .rounds import audit_boards

    meta = store.read("project.json")
    require(
        isinstance(meta, dict) and meta.get("schema_version") == "1.0",
        "E_STATE",
        "Unsupported project state.",
    )
    require(
        meta["registration_hash"]
        == digest({"decision": meta["decision"], "criteria": store.read("criteria.json")}),
        "E_CRITERIA_LOCKED",
        "Pre-registered decision or criteria changed on disk.",
        "Restore the registered inputs. Decision amendments are a later milestone.",
    )
    require(
        meta["initial_board_hash"] == digest(store.read("board/move-0.json")),
        "E_STATE",
        "The registered initial board changed on disk.",
    )
    if frozen(meta):
        require(
            meta.get("setup_hash") == digest(dossier_snapshot(store)),
            "E_SETUP_LOCKED",
            "The frozen actor roster or dossiers changed on disk.",
        )
    audit_boards(store, meta)
    return meta


def frozen(meta):
    return any(j["kind"] == "moves" for j in meta["jobs"].values())


def check_setup(meta):
    require(
        not frozen(meta),
        "E_SETUP_LOCKED",
        "Actor and dossier inputs lock when move jobs are emitted.",
    )


def actor(store, actor_id):
    identifier(actor_id)
    value = store.read(f"actors/{actor_id}.json")
    require(value is not None, "E_ACTOR", f"Unknown actor: {actor_id}")
    return value


def add_actor(store, payload):
    meta = project(store)
    check_setup(meta)
    value = Actor.model_validate(payload).model_dump()
    actors = store.records("actors")
    require(len(actors) < 6, "E_ACTOR", "A game supports at most six actors.")
    require(not any(a["id"] == value["id"] for a in actors), "E_EXISTS", "Actor id already exists.")
    require(
        value["role"] != "home" or not any(a["role"] == "home" for a in actors),
        "E_ACTOR",
        "A game has exactly one home actor.",
    )
    store.write(f"actors/{value['id']}.json", value)
    return value


def set_mode(store, actor_id, mode):
    check_setup(project(store))
    value = actor(store, actor_id)
    value = Actor.model_validate({**value, "mode": mode}).model_dump()
    store.write(f"actors/{actor_id}.json", value)
    return value


def save_dossier(store, actor_id, payload, author, note, *, origin="human"):
    check_setup(project(store))
    actor(store, actor_id)
    require(
        bool(author.strip()) and bool(note.strip()),
        "E_INPUT",
        "An author and edit rationale are required.",
    )
    value = Dossier.model_validate(payload).model_dump()
    event = {
        "id": "edit-" + uuid4().hex,
        "actor_id": actor_id,
        "origin": origin,
        "participant_id": author.strip(),
        "rationale": note.strip(),
        "recorded_at": now(),
        "before": store.read(f"dossiers/{actor_id}.json"),
        "after": value,
    }
    store.write(f"ingest/{event['id']}.json", event)
    store.write(f"dossiers/{actor_id}.json", value)
    if store.read(f"dossier-drafts/{actor_id}.json") is not None:
        store.write(f"dossier-drafts/{actor_id}.json", None)
    return {"actor_id": actor_id, "dossier_hash": digest(value), "event_id": event["id"]}


def dossier_snapshot(store):
    return {
        "actors": store.records("actors"),
        "dossiers": {
            a["id"]: store.read(f"dossiers/{a['id']}.json") for a in store.records("actors")
        },
    }


def ready_dossiers(store):
    snapshot = dossier_snapshot(store)
    actors = snapshot["actors"]
    require(
        3 <= len(actors) <= 6 and sum(a["role"] == "home" for a in actors) == 1,
        "E_ACTORS",
        "Declare one home actor and at least two external actors before review.",
    )
    incentives = set()
    for item in actors:
        dossier = snapshot["dossiers"][item["id"]]
        require(dossier is not None, "E_DOSSIER", f"Missing dossier for {item['id']}.")
        Dossier.model_validate(dossier)
        structure = digest(
            {k: sorted(s.casefold() for s in dossier[k]) for k in ("incentives", "constraints")}
        )
        require(
            structure not in incentives,
            "E_MIRROR_IMAGING",
            "Two actors have identical incentives and constraints.",
            "Research their distinct economics and revise the dossiers before approval.",
        )
        incentives.add(structure)
    return snapshot


def approve_dossiers(store, reviewer, note):
    meta = project(store)
    require(
        not meta.get("endgame_jobs", {}).get("endgame"),
        "E_ENDGAME_LOCKED",
        "The endgame approval snapshot is frozen.",
    )
    require(
        bool(reviewer.strip()) and bool(note.strip()),
        "E_APPROVAL",
        "Record a reviewer and review note.",
    )
    snapshot = ready_dossiers(store)
    approval = {
        "reviewer": reviewer.strip(),
        "note": note.strip(),
        "recorded_at": now(),
        "snapshot_hash": digest(snapshot),
    }
    meta["approvals"]["dossiers"] = approval
    store.write("project.json", meta)
    return approval


def is_approved(store, meta):
    return any(d["checkpoint"] == "dossiers" for d in meta["delegations"]) or meta["approvals"].get(
        "dossiers", {}
    ).get("snapshot_hash") == digest(dossier_snapshot(store))


def generate(store, kind, actor_id=None, move=0, facts=None, mode=None):
    if kind in {"novelty", "endgame"}:
        from . import endgame

        require(
            actor_id is None and move == 0 and facts is None,
            "E_INPUT",
            "Endgame jobs use the completed game; omit --actor, --move, and --facts.",
        )
        return endgame.generate(store, kind, mode or "agent")
    require(
        mode is None,
        "E_INPUT",
        "--mode is only available for novelty and endgame jobs; other actor modes are registered in state.",
    )
    if kind == "rebuttals":
        from .rounds import generate_rebuttals

        require(
            facts is None,
            "E_INPUT",
            "Rebuttals use the frozen actor dossier; --facts is not supported.",
        )
        return generate_rebuttals(store, move, actor_id)
    meta = project(store)
    if kind == "moves":
        require(
            move >= 1 and move <= meta["decision"]["moves"],
            "E_MOVE",
            "Move is outside the registered horizon.",
        )
        require(
            move == meta["closed_move"] + 1,
            "E_OPEN_RULINGS",
            "The previous round must close before new actor jobs.",
        )
        ready_dossiers(store)
        require(
            is_approved(store, meta),
            "E_APPROVAL",
            "Current dossiers require recorded review approval.",
            "Present the dossiers, then record approval with perla workflow approve dossiers --by NAME --note NOTE.",
        )
        actors = [actor(store, actor_id)] if actor_id else store.records("actors")
        meta["setup_hash"] = digest(dossier_snapshot(store))
    else:
        check_setup(meta)
        require(
            actor_id is not None and facts is not None,
            "E_INPUT",
            "Dossier generation requires --actor and --facts.",
        )
        require(move == 0, "E_MOVE", "Dossier jobs use move 0.")
        research = Research.model_validate(facts).model_dump()
        actors = [actor(store, actor_id)]
    generated = []
    for item in actors:
        existing = [
            j
            for j in meta["jobs"].values()
            if j["kind"] == kind and j["actor_id"] == item["id"] and j["move"] == move
        ]
        if kind == "moves" and existing:
            manifest = checked_manifest(store, meta, existing[0]["id"])
            generated.append(
                {
                    "job_id": manifest["id"],
                    "package": str(store.path(manifest["package"])),
                    "actor_id": item["id"],
                    "reused": True,
                    "mode": item["mode"],
                }
            )
            continue
        job_id = "job-" + uuid4().hex
        context = (
            {
                "decision": meta["decision"],
                "dossier": store.read(f"dossiers/{item['id']}.json"),
                "board": store.read(f"board/move-{move - 1}.json"),
            }
            if kind == "moves"
            else {"actor": item, "research": research}
        )
        manifest = {
            "id": job_id,
            "project_id": meta["id"],
            "kind": kind,
            "actor_id": item["id"],
            "actor_name": item["name"],
            "actor_role": item["role"],
            "move": move,
            "mode": item["mode"],
            "created_at": now(),
            "context": context,
            "package": f"jobs/{job_id}.jobs.ep",
            "included": (
                [
                    "project.json#decision",
                    f"dossiers/{item['id']}.json",
                    f"board/move-{move - 1}.json",
                ]
                if kind == "moves"
                else [f"actors/{item['id']}.json", "supplied_research"]
            ),
        }
        # Package construction happens before any canonical state is committed. A failed
        # export may leave an unregistered derived package, never a partially frozen roster.
        package = store.path(manifest["package"])
        package.parent.mkdir(parents=True, exist_ok=True)
        job_payload = edsl_adapter.save_job(manifest, package)
        manifest["job_hash"] = digest(job_payload)
        manifest_path = f"jobs/{job_id}.manifest.json"
        store.write(manifest_path, manifest)
        meta["jobs"][job_id] = {
            "id": job_id,
            "kind": kind,
            "actor_id": item["id"],
            "move": move,
            "manifest": manifest_path,
            "manifest_hash": digest(manifest),
            "ingested": False,
        }
        generated.append(
            {
                "job_id": job_id,
                "actor_id": item["id"],
                "package": str(package),
                "reused": False,
                "mode": item["mode"],
            }
        )
    store.write("project.json", meta)
    return {"jobs": generated}


def checked_manifest(store, meta, job_id, audit_package=True):
    identifier(job_id)
    registered = meta["jobs"].get(job_id)
    require(registered is not None, "E_LINEAGE", "Unknown job id.")
    manifest = store.read(registered["manifest"])
    require(
        manifest is not None and digest(manifest) == registered["manifest_hash"],
        "E_ISOLATION",
        "Job manifest changed or is missing.",
    )
    if manifest["kind"] == "moves":
        expected = {
            "decision": meta["decision"],
            "dossier": store.read(f"dossiers/{manifest['actor_id']}.json"),
            "board": store.read(f"board/move-{manifest['move'] - 1}.json"),
        }
        require(
            manifest["context"] == expected,
            "E_ISOLATION",
            "Move context differs from the permitted public/own-actor inputs.",
        )
    elif manifest["kind"] in {"rebuttals", "resolutions"}:
        from . import rounds

        if manifest["kind"] == "rebuttals":
            expected = rounds.rebuttal_context(store, meta, manifest["actor_id"], manifest["move"])
        else:
            review = rounds.read_review(store, meta, manifest["move"])
            require(review is not None, "E_REVIEW", "Control resolution review is missing.")
            control = checked_manifest(store, meta, review["control_job"], audit_package=False)
            expected = rounds.resolution_context(store, control, review)
        require(
            manifest["context"] == expected,
            "E_ISOLATION",
            "Rebuttal or resolution job context changed or contains unassigned evidence.",
        )
    if manifest["kind"] in {"novelty", "endgame"}:
        from . import endgame

        expected = (
            endgame.baseline_context(store, meta)
            if manifest["kind"] == "novelty"
            else endgame.game_context(store, meta)
        )
        require(
            manifest["context"] == expected,
            "E_ISOLATION",
            "Evaluation job includes unexpected inputs or its evidence snapshot changed.",
        )
    if audit_package:
        payload = edsl_adapter.load_job(store.path(manifest["package"]))
        require(
            digest(payload) == manifest["job_hash"],
            "E_ISOLATION",
            "Exported Jobs package differs from its recorded content.",
        )
    return manifest


def read_answer(meta, manifest, source, human=False):
    """Read a single answer and verify the shared actor/control provenance contract."""
    if human:
        submission = HumanSubmission.model_validate(read_json(source)).model_dump()
        expected = {
            "project_id": meta["id"],
            "job_id": manifest["id"],
            "kind": manifest["kind"],
            "actor_id": manifest["actor_id"],
            "move": manifest["move"],
        }
        require(
            all(submission[k] == v for k, v in expected.items()),
            "E_LINEAGE",
            "Human submission does not answer this job.",
        )
        answer = submission["answer"]
        provenance = {
            "origin": "human",
            "model": "human",
            "participant_id": submission["participant_id"],
        }
    else:
        require(
            manifest["mode"] != "human",
            "E_PROVENANCE",
            "This actor requires a human-attributed submission.",
        )
        answer, provenance = edsl_adapter.load_submission(source, manifest)
    return answer, provenance


def ingest(store, kind, job_id, source, human=False):
    if kind in {"novelty", "endgame"}:
        from . import endgame

        return endgame.ingest(store, kind, job_id, source, human)
    if kind in {"rebuttals", "resolutions"}:
        from .rounds import ingest_rebuttals, ingest_resolutions

        return (ingest_rebuttals if kind == "rebuttals" else ingest_resolutions)(
            store, job_id, source, human
        )
    if kind == "rulings":
        from .adjudication import ingest_rulings

        return ingest_rulings(store, job_id, source, human)
    meta = project(store)
    manifest = checked_manifest(store, meta, job_id)
    require(manifest["kind"] == kind, "E_LINEAGE", "Job kind does not match the ingest command.")
    require(
        not meta["jobs"][job_id]["ingested"],
        "E_COMMITTED",
        "This job already has an ingested answer.",
    )
    if kind == "dossiers":
        check_setup(meta)
    else:
        require(
            manifest["move"] == meta["closed_move"] + 1,
            "E_MOVE",
            "Results answer a closed or future round.",
        )
        require(
            not store.read(f"moves/move-{manifest['move']}/{manifest['actor_id']}.json"),
            "E_COMMITTED",
            "The actor's move is already committed and cannot be replaced.",
        )
    answer, provenance = read_answer(meta, manifest, source, human)
    if kind == "dossiers":
        answer = Dossier.model_validate(
            {**answer, "facts": manifest["context"]["research"]["facts"]}
        ).model_dump()
    else:
        answer = Move.model_validate(answer).model_dump()
    provenance.update(
        {
            "source_package": str(Path(source).resolve()),
            "job_id": job_id,
            "manifest_hash": meta["jobs"][job_id]["manifest_hash"],
            "ingested_at": now(),
            "answer_hash": digest(answer),
        }
    )
    event = {
        "id": "ingest-" + uuid4().hex,
        "kind": kind,
        "actor_id": manifest["actor_id"],
        "move": manifest["move"],
        "answer": answer,
        "provenance": provenance,
    }
    store.write(f"ingest/{event['id']}.json", event)
    if kind == "dossiers":
        store.write(f"dossiers/{manifest['actor_id']}.json", answer)
        if store.read(f"dossier-drafts/{manifest['actor_id']}.json") is not None:
            store.write(f"dossier-drafts/{manifest['actor_id']}.json", None)
    else:
        record = {
            "actor_id": manifest["actor_id"],
            "move": manifest["move"],
            "content": answer,
            "content_hash": digest(answer),
            "committed_at": provenance["ingested_at"],
            "provenance": provenance,
        }
        store.write(f"moves/move-{manifest['move']}/{manifest['actor_id']}.json", record)
    meta["jobs"][job_id]["ingested"] = True
    store.write("project.json", meta)
    return {
        "actor_id": manifest["actor_id"],
        "move": manifest["move"],
        "content_hash": digest(answer),
        "provenance": provenance,
    }


def action(kind, text, argv=None, approval=False, mutating=False, networked=False):
    result = {
        "type": kind,
        "description": text,
        "mutating": mutating,
        "networked": networked,
        "requires_approval": approval,
    }
    if argv:
        result["argv"] = argv
    return result


def status(store):
    from . import endgame, lookback, rounds

    meta = project(store)
    actors = store.records("actors")
    current = meta["closed_move"] + 1
    commits = store.records(f"moves/move-{current}")
    missing = [a["id"] for a in actors if not store.read(f"dossiers/{a['id']}.json")]
    jobs = [j for j in meta["jobs"].values() if j["kind"] == "moves" and j["move"] == current]
    control = store.read(f"adjudication/move-{current}.json")
    review = rounds.read_review(store, meta, current)
    if meta["closed_move"] == meta["decision"]["moves"]:
        final = endgame.checked_record(store, meta, "endgame")
        assessment = endgame.checked_record(store, meta, "endgame_assessment")
        endgame_jobs = meta.get("endgame_jobs", {})
        if final:
            phase, next_actions = (
                "complete",
                [
                    action(
                        "run",
                        "Export the finalized report context.",
                        ["perla", "report", "context"],
                        mutating=True,
                    )
                ],
            )
        elif assessment:
            phase, next_actions = (
                "endgame_review",
                [
                    action(
                        "facilitate",
                        "Review criterion verdicts and insight ownership; revise the assessment if needed, then run endgame to finalize.",
                    )
                ],
            )
        elif endgame_jobs.get("endgame"):
            phase, next_actions = (
                "awaiting_endgame",
                [
                    action(
                        "facilitate",
                        "Execute or collect the endgame job and ingest its evaluation.",
                    )
                ],
            )
        elif endgame_jobs.get("novelty") and not meta["jobs"][endgame_jobs["novelty"]]["ingested"]:
            phase, next_actions = (
                "awaiting_novelty",
                [
                    action(
                        "facilitate",
                        "Execute the isolated novelty baseline job and ingest its findings.",
                    )
                ],
            )
        else:
            phase = "ready_for_endgame"
            kind = "endgame" if endgame_jobs.get("novelty") else "novelty"
            next_actions = [
                action(
                    "run",
                    "Generate the endgame evaluation."
                    if kind == "endgame"
                    else "Generate the optional inputs-only novelty baseline before evaluation.",
                    ["perla", "job", "generate", kind],
                    mutating=True,
                )
            ]
    elif len(actors) < 3 or sum(a["role"] == "home" for a in actors) != 1:
        phase, next_actions = (
            "actors",
            [action("facilitate", "Declare one home and at least two external actors.")],
        )
    elif missing:
        phase, next_actions = (
            "dossiers",
            [
                action(
                    "facilitate", f"Research and supply sourced dossiers for: {', '.join(missing)}."
                )
            ],
        )
    elif not is_approved(store, meta):
        phase, next_actions = (
            "dossier_review",
            [
                action(
                    "facilitate",
                    "Present all dossiers for review and record approval with workflow approve dossiers.",
                    approval=True,
                )
            ],
        )
    elif control:
        phase = (
            control["status"]
            if control["status"] == "awaiting_rulings"
            else rounds.phase(store, meta, current)
        )
        commands = {
            "adjudication_routing": ["adjudicate", "route"],
            "rebuttal_generation": ["job", "generate", "rebuttals"],
            "control_resolution": ["adjudicate", "resolve"],
            "ready_to_close": ["adjudicate", "close"],
        }
        instructions = {
            "awaiting_rulings": "Execute the control package externally and ingest rulings.",
            "awaiting_rebuttals": "Collect one response per assigned actor job and ingest rebuttals.",
            "awaiting_resolution": "Execute the control resolution package and ingest resolutions.",
            "escalation_review": "Present blocking rulings to human reviewers and record their assessments with adjudicate assess.",
            "board_reconciliation": "Inspect conflicting board fields and record choices with adjudicate reconcile.",
            "ruling_review": "Present final rulings, escalation debt, and the proposed board; record approval with workflow approve rulings.",
        }
        next_actions = (
            [
                action(
                    "run",
                    "Advance the current adjudication phase.",
                    ["perla", *commands[phase], "--move", str(current)],
                    mutating=True,
                )
            ]
            if phase in commands
            else [
                action(
                    "facilitate",
                    instructions[phase],
                    approval=phase in {"escalation_review", "ruling_review"},
                )
            ]
        )
    elif len(commits) == len(actors):
        phase, next_actions = (
            "ready_for_adjudication",
            [
                action(
                    "run",
                    "All moves are committed. Open adjudication to export the neutral control job.",
                    ["perla", "adjudicate", "open", "--move", str(current)],
                    mutating=True,
                )
            ],
        )
    elif len(jobs) < len(actors):
        phase, next_actions = (
            "move_generation",
            [
                action(
                    "run",
                    "Generate remaining isolated move jobs.",
                    ["perla", "job", "generate", "moves", "--move", str(current)],
                    mutating=True,
                )
            ],
        )
    else:
        phase, next_actions = (
            "move_commitment",
            [
                action(
                    "facilitate",
                    "Inspect actor packages, choose execution models or human respondents, then ingest one answer per job.",
                )
            ],
        )
    lookback_record = lookback.checked(store, meta)
    lookback_state = None
    if lookback_record:
        scores = lookback_record["scores"]
        stale = bool(scores and scores[-1]["events_hash"] != digest(lookback_record["events"]))
        lookback_state = {
            "event_count": len(lookback_record["events"]),
            "score_count": len(scores),
            "score_stale": stale,
        }
        next_actions.append(
            action(
                "facilitate",
                "Record sourced outcomes and run perla lookback score --as-of YYYY-MM-DD.",
            )
        )
    elif phase == "complete":
        next_actions.append(
            action("facilitate", "Schedule lookback to record real-world outcomes with sources.")
        )
    data = {
        "project_id": meta["id"],
        "phase": phase,
        "current_move": current if current <= meta["decision"]["moves"] else None,
        "closed_move": meta["closed_move"],
        "actors": actors,
        "missing_dossiers": missing,
        "committed_actors": [c["actor_id"] for c in commits],
        "checkpoints": {
            "dossiers": {
                "approved": is_approved(store, meta),
                "record": meta["approvals"].get("dossiers"),
            },
            "rulings": {
                "approved": phase == "ready_to_close",
                "record": review["approval"] if review else None,
                "delegated": any(d["checkpoint"] == "rulings" for d in meta["delegations"]),
            },
        },
        "delegations": meta["delegations"],
        "jobs": jobs,
        "control": control,
        "review": (
            {
                "policy": review["policy"],
                "routing": review["routing"],
                "rebuttal_jobs": review["rebuttal_jobs"],
                "responded_actors": sorted(review["rebuttals"]),
                "resolution_job": review["resolution_job"],
            }
            if review
            else None
        ),
        "implementation": "setup-through-lookback",
        "lookback": lookback_state,
        "endgame_jobs": meta.get("endgame_jobs", {}),
    }
    return data, next_actions


def validate(store, strict=False):
    from . import endgame, lookback, rounds
    from .adjudication import audit_all

    meta = project(store)
    for job_id in meta["jobs"]:
        manifest = checked_manifest(store, meta, job_id)
        if manifest["kind"] == "moves" and meta["jobs"][job_id]["ingested"]:
            require(
                store.read(f"moves/move-{manifest['move']}/{manifest['actor_id']}.json")
                is not None,
                "E_COMMITMENT",
                "An ingested move's commitment record is missing.",
            )
    commits = []
    for move in range(1, meta["decision"]["moves"] + 1):
        for record in store.records(f"moves/move-{move}"):
            require(
                digest(record["content"]) == record["content_hash"],
                "E_COMMITMENT",
                "Committed move content changed on disk.",
            )
            lineage = meta["jobs"].get(record["provenance"]["job_id"], {})
            require(
                lineage.get("ingested")
                and lineage.get("actor_id") == record["actor_id"]
                and lineage.get("move") == move
                and lineage.get("kind") == "moves",
                "E_LINEAGE",
                "Committed move lineage is inconsistent.",
            )
            commits.append(record)
    audit_all(store, meta)
    rounds.audit_all(store, meta)
    endgame.audit(store, meta)
    lookback.audit(store, meta)
    models = {
        c["provenance"]["model"] for c in commits if c["provenance"]["origin"] == "simulation"
    }
    simulated_actors = {c["actor_id"] for c in commits if c["provenance"]["origin"] == "simulation"}
    warnings = rounds.health_warnings(store, meta)
    if len(simulated_actors) >= 2 and len(models) == 1:
        warnings.append(
            {"code": "W_SAME_MODEL", "message": "All simulated actors so far used the same model."}
        )
    final = endgame.checked_record(store, meta, "endgame")
    if final:
        warnings = list(
            {
                (w["code"], w["message"]): w for w in [*warnings, *final["health"]["warnings"]]
            }.values()
        )
    if strict and warnings:
        raise PerlaError(
            "E_JUDGMENT_SHARE"
            if any(w["code"] == "E_JUDGMENT_SHARE" for w in warnings)
            else "E_WORKFLOW",
            "Strict validation rejects workflow warnings.",
            warnings[0]["message"],
        )
    return {
        "valid": True,
        "audited_jobs": len(meta["jobs"]),
        "audited_commits": len(commits),
    }, warnings
