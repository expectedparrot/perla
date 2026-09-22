"""Audit an already executed Perla/ep CLI case; this script never runs inference."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from edsl import Results


def read(path):
    return json.loads(path.read_text())


def verify(root):
    root = root.resolve()
    repo = Path(__file__).resolve().parents[1]
    perla = repo / ".venv/bin/perla"
    check = subprocess.run(
        [str(perla), "--project", str(root), "workflow", "validate"],
        check=True,
        capture_output=True,
        text=True,
    )
    validation = json.loads(check.stdout)
    assert validation["data"]["valid"]
    meta = read(root / ".perla/project.json")
    assert meta["closed_move"] == 3
    assert len(meta["jobs"]) == 30
    records = []
    for job in meta["jobs"].values():
        assert job["ingested"]
        manifest = read(root / ".perla" / job["manifest"])
        folder = root / f"round-{job['move']}" / job["kind"]
        actor = job["actor_id"]
        results_file = folder / f"{actor}.results.ep"
        receipt = read(folder / f"{actor}.ep-receipt.json")
        assert receipt["exit_code"] == 0 and receipt["stdout"]["status"] == "ok"
        assert receipt["command"].startswith("ep run --jobs ")
        remote = receipt["stdout"]["data"]["meta"]["remote_job"]
        assert remote["wait"]["completed"]
        assert remote["wait"]["status"]["status"] == "completed"
        assert remote["wait"]["status"]["job_uuid"] == remote["job_uuid"]
        results = Results.git.load(results_file)
        assert len(results) == 1
        result = results[0].to_dict()
        assert result["scenario"]["job_id"] == job["id"]
        assert json.loads(result["scenario"]["context_json"]) == manifest["context"]
        assert result["model"]["model"] not in {"test", "human"}
        record_path = folder / f"{actor}.result.json"
        record_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        expected_source = root / ".perla" / manifest["package"]
        assert (folder / f"{actor}.jobs.ep").read_bytes() == expected_source.read_bytes()
        records.append(
            {
                "job_id": job["id"],
                "round": job["move"],
                "kind": job["kind"],
                "actor": actor,
                "model": result["model"]["model"],
                "command": receipt["command"],
                "remote_job_uuid": remote["job_uuid"],
                "results_uuid": remote["wait"]["results_uuid"],
                "results_sha256": hashlib.sha256(results_file.read_bytes()).hexdigest(),
                "results_file": str(results_file.relative_to(root)),
            }
        )
    assert len({r["remote_job_uuid"] for r in records}) == len(records)
    for move, month in [(1, "January"), (2, "February"), (3, "March")]:
        board = read(root / f".perla/board/move-{move}.json")
        assert month in board["calendar_period"]
        for field in [
            "adjusted_cafe_wait_reduction_pct",
            "incremental_daily_contribution_usd",
            "mobile_repeat_change_pp",
        ]:
            assert board[field] is None, (move, field)
        for field in [
            "complete_store_pairs",
            "pilot_cohort_complete_customers",
            "comparison_cohort_complete_customers",
        ]:
            assert board[field] == 0
    for actor in ["home", "dutch", "dunkin", "buyer"]:
        assert read(root / f".perla/dossiers/{actor}.json") == read(
            repo / f"docs/case/starbucks-queue/{actor}.json"
        )
    receipts = list(root.glob("round-*/*/*.ep-receipt*.json"))
    cost = 0
    for path in receipts:
        receipt = read(path)
        if receipt.get("exit_code") == 0:
            status = receipt["stdout"]["data"]["meta"]["remote_job"]["wait"]["status"]
            cost += (status.get("latest_job_run_details") or {}).get("cost_usd") or 0
    summary = {
        "project_id": meta["id"],
        "closed_rounds": 3,
        "accepted_responses": len(records),
        "remote_submissions": len(receipts),
        "reported_cost_usd": round(cost, 6),
        "execution": "Actual ep run CLI calls; named Perla exports and batch imports",
        "submission_order": "Independent actors were submitted concurrently within each stage; stages remained sequential",
        "case_dossiers": "Exact matches to the original tutorial case",
        "actual_measurements": "Absent; observed metrics remain null and sample counts zero",
        "validation": validation,
        "accepted_jobs": records,
    }
    (root / "audit.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in {"validation", "accepted_jobs"}}, indent=2
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("examples/starbucks-queue/cli-live"))
    verify(parser.parse_args().root)
