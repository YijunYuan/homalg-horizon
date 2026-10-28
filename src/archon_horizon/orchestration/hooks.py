"""HomAlg boundary hooks and narrowly scoped ledger restoration.

Commands are trusted workspace configuration; arguments never pass through a shell.
Violation paths are untrusted hook output and must pass validation as a complete set
before any restoration occurs. The user's repository is never staged or committed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
from dataclasses import dataclass

from archon_horizon.core.inbox import InboxDraft, InboxKind
from archon_horizon.core.workspace import Workspace
from archon_horizon.inboxes.filesystem import FilesystemInboxProvider
from archon_horizon.vcs.git import WorkspaceGit


@dataclass(frozen=True)
class HookResult:
    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""


def run_hook(command: str, workspace: Workspace, *, values: dict[str, str], log_path: Path) -> HookResult:
    """Expand each argv token separately so spaces in identifiers remain data."""
    try:
        argv = [part.format(**values) for part in shlex.split(command)]
        if not argv:
            raise ValueError("empty hook command")
        env = {**os.environ, "ARCHON_HORIZON_TASK": values.get("task_id", ""),
               "ARCHON_HORIZON_SESSION_DIR": values.get("session_dir", "")}
        if workspace.session_ledger:
            env["HOMALG_BOUNDARY_LOCK_HELD"] = "1"
        completed = subprocess.run(argv, cwd=workspace.root, env=env, capture_output=True,
                                   text=True, timeout=1800, check=False)
        result = HookResult(command, completed.returncode, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        result = HookResult(command, 124, str(exc.stdout or ""), "Hook timed out after 1800 seconds")
    except (OSError, ValueError, KeyError, IndexError) as exc:
        result = HookResult(command, 125, stderr=str(exc))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"command": command, "returncode": result.returncode,
                                 "stdout": result.stdout, "stderr": result.stderr}) + "\n")
    return result


def hook_issue(workspace: Workspace, title: str, detail: str) -> None:
    FilesystemInboxProvider(workspace.state_path / "inbox" / "local").create_item(InboxDraft(
        kind=InboxKind.ISSUE, author="homalg", audience="human", body=f"{title}\n\n{detail}",
    ))


def violation_paths(result: HookResult, workspace: Workspace) -> tuple[str, ...]:
    """Reject traversal, absolute paths, repository internals and symlink escapes."""
    payload = json.loads(result.stdout)
    paths = payload.get("violations") if isinstance(payload, dict) else None
    if not isinstance(paths, list) or not paths:
        raise ValueError("scope hook exit 4 requires a nonempty violations array")
    root = workspace.root.resolve()
    validated = []
    for raw in paths:
        if not isinstance(raw, str) or not raw or any(c in raw for c in "\x00\n\r\t\\"):
            raise ValueError("invalid scope violation path")
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", "..", ".git"} for part in raw.split("/")):
            raise ValueError(f"unsafe scope violation path: {raw!r}")
        absolute = root / raw
        if absolute == workspace.state_path.resolve() or workspace.state_path.resolve() in absolute.parents:
            raise ValueError(f"refusing to restore engine state: {raw!r}")
        # A final symlink can be safely unlinked, but never follow one of its parents.
        if not absolute.parent.resolve().is_relative_to(root):
            raise ValueError(f"scope violation path escapes workspace: {raw!r}")
        if absolute.is_dir() and not absolute.is_symlink():
            raise ValueError(f"scope violations must identify files: {raw!r}")
        validated.append(raw)
    return tuple(dict.fromkeys(validated))


def restore_violations(workspace: Workspace, paths: tuple[str, ...], start_sha: str, task_id: str) -> str:
    """Restore only listed file contents/modes from the ledger's session start."""
    git = WorkspaceGit(workspace.root)
    if not start_sha or git._run(["cat-file", "-t", start_sha]) != "commit":
        raise ValueError("scope rollback requires the recorded start commit")
    restorations = []
    for path in paths:
        entry = git._run(["ls-tree", "-z", start_sha, "--", path])
        if not entry:
            restorations.append((path, None, None))
            continue
        info, stored = entry.rstrip("\x00").split("\t", 1)
        mode, kind, oid = info.split()
        if stored != path or kind != "blob" or mode not in {"100644", "100755", "120000"}:
            raise ValueError(f"unsupported scope rollback entry: {path!r}")
        content = subprocess.run(["git", f"--git-dir={git.git_dir}", "cat-file", "blob", oid],
                                 capture_output=True, check=True).stdout
        if mode == "120000":
            target = (workspace.root / path).parent / os.fsdecode(content)
            if not target.resolve().is_relative_to(workspace.root.resolve()):
                raise ValueError(f"refusing restored symlink outside workspace: {path!r}")
        restorations.append((path, mode, content))
    for path, mode, content in restorations:
        dest = workspace.root / path
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        if mode is None:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            dest.symlink_to(os.fsdecode(content))
        else:
            dest.write_bytes(content)
            dest.chmod(0o755 if mode == "100755" else 0o644)
    return git.commit(f"scope revert: {task_id}", paths=paths) or git.current_sha() or start_sha
