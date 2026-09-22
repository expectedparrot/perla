"""Protect replay from duplicate submissions and incomplete-result resampling."""

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "examples/starbucks-queue/run_live.py"
spec = importlib.util.spec_from_file_location("starbucks_live_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def pending(root):
    item = {
        "job_id": "job-one",
        "label": "round-1-moves-home",
        "attempt": 1,
        "result": str(root / "execution/round-1-moves-home/results-1.ep"),
    }
    runner.write(root / "execution/pending.json", [item])
    return item


def test_resume_does_not_submit_existing_remote_job(tmp_path, monkeypatch):
    item = pending(tmp_path)
    runner.write(tmp_path / "execution" / item["label"] / "remote-1.json", {"uuid": "remote-one"})

    def unexpected_network():
        pytest.fail("Resuming an existing remote job must not submit or create a new client")

    monkeypatch.setattr(runner, "Coop", unexpected_network)
    runner.execute(argparse.Namespace(root=tmp_path, stage="submit", move=1))


def test_fetch_uses_creation_uuid_and_preserves_download(tmp_path, monkeypatch):
    item = pending(tmp_path)
    runner.write(tmp_path / "execution" / item["label"] / "remote-1.json", {"uuid": "remote-one"})
    Path(item["result"]).write_bytes(b"existing-results")

    class Client:
        def remote_inference_get(self, *, job_uuid):
            assert job_uuid == "remote-one"
            return {"status": "completed", "results_uuid": "result-one"}

    monkeypatch.setattr(runner, "Coop", Client)
    runner.execute(argparse.Namespace(root=tmp_path, stage="fetch", move=1))
    assert Path(item["result"]).read_bytes() == b"existing-results"
    status = json.loads((tmp_path / "execution" / item["label"] / "status-1.json").read_text())
    assert status["status"] == "completed"


def test_missing_results_do_not_create_rejection_or_new_attempt(tmp_path):
    item = pending(tmp_path)
    runner.write(tmp_path / ".perla/project.json", {"jobs": {item["job_id"]: {"ingested": False}}})
    with pytest.raises(RuntimeError, match="Results not yet downloaded"):
        runner.execute(argparse.Namespace(root=tmp_path, stage="ingest", move=1))
    assert not list(tmp_path.rglob("rejection-*.json"))
    assert json.loads((tmp_path / "execution/pending.json").read_text())[0]["attempt"] == 1
