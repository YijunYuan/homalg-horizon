"""Land isolated session checkouts whose session ended before landing finished.

A run process that dies (killed, reaped, host restart) leaves its isolated
checkout under ``session-worktrees/`` and its task ``running``. The committed
work in that checkout is real, but nothing lands it: lane start only returns the
task to the queue. ``land_checkout`` performs exactly the boundary steps the
session missed, under the same locks and checks as a normal session end:

* the conflict-checked landing (skipped when it already happened), and
* the ``after_session`` hooks, which judge the result and may settle the task.

The task's terminal status is never inferred: a task left ``running`` returns to
``queued`` before the hooks run, as at the end of any session whose agent did not
record a status. A landed checkout is reclaimed only after its ``build/``
evidence is archived, like any landed session.
"""

from __future__ import annotations

import dataclasses
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from archon_horizon.core.clock import utc_now
from archon_horizon.core.sessions import RunRecord
from archon_horizon.core.tasks import HorizonTask, TaskStatus
from archon_horizon.runlog import SessionLog
from archon_horizon.vcs.git import GitError, WorkspaceGit
from archon_horizon.vcs.session import (
    SessionConflict,
    SessionLedger,
    boundary_lock,
    generated_path,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX only, like session_ledger itself
    fcntl = None

# Session states whose isolated checkout may still hold unlanded or unjudged work.
UNFINISHED = {"interrupted", "integration-conflict", "finalizing", "running", "started"}


@contextmanager
def no_live_run(workspace) -> Iterator[None]:
    """Hold the run-policy lock exclusively: no run may start or be live meanwhile."""
    if fcntl is None:
        raise SessionConflict("session recovery requires POSIX flock support")
    path = workspace.state_path / "locks/run-policy.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SessionConflict("a run is live; stop it before landing preserved sessions") from exc
        yield


def _checkouts(workspace) -> list[Path]:
    directory = workspace.state_path / "session-worktrees"
    if not directory.is_dir():
        return []
    return sorted(
        path for path in directory.iterdir()
        if path.is_dir() and (path / workspace.state_dir / "vcs/workspace.git").is_dir()
    )


def _landed(workspace, checkout_id: str) -> str | None:
    """The canonical integration commit of this checkout, if it already landed."""
    try:
        sha = WorkspaceGit(workspace.root)._run(
            ["log", "-1", "--format=%H", f"--grep=Archon-Session: {checkout_id}", "--fixed-strings", "HEAD"],
            check=False,
        )
    except GitError:
        return None
    return sha or None


def _sessions(orch) -> list[tuple[str, SessionLog, dict[str, Any]]]:
    rows = []
    for run_id in orch.run_logs.ids():
        for session in orch.run_logs.get(run_id).sessions():
            meta = session.read_meta()
            if meta.get("role") == "horizon" and meta.get("task_id") and meta.get("start_sha"):
                rows.append((run_id, session, meta))
    return rows


def _scope_problems(workspace, task: HorizonTask, changed: tuple[str, ...]) -> list[str]:
    allowed = set(task.write_set.files)
    return [
        path for path in changed
        if path not in allowed and not generated_path(workspace, path)
    ]


def _descends(path: Path, start_sha: str) -> bool:
    try:
        return WorkspaceGit(path)._run(["merge-base", start_sha, "HEAD"], check=False) == start_sha
    except GitError:
        return False


def preserved_sessions(orch) -> list[dict[str, Any]]:
    """Every preserved checkout with its owning session, when it can be identified.

    The owner is the session whose metadata names the checkout. Sessions that
    predate that record are matched by evidence instead: an unfinished session
    with the same start commit whose task scope covers every changed file. An
    ambiguous or missing match is reported, never guessed.
    """
    workspace = orch.workspace
    sessions = _sessions(orch)
    named = {meta["session_ledger"]: (run_id, s, meta) for run_id, s, meta in sessions
             if meta.get("session_ledger")}
    rows = []
    for path in _checkouts(workspace):
        checkout_id = path.name
        row: dict[str, Any] = {"checkout": checkout_id, "path": str(path),
                               "landed": _landed(workspace, checkout_id)}
        owners = [named[checkout_id]] if checkout_id in named else []
        changed: tuple[str, ...] = ()
        if not owners:
            for run_id, session, meta in sessions:
                if meta.get("session_ledger") or meta.get("status") not in UNFINISHED:
                    continue
                if not _descends(path, meta["start_sha"]):
                    continue
                try:
                    task = orch.task_store.get(meta["task_id"])
                except (KeyError, FileNotFoundError):
                    continue
                probe = SessionLedger.reopen(workspace, task, checkout_id, meta["start_sha"])
                changed = probe.changed_files()
                if changed and not _scope_problems(workspace, task, changed):
                    owners.append((run_id, session, meta))
        if len(owners) == 1:
            run_id, session, meta = owners[0]
            row.update(run=run_id, session=session.name, task=meta["task_id"],
                       start_sha=meta["start_sha"], session_status=meta.get("status"))
        else:
            row["owner"] = "ambiguous" if owners else "unknown"
            row["candidates"] = [f"{run_id}/{session.name}" for run_id, session, _ in owners]
        rows.append(row)
    return rows


def _resolve(orch, checkout_id: str, session_ref: str | None):
    rows = {row["checkout"]: row for row in preserved_sessions(orch)}
    row = rows.get(checkout_id)
    if row is None:
        raise SessionConflict(f"no preserved session checkout {checkout_id}")
    if session_ref:
        run_id, _, name = session_ref.partition("/")
        matches = [s for s in orch.run_logs.get(run_id).sessions()
                   if s.name == name or s.name.startswith(name + "-")]
        if len(matches) != 1:
            raise SessionConflict(f"no unique session {session_ref}")
        session = matches[0]
        if "task" in row and (row["run"], row["session"]) != (run_id, session.name):
            raise SessionConflict(
                f"checkout {checkout_id} belongs to {row['run']}/{row['session']}, not {session_ref}")
        meta = session.read_meta()
        if meta.get("session_ledger") not in (None, checkout_id):
            raise SessionConflict(f"session {session_ref} names checkout {meta['session_ledger']}")
        return row, run_id, session, meta
    if "task" not in row:
        raise SessionConflict(
            f"the owner of checkout {checkout_id} is {row['owner']}; name it with --session RUN/SESSION"
            + (f" (candidates: {', '.join(row['candidates'])})" if row.get("candidates") else ""))
    session = next(s for s in orch.run_logs.get(row["run"]).sessions() if s.name == row["session"])
    return row, row["run"], session, session.read_meta()


def land_checkout(orch, checkout_id: str, *, session_ref: str | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
    """Land one preserved checkout and run the boundary hooks its session missed."""
    workspace = orch.workspace
    if not workspace.session_ledger:
        raise SessionConflict("session recovery applies to workspace.session_ledger workspaces")
    with no_live_run(workspace):
        row, run_id, session, meta = _resolve(orch, checkout_id, session_ref)
        task = orch.task_store.get(meta["task_id"])
        start_sha = str(meta["start_sha"])
        ledger = SessionLedger.reopen(workspace, task, checkout_id, start_sha)
        landed = _landed(workspace, checkout_id)
        changed = ledger.changed_files()
        problems = [] if landed else _scope_problems(workspace, task, changed)
        result: dict[str, Any] = {
            "checkout": checkout_id, "run": run_id, "session": session.name, "task": task.id,
            "start_sha": start_sha, "already_landed": bool(landed),
            "changed": list(changed), "outside_scope": problems, "dry_run": dry_run,
        }
        if problems:
            raise SessionConflict(
                f"checkout {checkout_id} changed files outside task {task.id}: {', '.join(problems)}")
        hooks_pending = not landed or "boundary" not in meta
        result["hooks_pending"] = hooks_pending
        if dry_run:
            return result
        run = RunRecord(id=run_id)
        try:
            with boundary_lock(workspace):
                current = orch.task_store.get(task.id)
                if current.status is TaskStatus.RUNNING:
                    orch.task_store.append_history(task.id, {
                        "at": utc_now().isoformat(), "actor": "system", "field": "status",
                        "from": "running", "to": "queued",
                        "note": "interrupted session recovered; the agent recorded no terminal status",
                    })
                    orch.task_store.put(dataclasses.replace(
                        current, status=TaskStatus.QUEUED, updated_at=utc_now()))
                if landed:
                    end_sha = str(meta.get("end_sha") or landed)
                    files: tuple[str, ...] = ()
                else:
                    _, end_sha, files = ledger.prepare_landing()
                orch._write_session_meta(session, {
                    "start_sha": start_sha, "end_sha": end_sha, "session_ledger": checkout_id,
                    "recovered_at": utc_now().isoformat(),
                })
                orch._emit("workspace.session.recovered", run_id=run_id, task_id=task.id,
                           session=session.name, checkout=checkout_id, files=list(files),
                           already_landed=bool(landed))
                failed = False
                if hooks_pending:
                    failed = orch._boundary_hooks_inner(
                        "after_session", run, task=task, session=session,
                        start_sha=start_sha, end_sha=end_sha)
                final = orch.task_store.get(task.id)
                status = {"ended_at": utc_now().isoformat()}
                if not final.metadata.get("finalization_hold"):
                    status["status"] = final.status.value
                orch._write_session_meta(session, status)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            orch._write_session_meta(session, {"status": "integration-conflict",
                                               "recovery_path": str(ledger.path)})
            raise SessionConflict(f"checkout {checkout_id} was not landed: {exc}") from exc
        ledger.landed = True
        ledger.close()
        result.update(landed_files=list(files), end_sha=end_sha, hooks_failed=failed,
                      status=final.status.value, reclaimed=not ledger.path.exists())
        return result
