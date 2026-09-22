"""Sourced observations and reproducible calibration over frozen forecasts."""

import copy
from itertools import combinations

from pydantic import TypeAdapter

from . import endgame, workflow
from .models import ISODate, LookbackInput, Text
from .store import digest, now
from .workflow import require

POPULATIONS = ("team_moves", "agent_rulings", "human_rulings")


def checked(store, meta):
    record = store.read("lookback.json")
    expected = meta.get("lookback_hash")
    require(
        (record is None and expected is None)
        or (record is not None and digest(record) == expected),
        "E_LOOKBACK",
        "Lookback state is missing, modified, or unregistered.",
    )
    return record


def save(store, meta, record):
    store.write("lookback.json", record)
    meta["lookback_hash"] = digest(record)
    store.write("project.json", meta)


def load(store):
    workflow.validate(store)
    meta = workflow.project(store)
    require(
        endgame.checked_record(store, meta, "endgame") is not None,
        "E_ENDGAME_REQUIRED",
        "Finalize endgame before opening lookback.",
    )
    return meta, checked(store, meta), store.read("predictions.json")


def open_lookback(store, author, note):
    author, note = (
        TypeAdapter(Text).validate_python(author),
        TypeAdapter(Text).validate_python(note),
    )
    meta, record, predictions = load(store)
    if record is not None:
        return record
    record = {
        "schema_version": "1.0",
        "artifact_type": "perla.lookback",
        "project_id": meta["id"],
        "registry_hash": digest(predictions),
        "opened_at": now(),
        "opened_by": author,
        "note": note,
        "events": {},
        "scores": [],
    }
    save(store, meta, record)
    return record


def require_open(record):
    require(record is not None, "E_LOOKBACK_REQUIRED", "Run perla lookback open first.")


def validate_events(events, registry):
    predictions = {p["id"]: p for p in registry}
    assigned = set()
    for record in events.values():
        event = record["content"]
        ids = set(event["prediction_ids"])
        require(ids <= predictions.keys(), "E_PREDICTIONS", "Unknown lookback prediction id.")
        require(not ids & assigned, "E_LOOKBACK", "Each prediction may belong to only one event.")
        assigned.update(ids)
        for pid in ids:
            prediction = predictions[pid]
            if event["outcome"] is True:
                require(
                    event["occurred_on"] <= prediction["due_date"],
                    "E_LOOKBACK_DATE",
                    f"Event {event['id']} occurred after {pid}'s registered deadline.",
                )
            elif event["outcome"] is False:
                require(
                    event["as_of"] >= prediction["due_date"],
                    "E_LOOKBACK_DATE",
                    f"Cannot record non-occurrence for {pid} before its deadline.",
                )


def record_outcomes(store, value, author, note):
    submission = LookbackInput.model_validate(value).model_dump()
    author, note = (
        TypeAdapter(Text).validate_python(author),
        TypeAdapter(Text).validate_python(note),
    )
    meta, record, registry = load(store)
    require_open(record)
    record = copy.deepcopy(record)
    submission_hash = digest(submission)
    ids = [e["id"] for e in submission["events"]]
    require(len(ids) == len(set(ids)), "E_LOOKBACK", "Duplicate event ids in submission.")
    for event in submission["events"]:
        old = record["events"].get(event["id"])
        if old:
            require(
                event["statement"] == old["content"]["statement"]
                and set(event["prediction_ids"]) == set(old["content"]["prediction_ids"]),
                "E_LOOKBACK_EVENT",
                "An event's proposition and prediction mapping are immutable; revise only its evidence and outcome.",
            )
            require(
                event["as_of"] >= old["content"]["as_of"],
                "E_LOOKBACK_DATE",
                "A revision cannot move the observation cutoff backwards.",
            )
        event["prediction_ids"] = sorted(event["prediction_ids"])
        record["events"][event["id"]] = {
            "content": event,
            "provenance": {
                "origin": "human",
                "author": author,
                "note": note,
                "recorded_at": now(),
                "submission_hash": submission_hash,
                "mapping": "retrospective_operator_assertion",
            },
            "history": [*old["history"], {k: old[k] for k in ("content", "provenance")}]
            if old
            else [],
        }
    validate_events(record["events"], registry)
    save(store, meta, record)
    return show_data(record, registry)


