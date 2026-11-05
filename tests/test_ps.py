"""`horizon ps`: the per-run process registry — list, reap zombies."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from archon_horizon.cli import main

_CONFIG = "workspace:\n  name: w\n  horizon_agent: {harness: hor}\nharnesses:\n  hor: {kind: \"null\"}\nprojects: {}\n"


def _marker(ws: Path, run_id: str, pid: int) -> Path:
    run_dir = ws / ".archon-horizon" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "process.json"
    path.write_text(json.dumps({"pid": pid, "host": __import__("socket").gethostname(),
                                "started_at": "2026-07-16T00:00:00+00:00"}), "utf-8")
    return path


def test_ps_lists_live_and_auto_reaps_zombies(tmp_path: Path, capsys) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(_CONFIG, "utf-8")
    live = _marker(ws, "0001", os.getpid())        # alive: this test process
    dead = _marker(ws, "0002", 2 ** 22 + 12345)    # almost certainly dead
    # Leave a still-running session meta under the dead run so reaping must
    # finalize it to interrupted (session-states hygiene).
    session = ws / ".archon-horizon" / "runs" / "0002" / "sessions" / "0001-horizon-T"
    session.mkdir(parents=True)
    (session / "meta.json").write_text(
        json.dumps({"status": "running", "role": "horizon"}), "utf-8",
    )

    assert main(["--root", str(ws), "ps", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = {r["run"]: r for r in payload["processes"]}
    assert rows["0001"]["alive"] is True
    assert "0002" not in rows  # auto-reaped
    assert "0002" in payload.get("reaped", [])
    assert live.exists()
    assert not dead.exists()
    meta = json.loads((session / "meta.json").read_text("utf-8"))
    assert meta["status"] == "interrupted"
    assert meta.get("ended_at")


def test_ps_clean_reaps_dead_markers_only(tmp_path: Path, capsys) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "config.yaml").write_text(_CONFIG, "utf-8")
    live = _marker(ws, "0001", os.getpid())
    dead = _marker(ws, "0002", 2 ** 22 + 12345)

    assert main(["--root", str(ws), "ps", "--clean", "--json"]) == 0
    assert live.exists()
    assert not dead.exists()


def test_run_registers_and_clears_process_marker(tmp_path: Path) -> None:
    from archon_horizon.config.loader import build_orchestrator
    from archon_horizon.core.sessions import Focus, RunRecord
    from archon_horizon.core.tasks import HorizonTask, TaskStatus, WriteSet
    from archon_horizon.harnesses.base import HarnessResult
    from archon_horizon.harnesses.null import NullHarness

    ws = tmp_path / "ws"
    (ws / "projects" / "p").mkdir(parents=True)
    (ws / "config.yaml").write_text(
        "workspace:\n  name: w\n  horizon_agent: {harness: hor}\n"
        "harnesses:\n  hor: {kind: \"null\"}\nprojects:\n  p: {path: projects/p}\n",
        "utf-8",
    )
    seen: dict[str, bool] = {}

    def probe(req) -> HarnessResult:
        run_dir = ws / ".archon-horizon" / "runs" / "0001"
        seen["marker_during_run"] = (run_dir / "process.json").exists()
        return HarnessResult(ok=True, text="ok")

    orch = build_orchestrator(ws, harnesses={"hor": NullHarness(probe)})
    orch.task_store.put(HorizonTask(
        id="T-1", project="p", objective="x", title="x",
        projects=("p",), status=TaskStatus.QUEUED, write_set=WriteSet(projects=("p",)),
    ))
    orch.run(RunRecord(id="", focus=Focus(tasks=("T-1",)), rounds_requested=1))

    assert seen["marker_during_run"] is True
    assert not (ws / ".archon-horizon" / "runs" / "0001" / "process.json").exists()  # cleared on exit


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="Linux descendant identity test")
def test_kill_captures_child_in_separate_session_and_preserves_unrelated_process(tmp_path: Path) -> None:
    from archon_horizon.commands.ps import _pid_alive, _process_stat, _terminate_process_tree

    child_file = tmp_path / "child.pid"
    program = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", program, str(child_file)], start_new_session=True)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child_pid = int(child_file.read_text())
        count = _terminate_process_tree(parent.pid, expected_start_ticks=_process_stat(parent.pid)[1], grace_s=0.2)
        parent.wait(timeout=2)
        assert count == 2
        assert not _pid_alive(child_pid)
        assert unrelated.poll() is None
    finally:
        for process in (parent, unrelated):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        if child_pid is not None and _pid_alive(child_pid):
            os.kill(child_pid, 9)


def test_kill_refuses_reused_root_pid_before_any_signal(monkeypatch) -> None:
    from archon_horizon.commands import ps

    signals = []
    monkeypatch.setattr(ps, "_process_stat", lambda pid: (1, 200, "S"))
    monkeypatch.setattr(ps.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    with pytest.raises(ValueError, match="recorded process identity"):
        ps._terminate_process_tree(23456789, expected_start_ticks=100, grace_s=0)
    assert not signals
