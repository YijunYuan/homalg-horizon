"""Concurrent lanes: several runs in one process pulling from one task queue.

Each lane is an ordinary sequential run with its own orchestrator, harness
instances and run log. The coordinator only decides *which* queued task a lane
may start, atomically and under the workspace boundary lock:

* a task whose write set conflicts with any running task is skipped;
* a task that cannot run in an isolated session checkout (private literature,
  shared authored files, workspace-wide or unscoped writes) or that its owner
  marked ``metadata.exclusive`` runs alone: it starts only when no other lane is
  active, and while it runs no lane starts anything;
* the highest-priority runnable task wins; when that task is exclusive and other
  lanes are busy, lanes wait rather than start lower-priority work around it;
* a lane with nothing to start waits while another lane is active, because the
  active session's outer hook may unlock new work; once no lane is active and
  nothing is runnable, every lane stops.

Claiming marks the task ``running`` before any agent starts, so two lanes (or a
second process) never select the same task. Isolated session leases remain the
final guard against overlapping writes.
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass, field

from archon_horizon.core.clock import utc_now
from archon_horizon.core.freeze import frozen_violations
from archon_horizon.core.tasks import HorizonTask, TaskStatus
from archon_horizon.vcs.session import boundary_lock, isolation_refusal

from .scheduler import _priority_key, write_sets_conflict

#: How long an idle lane sleeps between queue checks when no event wakes it.
IDLE_POLL_SECONDS = 30.0


def exclusive_reason(task: HorizonTask) -> str | None:
    """Why ``task`` must run alone, or ``None`` when it can share the workspace."""
    marked = task.metadata.get("exclusive")
    if marked:
        return str(marked) if isinstance(marked, str) else "marked exclusive"
    return isolation_refusal(task)


@dataclass
class _Lane:
    task_id: str
    exclusive: bool


@dataclass
class LaneCoordinator:
    """Shared by the lanes of one ``horizon run --lanes N`` invocation."""

    lanes: int
    poll_seconds: float = IDLE_POLL_SECONDS
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _active: dict[int, _Lane] = field(default_factory=dict, repr=False)
    _stopped: str | None = field(default=None, repr=False)
    # Tasks whose claimed session could not start; not offered again by this pool,
    # so a persistent refusal cannot spin a lane.
    _refused: set[str] = field(default_factory=set, repr=False)

    # ── control ──────────────────────────────────────────────────────

    def stop(self, reason: str) -> None:
        """No lane starts another session; running sessions finish normally."""
        with self._cond:
            if self._stopped is None:
                self._stopped = reason
            self._cond.notify_all()

    @property
    def stopped(self) -> str | None:
        return self._stopped

    def active(self) -> dict[int, str]:
        with self._cond:
            return {lane: state.task_id for lane, state in self._active.items()}

    # ── claiming ─────────────────────────────────────────────────────

    def _candidates(self, orch, run, tasks: list[HorizonTask]) -> list[HorizonTask]:
        in_focus = getattr(orch.scheduler, "_in_focus", None)
        ordered = []
        for task in sorted(tasks, key=_priority_key):
            if task.status is not TaskStatus.QUEUED or task.id in self._refused:
                continue
            if task.metadata.get("finalization_hold") or task.metadata.get("dispatch_hold"):
                continue
            if in_focus is not None and not in_focus(task, run.focus):
                continue
            if frozen_violations(task.write_set, orch.freeze):
                continue
            ordered.append(task)
        return ordered

    def _select(self, orch, run) -> tuple[HorizonTask | None, bool]:
        """Pick under the boundary lock; returns (task, exclusive)."""
        tasks = orch.task_store.list()
        if any(state.exclusive for state in self._active.values()):
            return None, False
        # An agent records ``done`` before its session lands, so a lane's task keeps
        # its write set until the lane is released, whatever its stored status.
        mine = {state.task_id for state in self._active.values()}
        running = [task for task in tasks
                   if task.status is TaskStatus.RUNNING or task.id in mine]
        for task in self._candidates(orch, run, tasks):
            if any(write_sets_conflict(task.write_set, other.write_set) for other in running):
                continue
            exclusive = exclusive_reason(task) is not None
            if exclusive and self._active:
                # Drain: start nothing around the highest-priority exclusive task.
                return None, False
            return task, exclusive
        return None, False

    def claim(self, orch, run, lane: int) -> HorizonTask | None:
        """Block until this lane may start a task; ``None`` means the pool is done."""
        with self._cond:
            while True:
                if self._stopped is not None:
                    return None
                with boundary_lock(orch.workspace):
                    task, exclusive = self._select(orch, run)
                    if task is not None:
                        orch.task_store.append_history(task.id, {
                            "at": utc_now().isoformat(),
                            "actor": "system",
                            "field": "status",
                            "from": task.status.value,
                            "to": TaskStatus.RUNNING.value,
                            "note": f"claimed by lane {lane} of run {run.id}",
                        })
                        orch.task_store.put(dataclasses.replace(
                            task, status=TaskStatus.RUNNING, updated_at=utc_now(),
                            metadata={**task.metadata,
                                      "lane": {"run_id": run.id, "lane": lane,
                                               "exclusive": exclusive}},
                        ))
                        self._active[lane] = _Lane(task.id, exclusive)
                        return orch.task_store.get(task.id)
                if not self._active:
                    return None
                self._cond.wait(timeout=self.poll_seconds)

    def release(self, orch, lane: int, *, started: bool) -> None:
        """Free the lane; a task that never started returns to the queue."""
        with self._cond:
            state = self._active.pop(lane, None)
            if state is not None and not started:
                self._refused.add(state.task_id)
                with boundary_lock(orch.workspace):
                    current = orch.task_store.get(state.task_id)
                    if current.status is TaskStatus.RUNNING:
                        orch.task_store.append_history(state.task_id, {
                            "at": utc_now().isoformat(),
                            "actor": "system",
                            "field": "status",
                            "from": "running",
                            "to": "queued",
                            "note": f"lane {lane} could not start the claimed session",
                        })
                        orch.task_store.put(dataclasses.replace(
                            current, status=TaskStatus.QUEUED, updated_at=utc_now()))
            self._cond.notify_all()


def requeue_stale_running(orch, *, live_task_ids: set[str] = frozenset()) -> list[str]:
    """Return orphaned ``running`` tasks to the queue before a lane pool starts.

    Only safe when no run process is alive: a crashed or killed session leaves its
    task ``running`` and the pool would otherwise never offer it again. An isolated
    session's unlanded checkout stays under ``session-worktrees/`` for recovery.
    """
    requeued = []
    with boundary_lock(orch.workspace):
        for task in orch.task_store.list():
            if task.status is not TaskStatus.RUNNING or task.id in live_task_ids:
                continue
            orch.task_store.append_history(task.id, {
                "at": utc_now().isoformat(),
                "actor": "system",
                "field": "status",
                "from": "running",
                "to": "queued",
                "note": "re-queued at lane start: no live run owns this session",
            })
            orch.task_store.put(dataclasses.replace(
                task, status=TaskStatus.QUEUED, updated_at=utc_now()))
            requeued.append(task.id)
    return requeued
