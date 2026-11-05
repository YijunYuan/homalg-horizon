"""Opt-in isolated session sources and serialized landing for concurrent runs.

The shared workspace is never the agent's live source worktree in this mode.
Each session has its own ledger and an immutable source snapshot. Audit refs retain
that isolated interval; optimistic landing refuses a concurrent human/source edit.
Control-plane state and the build cache stay shared. Private-library tasks are not
supported in this mode because those files are outside the source ledger.
"""
from __future__ import annotations

from contextlib import contextmanager
import dataclasses
try:
    import fcntl
except ImportError:  # Isolated concurrent mode requires POSIX advisory locks.
    fcntl = None
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import threading
import uuid

from archon_horizon.core.tasks import HorizonTask
from archon_horizon.core.workspace import Workspace
from .git import WorkspaceGit

_locks: dict[str, threading.RLock] = {}
_local = threading.local()


@contextmanager
def boundary_lock(workspace: Workspace):
    """Reentrant within a thread; exclusive across processes and threads."""
    if not workspace.session_ledger:
        yield
        return
    if fcntl is None:
        raise SessionConflict("session_ledger requires POSIX flock support")
    path = workspace.state_path / "locks/session-boundary.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, "held", {})
        if key in held:
            yield
            return
        with path.open("a+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            _local.held = {**held, key: stream}
            try:
                yield
            finally:
                _local.held = held


class SessionConflict(RuntimeError):
    """The session cannot safely acquire or land its source scope."""


@contextmanager
def run_policy_lock(workspace: Workspace):
    """Serial runs exclude every other run; isolated runs may share the workspace."""
    if fcntl is None:
        if workspace.session_ledger:
            raise SessionConflict("session_ledger requires POSIX flock support")
        yield
        return
    path = workspace.state_path / "locks/run-policy.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            mode = fcntl.LOCK_SH if workspace.session_ledger else fcntl.LOCK_EX
            fcntl.flock(stream.fileno(), mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SessionConflict("another run holds this workspace; concurrent runs require session_ledger: true") from exc
        yield


def corrected_interval(workspace: Workspace, start_sha: str, end_sha: str, paths: tuple[str, ...]) -> str:
    """Record rollback in the isolated audit tree without importing other sessions."""
    git = WorkspaceGit(workspace.root)
    with tempfile.TemporaryDirectory(dir=workspace.state_path / "vcs") as directory:
        index = Path(directory) / "index"
        git._run(["read-tree", end_sha], index_file=index)
        git._run(["restore", f"--source={start_sha}", "--staged", "--", *paths], index_file=index)
        tree = git._run(["write-tree"], index_file=index)
        commit = git._run(["commit-tree", tree, "-p", end_sha, "-m", "session audit: scope restoration"])
        git._run(["update-ref", f"refs/homalg-session-corrections/{uuid.uuid4().hex}", commit])
        return commit


def _source_path(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise SessionConflict("session source paths must be nonempty strings")
    path = PurePosixPath(raw)
    if (path.is_absolute() or any(p in {"", ".", "..", ".git"} for p in raw.split("/"))
            or any(c in raw for c in "\x00\n\r\\*?[]")):
        raise SessionConflict(f"session scope requires explicit safe file paths: {raw!r}")
    target = root / raw
    if not target.parent.resolve().is_relative_to(root.resolve()):
        raise SessionConflict(f"session source path escapes workspace: {raw!r}")
    return target


#: Source prefixes an isolated session can neither lease nor land.
UNSHARED_PREFIXES = ("references/", "literature/.private/", ".archon-horizon/", "taxonomy/")
#: Evidence larger than this stays in the reclaimed checkout instead of the archive.
EVIDENCE_LIMIT = 32 * 1024 * 1024


def _segment_match(pattern: str, path: str) -> bool:
    """Glob per path segment, so ``*`` never crosses a directory boundary."""
    import fnmatch

    left, right = pattern.split("/"), path.split("/")
    return len(left) == len(right) and all(
        fnmatch.fnmatchcase(part, glob) for glob, part in zip(left, right)
    )


def generated_path(workspace: Workspace, raw: str) -> bool:
    """True for a tracked file the boundary hooks regenerate after every landing."""
    return any(_segment_match(pattern, raw) for pattern in workspace.session_generated)


def isolation_refusal(task: HorizonTask, root: Path | None = None) -> str | None:
    """Why ``task`` cannot run in an isolated session checkout, without locking anything."""
    paths = task.write_set.files
    if task.write_set.workspace:
        return "workspace-wide write set"
    if not paths:
        return "no explicit file scope"
    for raw in paths:
        if raw.startswith(UNSHARED_PREFIXES):
            return f"shared or private scope: {raw}"
        if root is not None:
            try:
                target = _source_path(root, raw)
            except SessionConflict as exc:
                return str(exc)
            if target.is_dir() or target.is_symlink():
                return f"directory or symlink scope: {raw}"
    return None


class SessionLedger:
    def __init__(self, workspace: Workspace, task: HorizonTask):
        # Absolute paths throughout: Git resolves a relative core.excludesFile against
        # its own cwd, so a workspace opened as "." would leak the mounts into commits.
        workspace = dataclasses.replace(workspace, root=workspace.root.resolve())
        self.main = workspace
        self.task = task
        self.id = uuid.uuid4().hex
        self.path = workspace.state_path / "session-worktrees" / self.id
        self.streams = []
        self.start_sha = ""
        self.end_sha = ""
        self.workspace = workspace
        self.landed = False
        self.generated: tuple[str, ...] = ()

    def acquire(self) -> None:
        if fcntl is None:
            raise SessionConflict("session_ledger requires POSIX flock support")
        paths = self.task.write_set.files
        if not paths or self.task.write_set.workspace:
            raise SessionConflict("session_ledger requires a nonempty explicit file scope")
        keys = {"task:" + self.task.id: fcntl.LOCK_EX}
        for raw in paths:
            target = _source_path(self.main.root, raw)
            if raw.startswith(UNSHARED_PREFIXES) or target.is_dir() or target.is_symlink():
                raise SessionConflict(f"shared or external authored scope is unsupported in session_ledger: {raw}")
            for parent in PurePosixPath(raw).parents:
                if parent.as_posix() != ".":
                    keys.setdefault("file:" + parent.as_posix(), fcntl.LOCK_SH)
            keys["file:" + raw] = fcntl.LOCK_EX
        directory = self.main.state_path / "locks/session-scopes"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            for key in sorted(keys):
                stream = (directory / hashlib.sha256(key.encode()).hexdigest()).open("a+")
                self.streams.append(stream)
                try:
                    fcntl.flock(stream.fileno(), keys[key] | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise SessionConflict(f"another session holds {key}") from exc
        except Exception:
            self.close()
            raise

    def start(self) -> Workspace:
        with boundary_lock(self.main):
            main_git = WorkspaceGit(self.main.root)
            main_git.init()
            main_git.commit("workspace: session source snapshot", paths=["."])
            self.start_sha = main_git.current_sha() or ""
            if not self.start_sha:
                raise SessionConflict("session_ledger requires a nonempty source snapshot")
            result = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=self.main.root,
                                    capture_output=True, text=True)
            if result.returncode:
                raise SessionConflict("session_ledger requires a committed user repository HEAD")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--shared", "--no-checkout", str(self.main.root), str(self.path)],
                           check=True, capture_output=True)
            state = self.path / self.main.state_dir
            state.mkdir(parents=True, exist_ok=True)
            own_git = state / "vcs/workspace.git"
            own_git.parent.mkdir(parents=True)
            subprocess.run(["git", "clone", "--shared", "--bare", str(main_git.git_dir), str(own_git)],
                           check=True, capture_output=True)
            git = WorkspaceGit(self.path)
            git._run(["read-tree", "--reset", "-u", self.start_sha])
            # Snapshot ignored runtime configuration; no source writes reach the parent.
            for name in ("config.yaml", "AGENTS.md", "CLAUDE.md", ".mcp.json", ".env"):
                source = self.main.root / name
                if source.is_file():
                    shutil.copy2(source, self.path / name)
            # Share scheduler/inbox/log stores and the one build lock, not the source ledger.
            for name in ("tasks", "roadmap", "runs", "inbox", "overlay", "blueprints", "cache", "locks", "subagents", "tools", "tmp", "events"):
                source = self.main.state_path / name
                source.mkdir(parents=True, exist_ok=True)
                destination = state / name
                if not destination.exists():
                    destination.symlink_to(source.resolve(), target_is_directory=True)
            # Read-only shared inputs: the build cache (serialized by the shared check
            # lock), agent descriptors and the private literature checkout. Private
            # files never land from an isolated session; tasks that write them run
            # alone in the canonical workspace instead.
            for name in (".lake", ".claude", ".codex", "references", "literature/.private"):
                source = self.main.root / name
                destination = self.path / name
                if (source.exists() or source.is_symlink()) and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.symlink_to(source.resolve(), target_is_directory=source.is_dir())
            # Generated views are session-local copies: regenerating them here must
            # never overwrite the canonical projection, which boundary hooks own.
            for name in ("hgraph", "blueprint"):
                source = self.main.root / name
                destination = self.path / name
                if source.is_dir() and not source.is_symlink() and not destination.exists():
                    shutil.copytree(source, destination, symlinks=True)
            # Directory-only ignore patterns do not match our symlink mounts.
            # Keep these mounts and generated views out of every agent hgit commit.
            excludes = state / "vcs/session-excludes"
            excludes.write_text("\n".join((
                "/.lake", "/.claude", "/.codex", "/references", "/literature/.private",
                "/hgraph", "/blueprint",
            )) + "\n")
            git._run(["config", "core.excludesFile", str(excludes.resolve())])
            self.workspace = dataclasses.replace(self.main, root=self.path,
                                                 metadata={**self.main.metadata, "canonical_root": str(self.main.root.resolve())})
            return self.workspace

    def prepare_landing(self) -> tuple[str, str, tuple[str, ...]]:
        """Capture an isolated interval, check parent conflicts, then land exact files.

        Caller holds boundary_lock through hooks and final ledger commit. A failed
        session is preserved in session-worktrees for recovery, never discarded.
        """
        git = WorkspaceGit(self.path)
        git.init()
        git.commit(f"session {self.task.id}: source snapshot", paths=["."])
        self.end_sha = git.current_sha() or self.start_sha
        changed = tuple(filter(None, git._run(["diff", "--name-only", "--no-renames", self.start_sha, self.end_sha]).splitlines()))
        # Generated files reflect this session's tree only; every concurrent session
        # rewrites them. They stay in the audit interval but never land: the
        # after-session hook regenerates them from the combined canonical sources.
        self.generated = tuple(raw for raw in changed if generated_path(self.main, raw))
        paths = tuple(raw for raw in changed if raw not in self.generated)
        main_git = WorkspaceGit(self.main.root)
        audit_ref = f"refs/homalg-sessions/{self.id}"
        main_git._run(["fetch", "--no-tags", str(git.git_dir), f"HEAD:{audit_ref}"])
        # Compare actual main worktree against the immutable start tree before any mutation.
        for raw in paths:
            parent = _source_path(self.main.root, raw)
            staged = _source_path(self.path, raw)
            if raw.startswith(("references/", "literature/.private/", ".archon-horizon/")):
                raise SessionConflict(f"external/control-plane source change cannot land: {raw}")
            if staged.is_symlink() and not staged.resolve().is_relative_to(self.path.resolve()):
                raise SessionConflict(f"external symlink cannot land: {raw}")
            before = git._run(["ls-tree", self.start_sha, "--", raw])
            if before:
                # Git hashes both file content and mode; use a separate scratch blob query.
                info = before.split("\t", 1)[0].split()
                expected_mode, _, expected_oid = info
                if not parent.exists() and not parent.is_symlink():
                    raise SessionConflict(f"parent changed while session ran: {raw}")
                if parent.is_symlink():
                    data = os.fsencode(os.readlink(parent)); mode = "120000"
                else:
                    data = parent.read_bytes(); mode = "100755" if parent.stat().st_mode & 0o111 else "100644"
                actual_oid = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
                if (mode, actual_oid) != (expected_mode, expected_oid):
                    raise SessionConflict(f"parent changed while session ran: {raw}")
            elif parent.exists() or parent.is_symlink():
                raise SessionConflict(f"parent independently created session path: {raw}")
        # Keep byte/mode backups until the ledger commit succeeds; a copy/commit
        # failure must not leave a half-landed source tree behind.
        with tempfile.TemporaryDirectory(dir=self.main.state_path / "vcs") as directory:
            backup = Path(directory)
            saved = {}
            for number, raw in enumerate(paths):
                parent = self.main.root / raw
                target = backup / str(number)
                if parent.is_symlink():
                    target.symlink_to(os.readlink(parent))
                    saved[raw] = target
                elif parent.exists():
                    shutil.copy2(parent, target)
                    saved[raw] = target
                else:
                    saved[raw] = None
            try:
                for raw in paths:
                    parent, staged = self.main.root / raw, self.path / raw
                    if parent.is_symlink() or parent.exists():
                        parent.unlink()
                    if staged.is_symlink():
                        parent.parent.mkdir(parents=True, exist_ok=True)
                        parent.symlink_to(os.readlink(staged))
                    elif staged.exists():
                        parent.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(staged, parent)
                if paths:
                    main_git.commit(f"workspace: integrate isolated session {self.task.id}", paths=paths,
                                    trailers={"Archon-Task": self.task.id, "Archon-Session": self.id, "Archon-Commit": "integration"})
            except BaseException:
                for raw, saved_path in saved.items():
                    parent = self.main.root / raw
                    if parent.is_symlink() or parent.exists():
                        parent.unlink()
                    if saved_path is not None:
                        parent.parent.mkdir(parents=True, exist_ok=True)
                        if saved_path.is_symlink():
                            parent.symlink_to(os.readlink(saved_path))
                        else:
                            shutil.copy2(saved_path, parent)
                raise
        self.landed = True
        return self.start_sha, self.end_sha, paths

    @property
    def evidence_path(self) -> Path:
        """Where a landed session's ``build/`` evidence is retained in the workspace."""
        return self.main.root / "build" / "session-evidence" / self.id

    def _archive_evidence(self) -> None:
        source = self.path / "build"
        if not source.is_dir() or source.is_symlink():
            return
        destination = self.evidence_path
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > EVIDENCE_LIMIT:
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def close(self) -> None:
        for stream in reversed(self.streams):
            stream.close()
        self.streams.clear()
        if self.landed and self.path.exists():
            # Reports cite build/ receipts; keep them after the checkout is reclaimed.
            try:
                self._archive_evidence()
            except OSError:
                return  # keep the whole checkout rather than lose evidence
            shutil.rmtree(self.path)
