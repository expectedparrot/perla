"""Audit a completed live case and summarize the actual remote execution records."""

import argparse
import hashlib
import io
import json
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

from perla.cli import main


def read(path):
    return json.loads(path.read_text())


def verify(root):
    with redirect_stdout(io.StringIO()) as output:
        code = main(["--project", str(root), "workflow", "validate"])
    envelope = json.loads(output.getvalue())
    assert code == 0 and envelope["data"]["valid"], envelope
    meta = read(root / ".perla/project.json")
    assert meta["closed_move"] == 3
    counts = Counter()
    accepted = []
    for receipt in sorted((root / "execution").glob("*/accepted.json")):
        folder = receipt.parent
        attempt = read(receipt)["attempt"]
        manifest = read(folder / "manifest.json")
        result = read(folder / f"result-{attempt}.json")
        status = read(folder / f"status-{attempt}.json")
        remote = read(folder / f"remote-{attempt}.json")
        assert status["status"] == "completed"
        assert status["job_uuid"] == (remote.get("job_uuid") or remote["uuid"])
        assert result["model"]["model"] not in {"test", "human"}
        assert result["scenario"]["job_id"] == manifest["id"]
        assert json.loads(result["scenario"]["context_json"]) == manifest["context"]
        assert result["answer"] == read(folder / f"answer-{attempt}.json")
        assert meta["jobs"][manifest["id"]]["ingested"]
        package = folder / f"results-{attempt}.ep"
        counts[manifest["kind"]] += 1
        accepted.append(
            {
                "job_id": manifest["id"],
                "round": manifest["move"],
                "kind": manifest["kind"],
                "actor": manifest["actor_id"],
                "model": result["model"]["model"],
                "attempt": attempt,
                "remote_job_uuid": status["job_uuid"],
                "results_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            }
        )
    assert counts == {"moves": 12, "rulings": 3, "rebuttals": 12, "resolutions": 3}, counts
    debt = []
    for move in range(1, 4):
        closure = read(root / "execution" / f"round-{move}-closed.json")
        board = read(root / ".perla/board" / f"move-{move}.json")
        assert board == closure["board"]
        for field in [
            "adjusted_cafe_wait_reduction_pct",
            "incremental_daily_contribution_usd",
            "mobile_repeat_change_pp",
        ]:
            assert board[field] is None, (move, field)
        assert board["complete_store_pairs"] == 0
        assert board["pilot_cohort_complete_customers"] == 0
        assert board["comparison_cohort_complete_customers"] == 0
        debt.extend(f"round-{move}:{r}" for r in closure["escalation_debt"])
    statuses = [read(p) for p in (root / "execution").glob("*/status-*.json")]
    summary = {
        "project_id": meta["id"],
        "closed_rounds": 3,
        "accepted_responses": len(accepted),
        "response_counts": dict(counts),
        "rejected_attempts": len(list((root / "execution").glob("*/rejection-*.json"))),
        "remote_jobs": len(list((root / "execution").glob("*/remote-*.json"))),
        "reported_cost_usd": round(
            sum((s.get("latest_job_run_details") or {}).get("cost_usd") or 0 for s in statuses), 6
        ),
        "escalation_debt": debt,
        "final_calendar_period": board["calendar_period"],
        "actual_measurements": "absent; all decision metrics remain null and observed sample counts zero",
        "audit": envelope["data"],
        "accepted_jobs": accepted,
    }
    (root / "execution/audit.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "accepted_jobs"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("examples/starbucks-queue/dated-live"))
    args = parser.parse_args()
    verify(args.root.resolve())
