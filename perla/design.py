"""Build an experiment in a draft before registering its fixed decision and criteria."""

import json
import re
from contextlib import contextmanager

from pydantic import ValidationError

from . import workflow
from .models import Criterion, Decision, InitInput
from .store import Store


@contextmanager
def draft(root, *, create=False, editing=False):
    store = Store(root)
    store.state = store.root / ".perla-design"
    if editing:
        workflow.require(
            not (store.root / ".perla").exists(),
            "E_SETUP_LOCKED",
            "This game is initialized. Create a fresh directory for a different experiment.",
        )
    if create:
        store.state.mkdir(parents=True, exist_ok=True)
    workflow.require(store.state.is_dir(), "E_NO_DESIGN", "Start with perla game decision set.")
    with store.transaction():
        if editing:
            workflow.require(
                not (store.root / ".perla").exists(),
                "E_SETUP_LOCKED",
                "This game is initialized. Create a fresh directory for a different experiment.",
            )
        yield store, store.read("design.json", {"decision": {}, "criteria": [], "board": {}})


def summary(value):
    try:
        InitInput.model_validate(value)
        issues = []
    except ValidationError as exc:
        issues = [
            {"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()
        ]
    return {"design": value, "ready_to_initialize": not issues, "issues": issues}


def set_decision(root, payload):
    decision = Decision.model_validate(payload).model_dump()
    with draft(root, create=True, editing=True) as (store, value):
        value["decision"] = decision
        store.write("design.json", value)
        return summary(value)


def add_criterion(root, payload, replace=False):
    criterion = Criterion.model_validate(payload).model_dump()
    with draft(root, editing=True) as (store, value):
        workflow.require(
            criterion["deadline_move"] <= value["decision"]["moves"],
            "E_INPUT",
            "Criterion deadline exceeds the decision horizon.",
        )
        existing = next((c for c in value["criteria"] if c["id"] == criterion["id"]), None)
        if existing:
            workflow.require(
                existing == criterion or replace,
                "E_CONFLICT",
                "This criterion already has different content. Use --replace to revise it.",
            )
            value["criteria"][value["criteria"].index(existing)] = criterion
        else:
            workflow.require(len(value["criteria"]) < 5, "E_INPUT", "At most five criteria.")
            value["criteria"].append(criterion)
        store.write("design.json", value)
        return summary(value)


def number(text):
    value = json.loads(text)
    workflow.require(type(value) in (int, float), "E_INPUT", "Supply a finite number.")
    # Reject NaN and infinity before attempting a state write.
    json.dumps(value, allow_nan=False)
    return value


def board_value(root, path, entry, *, append=False):
    workflow.require(
        re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", path),
        "E_INPUT",
        "Use a field name or a dotted path such as period_ends.1.",
    )
    with draft(root, editing=True) as (store, value):
        target = value["board"]
        parts = path.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
            workflow.require(
                isinstance(target, dict), "E_INPUT", "A parent field is not an object."
            )
        if append:
            entries = target.setdefault(parts[-1], [])
            workflow.require(isinstance(entries, list), "E_INPUT", "The field is not a list.")
            if entry not in entries:
                entries.append(entry)
        else:
            target[parts[-1]] = entry
        store.write("design.json", value)
        return summary(value)


def show(root):
    with draft(root) as (_, value):
        return summary(value)


def initialize(root, delegates):
    with draft(root, editing=True) as (_, value):
        return workflow.initialize(root, value, delegates)