def summarize(rows):
    resolved = [r for r in rows if r["status"] == "resolved"]
    scored = [r for r in resolved if r["brier_score"] is not None]
    bins = []
    for index in range(10):
        members = [r for r in scored if min(int(r["probability"] * 10), 9) == index]
        bins.append(
            {
                "lower": index / 10,
                "upper": (index + 1) / 10,
                "upper_inclusive": index == 9,
                "count": len(members),
                "mean_probability": sum(r["probability"] for r in members) / len(members)
                if members
                else None,
                "observed_frequency": sum(r["outcome"] for r in members) / len(members)
                if members
                else None,
            }
        )
    return {
        "registered": len(rows),
        "resolved": len(resolved),
        "occurred": sum(r["outcome"] for r in resolved),
        "not_occurred": sum(not r["outcome"] for r in resolved),
        "unrecorded": sum(r["status"] == "unrecorded" for r in rows),
        "unresolved": sum(r["status"] == "unresolved" for r in rows),
        "unresolvable": sum(r["status"] == "unresolvable" for r in rows),
        "scored": len(scored),
        "resolved_without_probability": len(resolved) - len(scored),
        "brier_score": sum(r["brier_score"] for r in scored) / len(scored) if scored else None,
        "calibration_bins": bins,
    }


def compute(registry, events, as_of):
    as_of = TypeAdapter(ISODate).validate_python(as_of)
    validate_events(events, registry)
    require(
        all(e["content"]["as_of"] <= as_of for e in events.values()),
        "E_LOOKBACK_DATE",
        "The score cutoff must include every current observation; historical scores retain their snapshots.",
    )
    mapping = {pid: e for e in events.values() for pid in e["content"]["prediction_ids"]}
    rows = []
    for prediction in registry:
        observation = mapping.get(prediction["id"])
        event = observation["content"] if observation else None
        outcome = event["outcome"] if event else None
        probability = prediction["probability"]
        rows.append(
            {
                **prediction,
                "event_id": event["id"] if event else None,
                "status": event["status"] if event else "unrecorded",
                "outcome": outcome,
                "overdue": prediction["due_date"] < as_of and outcome is None,
                "observation_provenance": observation["provenance"] if observation else None,
                "brier_score": (probability - int(outcome)) ** 2
                if outcome is not None and probability is not None
                else None,
            }
        )
    populations = {
        pop: summarize([r for r in rows if r["population"] == pop]) for pop in POPULATIONS
    }
    basis_types = sorted({r["basis"] or "not_applicable" for r in rows})
    basis = {
        kind: summarize([r for r in rows if (r["basis"] or "not_applicable") == kind])
        for kind in basis_types
    }
    paired = []
    for left, right in combinations(POPULATIONS, 2):
        event_sets = {
            pop: {
                r["event_id"]
                for r in rows
                if r["population"] == pop and r["brier_score"] is not None
            }
            for pop in (left, right)
        }
        common = sorted(event_sets[left] & event_sets[right])
        summaries = {}
        for pop in (left, right):
            selected = [
                r
                for r in rows
                if r["population"] == pop
                and r["event_id"] in common
                and r["brier_score"] is not None
            ]
            stats = summarize(selected)
            event_scores = [
                sum(r["brier_score"] for r in selected if r["event_id"] == eid)
                / sum(r["event_id"] == eid for r in selected)
                for eid in common
            ]
            stats["event_weighted_brier_score"] = (
                sum(event_scores) / len(common) if common else None
            )
            summaries[pop] = stats
        paired.append(
            {
                "populations": [left, right],
                "event_ids": common,
                "event_count": len(common),
                "scores": summaries,
            }
        )
    warnings = []
    summary = summarize(rows)
    if not summary["scored"]:
        warnings.append(
            {
                "code": "W_NO_PROBABILITY_SCORES",
                "message": "No resolved forecasts have registered probabilities; calibration is unavailable.",
            }
        )
    if summary["resolved"] < len(rows):
        warnings.append(
            {
                "code": "W_LOOKBACK_INCOMPLETE",
                "message": "Some predictions remain unrecorded, unresolved, or unresolvable; they are excluded from scoring.",
            }
        )
    if not any(pair["event_count"] for pair in paired):
        warnings.append(
            {
                "code": "W_NO_MATCHED_EVENTS",
                "message": "No probability-bearing resolved events are shared across populations; comparative scores are unavailable.",
            }
        )
    return {
        "as_of": as_of,
        "predictions": rows,
        "summary": summary,
        "by_population": populations,
        "by_basis": basis,
        "by_population_and_basis": {
            pop: {
                kind: summarize(
                    [
                        r
                        for r in rows
                        if r["population"] == pop and (r["basis"] or "not_applicable") == kind
                    ]
                )
                for kind in basis_types
            }
            for pop in POPULATIONS
        },
        "matched_comparisons": paired,
        "warnings": warnings,
        "method": {
            "brier_score": "mean((registered_probability - boolean_outcome)^2); lower is better",
            "bins": "Ten equal-width bins; lower inclusive, upper exclusive except 1.0 in the final bin",
            "weighting": "Summaries and bins weight forecasts equally; paired event_weighted_brier_score weights shared events equally",
            "mapping": "Operator asserts grouped predictions concern the same proposition and resolution criteria; mapping is retrospective",
            "provenance": "Population identifies the source resolver. Registration provenance identifies who supplied probabilities; these may be different people or models.",
            "limits": "Descriptive local-game scores, not causal evidence of population superiority. Sources and event equivalence require human review. No probabilities are inferred or changed at lookback.",
        },
    }


