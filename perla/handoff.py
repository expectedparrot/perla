"""Named file handoff to the ep CLI; no inference or model selection here."""

import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

from . import workflow
from .store import PerlaError, atomic_json, read_json


def export_batch(store, generated, directory):
    directory = Path(directory).resolve()
    workflow.require(
        not directory.is_relative_to(store.state),
        "E_INVALID_PATH",
        "Export outside the canonical .perla directory.",
    )
    meta = workflow.project(store)
    entries = []
    sources = {}
    for job in generated.get("jobs", [generated]):
        manifest = workflow.checked_manifest(store, meta, job["job_id"])
        actor = manifest["actor_id"]
        workflow.identifier(actor)
        package = f"{actor}.jobs.ep"
        workflow.require(
            package not in sources, "E_EXPORT", "An export needs distinct actor names."
        )
        sources[package] = store.path(manifest["package"])
        entries.append(
            {
                "job_id": manifest["id"],
                "actor_id": actor,
                "kind": manifest["kind"],
                "move": manifest["move"],
                "package": package,
                "results": f"{actor}.results.ep",
            }
        )
    batch = {"version": 1, "project_id": meta["id"], "jobs": entries}
    if directory.exists():
        workflow.require(
            (directory / "batch.json").is_file() and read_json(directory / "batch.json") == batch,
            "E_EXPORT_EXISTS",
            "The export directory already contains a different batch.",
            "Choose a new --output directory; existing files are never replaced.",
        )
        for name, source in sources.items():
            workflow.require(
                (directory / name).is_file()
                and (directory / name).read_bytes() == source.read_bytes(),
                "E_ISOLATION",
                f"Exported package {name} changed or is missing.",
            )
    else:
        directory.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".perla-export-", dir=directory.parent))
        try:
            for name, source in sources.items():
                shutil.copyfile(source, temporary / name)
            atomic_json(temporary / "batch.json", batch)
            temporary.rename(directory)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return {"directory": str(directory), **batch}


def ingest_batch(store, kind, directory, human=False):
    """The caller's store transaction makes batch validation/commit all-or-nothing."""
    directory = Path(directory).resolve()
    batch = read_json(directory / "batch.json")
    meta = workflow.project(store)
    workflow.require(
        isinstance(batch, dict)
        and batch.get("version") == 1
        and batch.get("project_id") == meta["id"]
        and isinstance(batch.get("jobs"), list)
        and batch["jobs"],
        "E_LINEAGE",
        "This is not a batch from the selected project.",
    )
    pending, skipped, seen = [], [], set()
    for entry in batch["jobs"]:
        workflow.require(isinstance(entry, dict), "E_LINEAGE", "Invalid batch entry.")
        job_id = entry.get("job_id")
        workflow.require(
            isinstance(job_id, str) and job_id not in seen,
            "E_LINEAGE",
            "Invalid or duplicate batch job.",
        )
        seen.add(job_id)
        manifest = workflow.checked_manifest(store, meta, job_id)
        actor = manifest["actor_id"]
        workflow.require(
            entry
            == {
                "job_id": job_id,
                "actor_id": actor,
                "kind": kind,
                "move": manifest["move"],
                "package": f"{actor}.jobs.ep",
                "results": f"{actor}.results.ep",
            }
            and manifest["kind"] == kind,
            "E_LINEAGE",
            "Batch entries must match the registered jobs and requested kind.",
        )
        if meta["jobs"][job_id]["ingested"]:
            skipped.append(job_id)
            continue
        source = directory / entry["results"]
        if not source.is_file():
            raise PerlaError(
                "E_RESULTS_MISSING",
                f"Missing Results: {source}",
                "Run ep with --output pointing to this file, then repeat the import.",
            )
        pending.append((job_id, source))
    imported = []
    for job_id, source in pending:
        try:
            imported.append(workflow.ingest(store, kind, job_id, source, human))
        except PerlaError as exc:
            raise PerlaError(exc.code, f"Results {source}: {exc.message}", exc.remediation) from exc
        except ValidationError as exc:
            raise PerlaError(
                "E_INPUT",
                f"Results {source}: {exc}",
                "Preserve the rejected Results and obtain a new answer for this job, then repeat the import.",
            ) from exc
    return {"imported": imported, "already_ingested": skipped}
