"""Directory-scoped project selection; explicit paths always take precedence."""

import os
from pathlib import Path

from .store import PerlaError, Store, atomic_json, read_json

FILENAME = ".perla-project.json"


def resolve(explicit=None, start=None):
    start = Path(start or Path.cwd()).resolve()
    if explicit is not None:
        return Path(explicit).expanduser().resolve(), "argument"
    if os.environ.get("PERLA_PROJECT"):
        return Path(os.environ["PERLA_PROJECT"]).expanduser().resolve(), "environment"
    for directory in (start, *start.parents):
        # A real project takes precedence over a selection in an ancestor workspace.
        if (directory / ".perla" / "project.json").is_file():
            return directory, "current_directory"
        pointer = directory / FILENAME
        if pointer.is_file():
            value = read_json(pointer)
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != "1.0"
                or not isinstance(value.get("project"), str)
            ):
                raise PerlaError("E_PROJECT_SELECTION", f"Invalid project selection: {pointer}")
            target = Path(value["project"])
            if not target.is_absolute() or not (target / ".perla" / "project.json").is_file():
                raise PerlaError(
                    "E_PROJECT_SELECTION",
                    f"Selected project is missing: {target}",
                    "Run perla project set PATH or perla project unset from the selection directory.",
                )
            return target.resolve(), str(pointer)
    return start, "current_directory"


def select(path, start=None):
    directory = Path(start or Path.cwd()).resolve()
    store = Store.discover(Path(path).expanduser())
    from .workflow import project

    with store.transaction():
        project(store)
    pointer = directory / FILENAME
    atomic_json(pointer, {"schema_version": "1.0", "project": str(store.root)})
    return {"project": str(store.root), "selection_file": str(pointer), "scope": str(directory)}


def unset(start=None):
    directory = Path(start or Path.cwd()).resolve()
    pointer = directory / FILENAME
    existed = pointer.exists()
    pointer.unlink(missing_ok=True)
    return {"selection_file": str(pointer), "removed": existed}
