"""Lanes: several isolated sessions share one queue without overlapping writes."""
from __future__ import annotations

import dataclasses
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml

from archon_horizon.config.loader import build_orchestrator, build_workspace, load_config
from archon_horizon.core.sessions import Focus, RunRecord
from archon_horizon.core.tasks import HorizonTask, TaskStatus, WriteSet
from archon_horizon.harnesses.null import NullHarness
from archon_horizon.orchestration.lanes import (
    LaneCoordinator,
    exclusive_reason,
    requeue_stale_running,
)


def setup(root: Path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / ".gitignore").write_text(".archon-horizon/\n.lake/\nbuild/\nreferences\nliterature/.private/\n/hgraph/\n")
    (root / ".lake").mkdir()
    (root / "references").mkdir()
    (root / "references" / "page.tex").write_text("private original")
    (root / "config.yaml").write_text(yaml.safe_dump({
        "workspace": {"name": "lanes", "session_ledger": True,
                      "horizon_agent": {"harness": "hor"}, "hooks": {}},
        "harnesses": {"hor": {"kind": "null"}}, "projects": {"HomAlg": {"path": "."}},
    }))
    for name in ("a", "b", "c", "d", "shared"):
        (root / f"{name}.lean").write_text("original")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "initial"], cwd=root, check=True)
    return build_workspace(load_config(root), root)


def task(name: str, *files: str, priority: str = "normal") -> HorizonTask:
    return HorizonTask(id=name, project="HomAlg", projects=("HomAlg",), objective="edit",
                       priority=priority,
                       write_set=WriteSet(projects=("HomAlg",), files=files or (f"{name}.lean",)))


