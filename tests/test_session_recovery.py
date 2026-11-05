"""A preserved checkout from an interrupted run lands later, with every normal check."""
from __future__ import annotations

import dataclasses
import fcntl
import json
import shlex
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archon_horizon.cli import app
from archon_horizon.config.loader import build_orchestrator
from archon_horizon.core.tasks import TaskStatus
from archon_horizon.harnesses.null import NullHarness
from archon_horizon.orchestration.recovery import land_checkout, preserved_sessions
from archon_horizon.vcs.git import WorkspaceGit
from archon_horizon.vcs.session import SessionConflict, SessionLedger, boundary_lock

from test_session_ledger import setup, task

HOOK = '''import pathlib, sys
root = pathlib.Path.cwd()
marker = root / ".archon-horizon/hook-runs"
with marker.open("a") as out:
    out.write(" ".join(sys.argv[1:]) + "\\n")
'''


def workspace(tmp_path: Path):
    (tmp_path / "hook.py").write_text(HOOK)
    command = f"{shlex.quote(sys.executable)} hook.py {{task_id}} {{start_sha}} {{end_sha}}"
    ws = setup(tmp_path, {"after_session": [command]})
    orch = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
    return ws, orch


def interrupted(orch, ws, name: str, *, land_first: bool = False, record_checkout: bool = False):
    """An isolated session whose process died: task running, checkout preserved."""
    orch.task_store.put(dataclasses.replace(task(name), status=TaskStatus.RUNNING))
    ledger = SessionLedger(ws, orch.task_store.get(name))
    ledger.acquire()
    active = ledger.start()
    (active.root / f"{name}.lean").write_text(f"session {name}")
    WorkspaceGit(active.root).commit(f"work on {name}", paths=[f"{name}.lean"])
    # Uncommitted edits at the moment of the crash land too; generated files do not.
    (active.root / f"{name}.lean").write_text(f"session {name}, uncommitted")
    session = orch.run_logs.allocate().new_session(f"horizon-{name}")
    meta = {"role": "horizon", "task_id": name, "start_sha": ledger.start_sha,
            "status": "interrupted"}
    if land_first:
        with boundary_lock(ws):
            _, end_sha, _ = ledger.prepare_landing()
        meta.update(end_sha=end_sha)
        ledger.landed = False  # the process died before the hooks and cleanup
    if land_first or record_checkout:
        meta["session_ledger"] = ledger.id
    session.write_meta(meta)
    ledger.close()
    assert ledger.path.is_dir()
    return ledger, session


def hook_runs(ws) -> list[str]:
    path = ws.state_path / "hook-runs"
    return path.read_text().splitlines() if path.exists() else []


def test_unlanded_checkout_is_matched_by_scope_landed_and_judged_by_the_hooks(tmp_path):
    ws, orch = workspace(tmp_path)
    ledger, session = interrupted(orch, ws, "a")
    rows = preserved_sessions(orch)
    assert len(rows) == 1 and rows[0]["task"] == "a" and not rows[0]["landed"]
    preview = land_checkout(orch, ledger.id, dry_run=True)
    assert preview["changed"] == ["a.lean"] and preview["hooks_pending"]
    assert (tmp_path / "a.lean").read_text() == "original"
    result = land_checkout(orch, ledger.id)
    assert result["landed_files"] == ["a.lean"]
    assert (tmp_path / "a.lean").read_text() == "session a, uncommitted"
    # Never inferred done: the interrupted task returns to the queue for the hooks.
    assert orch.task_store.get("a").status is TaskStatus.QUEUED
    assert hook_runs(ws) == [f"a {ledger.start_sha} {result['end_sha']}"]
    meta = session.read_meta()
    assert meta["session_ledger"] == ledger.id and meta["status"] == "queued"
    assert meta["boundary"]["state"] == "passed"
    assert not ledger.path.exists() and result["reclaimed"]
    assert preserved_sessions(orch) == []


