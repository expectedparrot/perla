"""Export and ingest a real EDSL game; inference uses the EDSL remote API.

Use --root to keep independent runs separate. This script never constructs Results,
substitutes answers, or records a human assessment. See docs/index.html.
"""

import argparse
import io
import json
import shlex
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from edsl import Coop, Jobs, Model, ModelList, Results

from perla.cli import main
from perla.edsl_adapter import response_format_notes

REPO = Path(__file__).resolve().parents[2]
CASE = REPO / "docs/case/starbucks-queue"
MODELS = {
    "home": ("openai", "gpt-4.1"),
    "dutch": ("google", "gemini-2.5-flash"),
    "dunkin": ("openai", "gpt-4.1-mini"),
    "buyer": ("google", "gemini-2.5-pro"),
    "control": ("openai", "gpt-4.1"),
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def run(root, *args):
    with redirect_stdout(io.StringIO()) as stream:
        code = main(["--project", str(root), *map(str, args)])
    envelope = json.loads(stream.getvalue())
    if code:
        raise RuntimeError(json.dumps(envelope["error"]))
    return envelope["data"]


def plan(root, jobs):
    execution = root / "execution"
    pending = []
    for job in jobs:
        manifest = json.loads((root / ".perla/jobs" / f"{job['job_id']}.manifest.json").read_text())
        meta = json.loads((root / ".perla/project.json").read_text())
        if meta["jobs"][job["job_id"]]["ingested"]:
            continue
        actor = manifest["actor_id"]
        service, model = MODELS.get(actor, MODELS["control"])
        label = f"round-{manifest['move']}-{manifest['kind']}-{actor}"
        folder = execution / label
        folder.mkdir(parents=True, exist_ok=True)
        attempts = sorted(folder.glob("attempt-*.json"), key=lambda p: int(p.stem.split("-")[-1]))
        if attempts:
            existing = json.loads(attempts[-1].read_text())
            if not (folder / f"rejection-{existing['attempt']}.json").exists():
                pending.append(existing)
                continue
        package = Jobs.git.load(job["package"])
        write(folder / "manifest.json", manifest)
        write(folder / "prompts.json", package.prompts().to_dict())
        # Kept separate so the registered Jobs package remains model-free and immutable.
        model_list = folder / "model.ep"
        ModelList([Model(model, service_name=service, max_tokens=16000, temperature=0.5)]).git.save(
            model_list
        )
        attempt = int(attempts[-1].stem.split("-")[-1]) + 1 if attempts else 1
        result = folder / f"results-{attempt}.ep"
        command = [
            "ep",
            "run",
            "--jobs",
            job["package"],
            "--model_list",
            str(model_list),
            "--background",
            "--wait",
            "--timeout",
            "900",
            "--task-timeout",
            "600",
            "--remote_inference_results_visibility",
            "private",
            "--fresh",
            "--output",
            str(result),
        ]
        record = {
            **job,
            "actor_id": actor,
            "kind": manifest["kind"],
            "move": manifest["move"],
            "label": label,
            "service": service,
            "model": model,
            "attempt": attempt,
            "result": str(result),
            "command": command,
            "prepared_at": datetime.now(timezone.utc).isoformat(),
        }
        write(folder / f"attempt-{attempt}.json", record)
        pending.append(record)
    write(execution / "pending.json", pending)
    for record in pending:
        print(shlex.join(record["command"]))


def execute(args):
    root = args.root.resolve()
    if args.stage == "play":

        def stage(name, move):
            execute(argparse.Namespace(root=root, stage=name, move=move))

        def complete_pending(move):
            while True:
                pending = json.loads((root / "execution/pending.json").read_text())
                if not pending:
                    return
                if max(item["attempt"] for item in pending) > args.max_attempts:
                    raise RuntimeError(
                        "Attempt limit reached; inspect rejection artifacts before increasing --max-attempts"
                    )
                stage("submit", move)
                started = time.monotonic()
                while True:
                    stage("fetch", move)
                    if all(Path(item["result"]).exists() for item in pending):
                        break
                    for item in pending:
                        status = json.loads(
                            (
                                root
                                / "execution"
                                / item["label"]
                                / f"status-{item['attempt']}.json"
                            ).read_text()
                        )
                        if status["status"] in {"failed", "cancelled", "partially_failed"}:
                            raise RuntimeError(
                                f"Remote job failed: {item['label']}; inspect status artifact"
                            )
                    if time.monotonic() - started > 900:
                        raise RuntimeError(
                            "Remote jobs are still pending after 15 minutes; resume play later"
                        )
                    time.sleep(15)
                try:
                    stage("ingest", move)
                    return
                except SystemExit as exc:
                    if exc.code != 1:
                        raise
                    # A rejected structured answer is retried through real inference;
                    # the original Results and rejection stay on disk.

        first = json.loads((root / ".perla/project.json").read_text())["closed_move"] + 1
        for move in range(first, 4):
            print(f"ROUND {move}", flush=True)
            stage("moves", move)
            complete_pending(move)
            stage("control", move)
            complete_pending(move)
            stage("route", move)
            stage("rebuttals", move)
            complete_pending(move)
            stage("resolve", move)
            complete_pending(move)
            stage("close", move)
    elif args.stage == "setup":
        run(
            root,
            "init",
            "--input",
            CASE / "game.json",
            "--delegate",
            "dossiers",
            "--delegate",
            "rulings",
        )
        run(root, "adjudicate", "policy", "set", "--input", CASE / "live-policy.json")
        for actor, name, role in [
            ("home", "Starbucks", "home"),
            ("dutch", "Dutch Bros", "competitor"),
            ("dunkin", "Dunkin", "competitor"),
            ("buyer", "Morning customers (composite)", "wildcard"),
        ]:
            run(root, "actor", "add", actor, "--name", name, "--role", role)
            run(
                root,
                "dossier",
                "edit",
                actor,
                "--input",
                CASE / f"{actor}.json",
                "--by",
                "automated-documentation-setup",
                "--note",
                "Sourced public research and labeled author assumptions; model-only demonstration, no human approval claimed",
            )
        write(
            root / "execution/run.json",
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "models": MODELS,
                "human_review": "Delegated for this model-only documentation demonstration",
                "human_budget": 0,
                "source_case": "docs/case/starbucks-queue",
                "rounds": 3,
            },
        )
        print(json.dumps(run(root, "status"), indent=2))
    elif args.stage == "submit":
        for item in json.loads((root / "execution/pending.json").read_text()):
            folder = root / "execution" / item["label"]
            receipt_path = folder / f"remote-{item['attempt']}.json"
            if receipt_path.exists():
                print(item["label"], "already submitted", flush=True)
                continue
            job = Jobs.git.load(item["package"]).by(ModelList.git.load(folder / "model.ep"))
            manifest = json.loads((folder / "manifest.json").read_text())
            # Older exports used symbolic d.ACTOR.FACT instructions. A rejected job
            # may receive concrete formatting guidance derived from its unchanged
            # evidence. Save the exact execution package; never edit an answer or
            # the registered package. Returned prompts also preserve this addition.
            if (
                item["attempt"] > 1
                and "Allowed evidence citations" not in job.survey.questions[0].question_text
            ):
                notes = response_format_notes(manifest)
                job.survey.questions[0].question_text += notes
                write(
                    folder / f"format-clarification-{item['attempt']}.json",
                    {
                        "text": notes,
                        "reason": "Clarify schema and citation spelling after structural rejection",
                        "evidence_changed": False,
                    },
                )
            job.git.save(folder / f"execution-{item['attempt']}.ep")
            receipt = Coop().remote_inference_create(
                job,
                description=f"Perla Starbucks {item['label']}",
                visibility="private",
                initial_results_visibility="private",
                iterations=1,
                fresh=True,
                task_timeout=600,
            )
            write(receipt_path, receipt)
            print(item["label"], json.dumps(receipt, default=str), flush=True)
    elif args.stage == "fetch":
        for item in json.loads((root / "execution/pending.json").read_text()):
            folder = root / "execution" / item["label"]
            receipt = json.loads((folder / f"remote-{item['attempt']}.json").read_text())
            status = Coop().remote_inference_get(
                job_uuid=receipt.get("job_uuid") or receipt["uuid"]
            )
            write(folder / f"status-{item['attempt']}.json", status)
            print(item["label"], status["status"], flush=True)
            if status["status"] == "completed" and not Path(item["result"]).exists():
                Results.pull(status["results_uuid"]).git.save(item["result"])
    elif args.stage == "ingest":
        pending = json.loads((root / "execution/pending.json").read_text())
        failures = []
        for item in pending:
            folder = root / "execution" / item["label"]
            meta = json.loads((root / ".perla/project.json").read_text())
            if meta["jobs"][item["job_id"]]["ingested"]:
                continue
            if not Path(item["result"]).exists():
                raise RuntimeError(
                    f"Results not yet downloaded for {item['label']}; fetch before ingestion"
                )
            try:
                result = Results.git.load(item["result"])
                write(folder / f"answer-{item['attempt']}.json", dict(result[0].answer))
                write(folder / f"result-{item['attempt']}.json", result[0].to_dict())
                receipt = run(
                    root,
                    "ingest",
                    item["kind"],
                    "--job",
                    item["job_id"],
                    "--source",
                    item["result"],
                )
                write(folder / "accepted.json", {"attempt": item["attempt"], "receipt": receipt})
                print(item["label"], "accepted")
            except Exception as exc:
                write(folder / f"rejection-{item['attempt']}.json", {"error": str(exc)})
                failures.append(
                    {
                        "job_id": item["job_id"],
                        "package": item["package"],
                        "actor_id": item["actor_id"],
                    }
                )
                print(item["label"], "REJECTED", str(exc))
        if failures:
            plan(root, failures)
            raise SystemExit(1)
    elif args.stage in {"moves", "rebuttals"}:
        plan(root, run(root, "job", "generate", args.stage, "--move", args.move)["jobs"])
    elif args.stage == "control":
        plan(root, [run(root, "adjudicate", "open", "--move", args.move)])
    elif args.stage == "route":
        print(json.dumps(run(root, "adjudicate", "route", "--move", args.move), indent=2))
    elif args.stage == "resolve":
        plan(root, [run(root, "adjudicate", "resolve", "--move", args.move)])
    elif args.stage == "close":
        receipt = run(root, "adjudicate", "close", "--move", args.move)
        write(root / "execution" / f"round-{args.move}-closed.json", receipt)
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=[
            "setup",
            "play",
            "submit",
            "fetch",
            "moves",
            "ingest",
            "control",
            "route",
            "rebuttals",
            "resolve",
            "close",
        ],
    )
    parser.add_argument("--move", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--root", type=Path, default=REPO / "examples/starbucks-queue/live")
    execute(parser.parse_args())