def score(store, as_of):
    meta, record, registry = load(store)
    require_open(record)
    data = compute(registry, record["events"], as_of)
    latest = record["scores"][-1] if record["scores"] else None
    if (
        latest
        and latest["events_hash"] == digest(record["events"])
        and latest["result"]["as_of"] == as_of
    ):
        return latest
    record = copy.deepcopy(record)
    snapshot = {
        "id": f"s{len(record['scores']) + 1:03d}",
        "computed_at": now(),
        "registry_hash": record["registry_hash"],
        "events_hash": digest(record["events"]),
        "events": copy.deepcopy(record["events"]),
        "result": data,
    }
    record["scores"].append(snapshot)
    save(store, meta, record)
    return snapshot


def show_data(record, registry):
    latest = record["scores"][-1] if record["scores"] else None
    return {
        "opened_at": record["opened_at"],
        "registry_hash": record["registry_hash"],
        "registry": registry,
        "events": record["events"],
        "latest_score": latest,
        "score_count": len(record["scores"]),
        "score_stale": bool(latest and latest["events_hash"] != digest(record["events"])),
    }


def show(store):
    _, record, registry = load(store)
    require_open(record)
    return show_data(record, registry)


def audit(store, meta):
    record = checked(store, meta)
    if record is None:
        return
    registry = store.read("predictions.json")
    require(
        endgame.checked_record(store, meta, "endgame") is not None
        and record["registry_hash"] == digest(registry)
        and record["project_id"] == meta["id"],
        "E_LOOKBACK",
        "Lookback does not match the finalized prediction registry.",
    )
    validate_events(record["events"], registry)
    for snapshot in record["scores"]:
        require(
            snapshot["registry_hash"] == record["registry_hash"]
            and snapshot["events_hash"] == digest(snapshot["events"])
            and snapshot["result"]
            == compute(registry, snapshot["events"], snapshot["result"]["as_of"]),
            "E_LOOKBACK",
            "Lookback score differs from its registered evidence snapshot.",
        )