def test_already_landed_checkout_only_gets_the_missed_hooks(tmp_path):
    ws, orch = workspace(tmp_path)
    ledger, session = interrupted(orch, ws, "a", land_first=True)
    before = WorkspaceGit(tmp_path).current_sha()
    assert preserved_sessions(orch)[0]["landed"]
    result = land_checkout(orch, ledger.id)
    assert result["already_landed"] and result["landed_files"] == []
    assert WorkspaceGit(tmp_path).current_sha() == before
    assert len(hook_runs(ws)) == 1
    assert not ledger.path.exists()


def test_parent_change_refuses_and_keeps_the_checkout(tmp_path):
    ws, orch = workspace(tmp_path)
    ledger, session = interrupted(orch, ws, "a")
    (tmp_path / "a.lean").write_text("human change")
    with pytest.raises(SessionConflict, match="parent changed"):
        land_checkout(orch, ledger.id)
    assert (tmp_path / "a.lean").read_text() == "human change"
    assert ledger.path.is_dir() and hook_runs(ws) == []
    assert session.read_meta()["status"] == "integration-conflict"


def test_out_of_scope_or_unidentified_checkouts_are_refused(tmp_path):
    ws, orch = workspace(tmp_path)
    ledger, session = interrupted(orch, ws, "a", record_checkout=True)
    (ledger.path / "forbidden.lean").write_text("outside the task")
    with pytest.raises(SessionConflict, match="outside task a"):
        land_checkout(orch, ledger.id)
    assert (tmp_path / "forbidden.lean").read_text() == "original"
    # Without a record and with no scope match, the owner is never guessed.
    other, _ = interrupted(orch, ws, "b")
    (other.path / "forbidden.lean").write_text("outside the task")
    row = next(row for row in preserved_sessions(orch) if row["checkout"] == other.id)
    assert row["owner"] == "unknown"
    with pytest.raises(SessionConflict, match="--session"):
        land_checkout(orch, other.id)


def test_a_live_run_blocks_recovery(tmp_path):
    ws, orch = workspace(tmp_path)
    ledger, _ = interrupted(orch, ws, "a")
    path = ws.state_path / "locks/run-policy.lock"
    with path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        with pytest.raises(SessionConflict, match="a run is live"):
            land_checkout(orch, ledger.id)
    assert (tmp_path / "a.lean").read_text() == "original"


def test_cli_lists_and_lands(tmp_path, monkeypatch):
    ws, orch = workspace(tmp_path)
    ledger, _ = interrupted(orch, ws, "a")
    runner = CliRunner()
    monkeypatch.setattr(sys, "argv", ["horizon", "--json"])  # JSON mode is read from argv
    listed = runner.invoke(app, ["--root", str(tmp_path), "sessions", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["checkouts"][0]["checkout"] == ledger.id
    landed = runner.invoke(app, ["--root", str(tmp_path), "sessions", "land", ledger.id, "--json"])
    assert landed.exit_code == 0, landed.output
    assert json.loads(landed.output)["landed_files"] == ["a.lean"]
    assert (tmp_path / "a.lean").read_text() == "session a, uncommitted"


def test_new_sessions_record_their_checkout_at_start(tmp_path):
    ws, orch = workspace(tmp_path)
    orch.task_store.put(task("a"))
    seen = {}

    class Probe:
        def __init__(self, original):
            self.original = original

        def run_task(self, context):
            sessions = [s for r in orch.run_logs.ids() for s in orch.run_logs.get(r).sessions()]
            seen.update(next(s.read_meta() for s in sessions if "horizon" in s.name))
            return self.original.run_task(context)

    from archon_horizon.core.sessions import Focus, RunRecord

    orch.horizon = Probe(orch.horizon)
    orch.run(RunRecord(id="", focus=Focus(tasks=("a",)), rounds_requested=1))
    assert len(seen.get("session_ledger", "")) == 32
