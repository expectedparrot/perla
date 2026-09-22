"""Build sourced dossiers a statement at a time, without editing JSON files."""

import getpass
from uuid import uuid4

from pydantic import TypeAdapter

from . import workflow
from .models import Dossier, Fact, Text
from .store import digest, now

FIELDS = {
    "incentive": "incentives",
    "constraint": "constraints",
    "capability": "capabilities",
    "red-line": "red_lines",
}


def current(store, actor):
    workflow.check_setup(workflow.project(store))
    workflow.actor(store, actor)
    return (
        store.read(f"dossier-drafts/{actor}.json")
        or store.read(f"dossiers/{actor}.json")
        or {"facts": [], **{field: [] for field in FIELDS.values()}}
    )


def missing(value):
    return [key for key in ("facts", *FIELDS.values()) if not value[key]]


def show(store, actor):
    draft = store.read(f"dossier-drafts/{actor}.json")
    if draft:
        return {**draft, "draft": True, "missing": missing(draft)}
    return store.read(f"dossiers/{actor}.json")


def record(store, actor, before, after, operation):
    remaining = missing(after)
    if not remaining:
        after = Dossier.model_validate(after).model_dump()
    if after == before:
        return {"actor_id": actor, "changed": False, "draft": bool(remaining), "missing": remaining}
    event = {
        "id": "edit-" + uuid4().hex,
        "actor_id": actor,
        "origin": "operator",
        "participant_id": getpass.getuser(),
        "rationale": operation,
        "recorded_at": now(),
        "before": before,
        "after": after,
    }
    store.write(f"ingest/{event['id']}.json", event)
    if remaining:
        store.write(f"dossier-drafts/{actor}.json", after)
    else:
        store.write(f"dossiers/{actor}.json", after)
        store.write(f"dossier-drafts/{actor}.json", None)
    return {
        "actor_id": actor,
        "changed": True,
        "draft": bool(remaining),
        "missing": remaining,
        "dossier_hash": digest(after),
        "event_id": event["id"],
    }


def add_fact(store, actor, fact_id, claim, sources, replace=False):
    before = current(store, actor)
    fact = Fact.model_validate({"id": fact_id, "claim": claim, "sources": sources}).model_dump()
    existing = next((f for f in before["facts"] if f["id"] == fact_id), None)
    workflow.require(
        existing is None or existing == fact or replace,
        "E_EXISTS",
        f"Fact {fact_id} already has a different claim or source.",
        "Use --replace to record an explicit revision of this fact.",
    )
    facts = [fact if f["id"] == fact_id else f for f in before["facts"]]
    if existing is None:
        facts.append(fact)
    return record(store, actor, before, {**before, "facts": facts}, f"Set dossier fact {fact_id}")


def add_entry(store, actor, kind, text):
    before = current(store, actor)
    text = TypeAdapter(Text).validate_python(text)
    field = FIELDS[kind]
    entries = before[field] if text in before[field] else [*before[field], text]
    return record(store, actor, before, {**before, field: entries}, f"Add dossier {kind}")