class Recorder:
    """Shared observation of which sessions overlap in time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: dict[str, Path] = {}
        self.overlaps: list[tuple[str, ...]] = []
        self.started: list[str] = []
        self.roots: dict[str, Path] = {}


class Writer:
    """A fake native session: edits its own files, then records ``done``."""

    def __init__(self, original, store, recorder: Recorder, *, hold: float = 0.3,
                 on_start=None) -> None:
        self.original, self.store, self.recorder = original, store, recorder
        self.hold, self.on_start = hold, on_start

    def run_task(self, context):
        task_id = context.task.id
        with self.recorder.lock:
            self.recorder.started.append(task_id)
            self.recorder.active[task_id] = context.workspace.root
            self.recorder.roots[task_id] = context.workspace.root
            if len(self.recorder.active) > 1:
                self.recorder.overlaps.append(tuple(sorted(self.recorder.active)))
        if self.on_start:
            self.on_start(task_id, context)
        for raw in context.task.write_set.files:
            target = context.workspace.root / raw
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"changed by {task_id}")
        time.sleep(self.hold)
        with self.recorder.lock:
            self.recorder.active.pop(task_id, None)
        current = self.store.get(task_id)
        self.store.append_history(task_id, {"actor": "horizon", "field": "status",
                                            "from": "running", "to": "done"})
        self.store.put(dataclasses.replace(current, status=TaskStatus.DONE))
        return self.original.run_task(context)


def run_lanes(root: Path, count: int, recorder: Recorder, *, rounds: int = 10,
              poll: float = 0.05, **writer) -> list:
    coordinator = LaneCoordinator(lanes=count, poll_seconds=poll)
    orchestrators = []
    for lane in range(count):
        orch = build_orchestrator(root, harnesses={"hor": NullHarness("")})
        orch.horizon = Writer(orch.horizon, orch.task_store, recorder, **writer)
        orch.lanes, orch.lane_id = coordinator, lane
        orchestrators.append(orch)
    errors = []

    def work(orch):
        try:
            orch.run(RunRecord(id="", focus=Focus(), rounds_requested=rounds))
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(orch,)) for orch in orchestrators]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    return orchestrators


def test_lanes_run_disjoint_tasks_concurrently_and_land_each_once(tmp_path):
    setup(tmp_path)
    store = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")}).task_store
    for name in ("a", "b", "c", "d"):
        store.put(task(name))
    recorder = Recorder()
    run_lanes(tmp_path, 3, recorder)
    assert sorted(recorder.started) == ["a", "b", "c", "d"]
    assert recorder.overlaps, "lanes never overlapped"
    assert max(len(group) for group in recorder.overlaps) <= 3
    for name in ("a", "b", "c", "d"):
        assert (tmp_path / f"{name}.lean").read_text() == f"changed by {name}"
        assert store.get(name).status is TaskStatus.DONE
        assert recorder.roots[name] != tmp_path  # isolated checkout


def test_conflicting_write_sets_never_overlap(tmp_path):
    setup(tmp_path)
    store = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")}).task_store
    store.put(task("a", "a.lean", "shared.lean"))
    store.put(task("b", "b.lean", "shared.lean"))
    store.put(task("c"))
    recorder = Recorder()
    run_lanes(tmp_path, 3, recorder)
    assert sorted(recorder.started) == ["a", "b", "c"]
    assert not any({"a", "b"} <= set(group) for group in recorder.overlaps)


def test_private_scope_runs_alone_in_the_canonical_workspace(tmp_path):
    setup(tmp_path)
    store = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")}).task_store
    private = task("p", "references/page.tex", priority="high")
    assert exclusive_reason(private)
    store.put(private)
    store.put(task("a"))
    store.put(task("b"))
    recorder = Recorder()
    run_lanes(tmp_path, 3, recorder)
    assert sorted(recorder.started) == ["a", "b", "p"]
    assert recorder.started[0] == "p"  # highest priority starts first, alone
    assert not any("p" in group for group in recorder.overlaps)
    assert recorder.roots["p"] == tmp_path
    assert (tmp_path / "references" / "page.tex").read_text() == "changed by p"
    assert store.get("p").status is TaskStatus.DONE


def test_lanes_drain_before_an_exclusive_task_and_hold_new_work_while_it_runs(tmp_path):
    setup(tmp_path)
    store = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")}).task_store
    store.put(task("a", priority="urgent"))
    store.put(dataclasses.replace(task("x"), metadata={"exclusive": "needs canonical replay"},
                                  priority="high"))
    store.put(task("b"))
    recorder = Recorder()
    run_lanes(tmp_path, 3, recorder)
    assert recorder.started.index("a") < recorder.started.index("x")
    assert recorder.started.index("x") < recorder.started.index("b")
    assert not any("x" in group for group in recorder.overlaps)


def test_idle_lane_waits_for_work_unlocked_by_an_active_session(tmp_path):
    setup(tmp_path)
    store = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")}).task_store
    store.put(task("a"))

    def unlock(task_id, context):
        if task_id == "a":  # e.g. the outer hook's planning creating newly ready units
            store.put(task("b"))

    recorder = Recorder()
    run_lanes(tmp_path, 2, recorder, on_start=unlock)
    assert recorder.started == ["a", "b"]
    assert store.get("b").status is TaskStatus.DONE


def test_session_that_cannot_start_is_requeued_and_not_offered_again(tmp_path, monkeypatch):
    from archon_horizon.vcs import session as session_module

    setup(tmp_path)
    store = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")}).task_store
    store.put(task("a"))
    store.put(task("b"))
    original = session_module.SessionLedger.start

    def refuse(self):
        if self.task.id == "a":
            raise session_module.SessionConflict("injected start failure")
        return original(self)

    monkeypatch.setattr(session_module.SessionLedger, "start", refuse)
    recorder = Recorder()
    run_lanes(tmp_path, 2, recorder)
    assert recorder.started == ["b"]
    assert store.get("a").status is TaskStatus.QUEUED
    assert store.get("b").status is TaskStatus.DONE


def test_fatal_stop_lets_running_sessions_finish_and_starts_nothing_new(tmp_path):
    setup(tmp_path)
    store = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")}).task_store
    for name in ("a", "b", "c", "d"):
        store.put(task(name))
    coordinator_box = {}

    def stop_after_first(task_id, context):
        if task_id == "a":
            coordinator_box["stop"]()

    recorder = Recorder()
    coordinator = LaneCoordinator(lanes=2, poll_seconds=0.05)
    coordinator_box["stop"] = lambda: coordinator.stop("usage_limit")
    orchestrators = []
    for lane in range(2):
        orch = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
        orch.horizon = Writer(orch.horizon, orch.task_store, recorder, on_start=stop_after_first)
        orch.lanes, orch.lane_id = coordinator, lane
        orchestrators.append(orch)
    threads = [threading.Thread(target=o.run, args=(RunRecord(id="", focus=Focus(),
                                                               rounds_requested=10),))
               for o in orchestrators]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(recorder.started) <= 2
    assert all(store.get(name).status is TaskStatus.DONE for name in recorder.started)
    assert all(store.get(name).status is TaskStatus.QUEUED
               for name in {"a", "b", "c", "d"} - set(recorder.started))


def test_orphaned_running_tasks_are_requeued_before_lanes_start(tmp_path):
    setup(tmp_path)
    orch = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
    orch.task_store.put(dataclasses.replace(task("a"), status=TaskStatus.RUNNING))
    orch.task_store.put(task("b"))
    assert requeue_stale_running(orch) == ["a"]
    assert orch.task_store.get("a").status is TaskStatus.QUEUED


def test_isolated_checkout_mounts_private_literature_and_keeps_views_local(tmp_path):
    from archon_horizon.vcs.session import SessionLedger

    ws = setup(tmp_path)
    (tmp_path / "literature" / ".private").mkdir(parents=True)
    (tmp_path / "literature" / ".private" / "t.yaml").write_text("pages: {}")
    (tmp_path / "hgraph").mkdir()
    (tmp_path / "hgraph" / "config.yaml").write_text("canonical")
    ledger = SessionLedger(ws, task("a"))
    ledger.acquire()
    try:
        active = ledger.start()
        assert (active.root / "literature/.private/t.yaml").read_text() == "pages: {}"
        assert not (active.root / "hgraph").is_symlink()
        (active.root / "hgraph" / "config.yaml").write_text("session view")
        assert (tmp_path / "hgraph" / "config.yaml").read_text() == "canonical"
        (active.root / "build").mkdir()
        (active.root / "build" / "receipt.json").write_text("{}")
        (active.root / "a.lean").write_text("new")
        from archon_horizon.vcs.session import boundary_lock
        with boundary_lock(ws):
            _, _, files = ledger.prepare_landing()
        assert files == ("a.lean",)
    finally:
        ledger.close()
    assert (ledger.evidence_path / "receipt.json").read_text() == "{}"
    assert not ledger.path.exists()


def test_relative_workspace_root_keeps_mounts_out_of_the_session_ledger(tmp_path, monkeypatch):
    from archon_horizon.vcs.session import SessionLedger, boundary_lock

    setup(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    ws = build_workspace(load_config(Path(".")), Path("."))
    assert not ws.root.is_absolute()
    ledger = SessionLedger(ws, task("a"))
    ledger.acquire()
    try:
        active = ledger.start()
        assert active.metadata["canonical_root"] == str(tmp_path.resolve())
        (active.root / "a.lean").write_text("new")
        with boundary_lock(ledger.main):
            _, _, files = ledger.prepare_landing()
        assert files == ("a.lean",)
    finally:
        ledger.close()
    assert (tmp_path / "a.lean").read_text() == "new"


def test_generated_files_never_land_from_concurrent_sessions(tmp_path):
    from archon_horizon.vcs.session import SessionLedger, boundary_lock

    setup(tmp_path)
    (tmp_path / "roots").mkdir()
    (tmp_path / "roots" / "nodes.json").write_text("canonical")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                    "commit", "-qm", "roots"], cwd=tmp_path, check=True)
    config = yaml.safe_load((tmp_path / "config.yaml").read_text())
    config["workspace"]["session_generated"] = ["roots/*.json"]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    ws = build_workspace(load_config(tmp_path), tmp_path)
    assert ws.session_generated == ("roots/*.json",)
    first, second = SessionLedger(ws, task("a")), SessionLedger(ws, task("b"))
    try:
        first.acquire(); second.acquire()
        one, two = first.start(), second.start()
        for active, name in ((one, "a"), (two, "b")):
            (active.root / f"{name}.lean").write_text(f"from {name}")
            (active.root / "roots" / "nodes.json").write_text(f"view of {name}")
        with boundary_lock(ws):
            assert first.prepare_landing()[2] == ("a.lean",)
        (tmp_path / "roots" / "nodes.json").write_text("regenerated by the hook")
        with boundary_lock(ws):
            assert second.prepare_landing()[2] == ("b.lean",)
        assert second.generated == ("roots/nodes.json",)
    finally:
        first.close(); second.close()
    assert (tmp_path / "roots" / "nodes.json").read_text() == "regenerated by the hook"
    assert (tmp_path / "b.lean").read_text() == "from b"


def test_session_generated_config_rejects_escaping_patterns(tmp_path):
    from archon_horizon.config.schema import ConfigError

    setup(tmp_path)
    config = yaml.safe_load((tmp_path / "config.yaml").read_text())
    config["workspace"]["session_generated"] = ["../outside.json"]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ConfigError, match="session_generated"):
        load_config(tmp_path)


def test_hgraph_parse_pool_survives_a_lock_held_by_another_thread(tmp_path):
    """A forked pool child inherits held locks; lanes need a spawned pool."""
    from archon_horizon.hgraph import sync as hgraph_sync

    lean = tmp_path / "Lean"
    lean.mkdir()
    for number in range(hgraph_sync._PARALLEL_THRESHOLD + 2):
        (lean / f"M{number}.lean").write_text(f"theorem t{number} : True := trivial\n")
    held = threading.Lock()
    held.acquire()  # simulates another lane holding a lock across the fork
    result: dict = {}

    def parse():
        files = hgraph_sync._iter_lean_files((lean,))
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(mp_context=multiprocessing.get_context("spawn")) as ex:
            result["parsed"] = list(ex.map(hgraph_sync._read_and_parse_lean,
                                           ((f, tmp_path) for f in files)))

    worker = threading.Thread(target=parse)
    worker.start()
    worker.join(timeout=60)
    held.release()
    assert not worker.is_alive()
    assert len(result["parsed"]) == hgraph_sync._PARALLEL_THRESHOLD + 2
    source = Path(hgraph_sync.__file__).read_text()
    assert 'get_context("spawn")' in source
