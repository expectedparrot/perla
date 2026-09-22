"""Atomic loading of a readable cast manifest and the dossiers it references."""

import getpass
from pathlib import Path
from typing import Annotated

from pydantic import Field

from . import workflow
from .models import Actor, Dossier, Record, Text
from .store import read_json


class CastActor(Actor):
    dossier: Text


class Cast(Record):
    actors: Annotated[list[CastActor], Field(min_length=1, max_length=6)]


def load(store, path):
    workflow.check_setup(workflow.project(store))
    path = Path(path).resolve()
    cast = Cast.model_validate(read_json(path))
    ids = [a.id for a in cast.actors]
    workflow.require(len(set(ids)) == len(ids), "E_INPUT", "Cast actor ids must be unique.")
    prepared = []
    for entry in cast.actors:
        source = (path.parent / entry.dossier).resolve()
        workflow.require(
            source.is_relative_to(path.parent),
            "E_INVALID_PATH",
            "Dossiers must be inside the case directory.",
        )
        dossier = Dossier.model_validate(read_json(source)).model_dump()
        actor = entry.model_dump(exclude={"dossier"})
        existing = store.read(f"actors/{entry.id}.json")
        if existing:
            workflow.require(
                existing == actor
                and store.read(f"dossiers/{entry.id}.json") == dossier
                and not store.read(f"dossier-drafts/{entry.id}.json"),
                "E_EXISTS",
                f"Actor {entry.id} already has different settings or dossier content.",
                "Inspect the existing actor; loading a case never replaces your edits.",
            )
        prepared.append((actor, dossier, bool(existing)))
    added, reused = [], []
    for actor, dossier, exists in prepared:
        if exists:
            reused.append(actor["id"])
            continue
        workflow.add_actor(store, actor)
        workflow.save_dossier(
            store,
            actor["id"],
            dossier,
            getpass.getuser(),
            f"Loaded complete dossier from cast manifest {path.name}",
            origin="operator",
        )
        added.append(actor["id"])
    return {"loaded": added, "already_loaded": reused, "actors": len(prepared)}
