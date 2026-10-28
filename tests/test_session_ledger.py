"""Concurrent runs never share live source trees or audit another session's edits."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import shlex
import subprocess
import sys
import threading

import pytest
import yaml

from archon_horizon.config.loader import build_orchestrator, build_workspace, load_config
from archon_horizon.core.sessions import Focus, RunRecord
from archon_horizon.core.tasks import HorizonTask, TaskStatus, WriteSet
from archon_horizon.harnesses.null import NullHarness
from archon_horizon.vcs.git import WorkspaceGit
from archon_horizon.vcs.session import SessionConflict, SessionLedger, boundary_lock


def setup(root: Path, hooks=None):
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / ".gitignore").write_text(".archon-horizon/\n.lake/\n")
    (root / ".lake").mkdir()
    (root / "config.yaml").write_text(yaml.safe_dump({
        "workspace": {"name": "parallel", "session_ledger": True,
                      "horizon_agent": {"harness": "hor"}, "hooks": hooks or {}},
        "harnesses": {"hor": {"kind": "null"}}, "projects": {"HomAlg": {"path": "."}},
    }))
    for name in ("a.lean", "b.lean", "forbidden.lean"):
        (root / name).write_text("original")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"], cwd=root, check=True)
    return build_workspace(load_config(root), root)


def task(name):
    return HorizonTask(id=name, project="HomAlg", projects=("HomAlg",), objective="edit scoped file",
                       write_set=WriteSet(projects=("HomAlg",), files=(f"{name}.lean",)))


def test_isolated_intervals_land_disjoint_sources_without_other_session_changes(tmp_path):
    ws = setup(tmp_path)
    first, second = SessionLedger(ws, task("a")), SessionLedger(ws, task("b"))
    user_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path)
    try:
        first.acquire(); second.acquire()
        one, two = first.start(), second.start()
        assert one.root != two.root != tmp_path
        assert (one.root / ".lake").resolve() == (two.root / ".lake").resolve()
        assert (one.state_path / "cache").resolve() == (two.state_path / "cache").resolve()
        (one.root / "a.lean").write_text("from a")
        (two.root / "b.lean").write_text("from b")
        assert (tmp_path / "a.lean").read_text() == "original"
        with boundary_lock(ws):
            start_b, end_b, files_b = second.prepare_landing()
        with boundary_lock(ws):
            start_a, end_a, files_a = first.prepare_landing()
        assert files_a == ("a.lean",) and files_b == ("b.lean",)
        git = WorkspaceGit(tmp_path)
        assert git._run(["diff", "--name-only", start_a, end_a]) == "a.lean"
        assert git._run(["diff", "--name-only", start_b, end_b]) == "b.lean"
        assert (tmp_path / "a.lean").read_text() == "from a"
        assert (tmp_path / "b.lean").read_text() == "from b"
        assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path) == user_head
    finally:
        first.close(); second.close()


def test_conflicting_parent_edit_preserves_both_parent_and_session_recovery(tmp_path):
    ws = setup(tmp_path)
    session = SessionLedger(ws, task("a"))
    session.acquire()
    try:
        active = session.start()
        (active.root / "a.lean").write_text("session change")
        (tmp_path / "a.lean").write_text("human change")
        with boundary_lock(ws), pytest.raises(SessionConflict, match="parent changed"):
            session.prepare_landing()
        assert (tmp_path / "a.lean").read_text() == "human change"
    finally:
        session.close()
    assert (session.path / "a.lean").read_text() == "session change"


def test_overlapping_scopes_and_private_transcription_refuse_safely(tmp_path):
    ws = setup(tmp_path)
    first, second = SessionLedger(ws, task("a")), SessionLedger(ws, dataclasses.replace(task("a"), id="other"))
    first.acquire()
    try:
        with pytest.raises(SessionConflict, match="another session"):
            second.acquire()
    finally:
        first.close(); second.close()
    private = dataclasses.replace(task("a"), write_set=WriteSet(files=("references/Book1945/tex/page-0001.tex",)))
    with pytest.raises(SessionConflict, match="unsupported"):
        SessionLedger(ws, private).acquire()


@pytest.mark.parametrize("mode", ["threads", "processes"])
def test_overlapping_runs_scope_rollback_preserves_other_run_and_generated_output(tmp_path, mode):
    script = tmp_path / "hook.py"
    script.write_text('''import json, pathlib, subprocess, sys
root = pathlib.Path.cwd()
task, start, end = sys.argv[1:]
git = root / ".archon-horizon/vcs/workspace.git"
paths = subprocess.check_output(["git", f"--git-dir={git}", "diff", "--name-only", start, end], text=True).splitlines()
violations = [p for p in paths if p != task + ".lean"]
# A retry after restoration sees the corrected main ledger interval.
violations = [p for p in violations if (root/p).exists() and (root/p).read_text() != "original"]
if violations:
    print(json.dumps({"violations": violations}))
    raise SystemExit(4)
(root / "generated.txt").write_text((root/"a.lean").read_text()+"|"+(root/"b.lean").read_text())
''')
    ws = setup(tmp_path, {"after_session": [f"{shlex.quote(sys.executable)} hook.py {{task_id}} {{start_sha}} {{end_sha}}"]})
    # hook.py was written before setup, so the committed baseline includes it.
    one = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
    two = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
    one.task_store.put(task("a")); two.task_store.put(task("b"))
    import multiprocessing
    factory = multiprocessing.get_context("fork") if mode == "processes" else threading
    barrier = factory.Barrier(2)
    completed_b = factory.Event()
    errors = multiprocessing.get_context("fork").Queue()

    class Writer:
        def __init__(self, original, name, store):
            self.original, self.name, self.store = original, name, store
        def run_task(self, context):
            (context.workspace.root / f"{self.name}.lean").write_text(f"changed {self.name}")
            if self.name == "a":
                (context.workspace.root / "forbidden.lean").write_text("illegal")
            barrier.wait(timeout=10)
            if self.name == "a":
                assert completed_b.wait(timeout=15)
            current = self.store.get(self.name)
            self.store.append_history(self.name, {"actor": "horizon", "field": "status", "from": "running", "to": "done"})
            self.store.put(dataclasses.replace(current, status=TaskStatus.DONE))
            return self.original.run_task(context)

    one.horizon = Writer(one.horizon, "a", one.task_store)
    two.horizon = Writer(two.horizon, "b", two.task_store)
    def run(orch, name):
        try:
            orch.run(RunRecord(id="", focus=Focus(tasks=(name,)), rounds_requested=1))
        except BaseException as exc:
            errors.put(repr(exc))
        finally:
            if name == "b": completed_b.set()
    worker_type = factory.Process if mode == "processes" else factory.Thread
    workers = [worker_type(target=run, args=(one, "a")), worker_type(target=run, args=(two, "b"))]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=30)
    assert not any(worker.is_alive() for worker in workers)
    assert errors.empty()
    assert (tmp_path / "a.lean").read_text() == "changed a"
    assert (tmp_path / "b.lean").read_text() == "changed b"
    assert (tmp_path / "forbidden.lean").read_text() == "original"
    assert (tmp_path / "generated.txt").read_text() == "changed a|changed b"
    assert one.task_store.get("a").status is TaskStatus.QUEUED
    assert two.task_store.get("b").status is TaskStatus.DONE
    metas = [json.loads(p.read_text()) for p in (ws.state_path / "runs").glob("*/sessions/*horizon*/meta.json")]
    assert len(metas) == 2
    a = next(m for m in metas if m["task_id"] == "a")
    assert a["status"] == "scope-violation"
    assert WorkspaceGit(tmp_path)._run(["diff", "--name-only", a["start_sha"], a["end_sha"]]).splitlines() == ["a.lean", "forbidden.lean"]


def test_failed_landing_restores_parent_sources_and_preserves_recovery(tmp_path, monkeypatch):
    ws = setup(tmp_path)
    session = SessionLedger(ws, task("a"))
    session.acquire()
    try:
        active = session.start()
        (active.root / "a.lean").write_text("new source")
        original = WorkspaceGit.commit
        def fail_parent(self, message, **kwargs):
            if self.root == tmp_path and message.startswith("workspace: integrate isolated"):
                raise RuntimeError("injected commit failure")
            return original(self, message, **kwargs)
        monkeypatch.setattr(WorkspaceGit, "commit", fail_parent)
        with boundary_lock(ws), pytest.raises(RuntimeError, match="injected"):
            session.prepare_landing()
        assert (tmp_path / "a.lean").read_text() == "original"
    finally:
        session.close()
    assert (session.path / "a.lean").read_text() == "new source"


def test_serial_run_lock_excludes_another_run(tmp_path):
    from archon_horizon.vcs.session import run_policy_lock
    ws = dataclasses.replace(setup(tmp_path), session_ledger=False)
    with run_policy_lock(ws), pytest.raises(SessionConflict, match="another run"):
        with run_policy_lock(ws):
            pytest.fail("serial runs overlapped")
