"""HomAlg fork boundary, repository preservation, and fail-closed rollback regressions."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

from archon_horizon.config.loader import build_orchestrator
from archon_horizon.config.schema import ConfigError, WorkspaceConfig
from archon_horizon.core.inbox import InboxDraft, InboxKind, inbox_attention_rank
from archon_horizon.core.sessions import Focus, RunRecord
from archon_horizon.core.tasks import HorizonTask, TaskStatus, WriteSet
from archon_horizon.core.workspace import Workspace
from archon_horizon.hgraph.sync import parse_lean
from archon_horizon.harnesses.null import NullHarness
from archon_horizon.inboxes.filesystem import FilesystemInboxProvider
from archon_horizon.orchestration.hooks import HookResult, run_hook, violation_paths
from archon_horizon.vcs.git import WorkspaceGit, neutralize_nested_git


def workspace(root: Path, hooks=None) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / ".gitignore").write_text(".archon-horizon/\nprivate/\n*.lock\n")
    (root / "lakefile.toml").write_text('name = "Test"\n')
    (root / "config.yaml").write_text(yaml.safe_dump({
        "workspace": {"name": "test", "horizon_agent": {"harness": "hor"},
                      "manage_gitignore": False, "hooks": hooks or {}},
        "harnesses": {"hor": {"kind": "null"}},
        "projects": {"HomAlg": {"path": "."}},
    }))


def task(orch):
    orch.task_store.put(HorizonTask(id="T-1", project="HomAlg", projects=("HomAlg",),
                                  title="test", objective="test", status=TaskStatus.QUEUED,
                                  write_set=WriteSet(projects=("HomAlg",))))


def test_multiline_lean_declarations_preserve_body_namespace():
    text = 'namespace N\n/-- A doc. -/\nprivate theorem\n\n -- name follows\n proof₁ : True := by trivial\ndef\n value : Nat := 1\nend N\n'
    rows = parse_lean(text)
    assert [r["fqname"] for r in rows] == ["N.proof₁", "N.value"]
    assert "private theorem\n" in rows[0]["body"]
    assert "A doc." in rows[0]["doc"]
    assert parse_lean('instance\n : Inhabited Nat := ⟨0⟩\n') == []


def test_root_git_survives_two_null_sessions_and_ledger_tracks_root(tmp_path):
    workspace(tmp_path)
    (tmp_path / "proof.lean").write_text("theorem t : True := by trivial\n")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "source.pdf").write_bytes(b"private")
    orch = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
    task(orch)
    orch.run(RunRecord(id="", focus=Focus(tasks=("T-1",)), rounds_requested=2))
    assert (tmp_path / ".git" / "HEAD").is_file()
    assert not (tmp_path / ".git.disabled").exists()
    git = WorkspaceGit(tmp_path)
    files = git._run(["ls-tree", "-r", "--name-only", "HEAD"]).splitlines()
    assert "proof.lean" in files
    assert not any(p.startswith((".git/", ".archon-horizon/", "private/")) for p in files)
    # Explicit '.' also works after the first commit, through the ledger pre-commit hook.
    (tmp_path / "proof.lean").write_text("theorem t : True := trivial\n")
    assert git.commit("whole tree", paths=["."])
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".git").mkdir()
    assert neutralize_nested_git(nested, workspace_root=tmp_path)


def test_hooks_capture_sha_and_rollback_only_violations_even_after_done(tmp_path):
    script = tmp_path / "hook.py"
    script.write_text('''import json, pathlib, sys, os
root = pathlib.Path.cwd()
with (root / "calls").open("a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "before":
    assert os.environ["ARCHON_HORIZON_TASK"] == ""
    raise SystemExit(0)
assert os.environ["ARCHON_HORIZON_TASK"] == "T-1"
assert os.environ["ARCHON_HORIZON_SESSION_DIR"] == sys.argv[4]
if (root / "forbidden.lean").read_text() != "original":
    print(json.dumps({"violations": ["forbidden.lean", "new.lean"]}))
    raise SystemExit(4)
''')
    py = shlex.quote(sys.executable)
    workspace(tmp_path, {"before_round": [f"{py} hook.py before"],
                         "after_session": [f"{py} hook.py after {{start_sha}} {{end_sha}} {{session_dir}}"]})
    (tmp_path / "forbidden.lean").write_text("original")
    (tmp_path / "allowed.lean").write_text("before")
    orch = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
    task(orch)
    original = orch.horizon

    class Writer:
        def run_task(self, context):
            (tmp_path / "forbidden.lean").write_text("wrong")
            (tmp_path / "allowed.lean").write_text("legitimate")
            (tmp_path / "new.lean").write_text("out of scope")
            current = orch.task_store.get("T-1")
            orch.task_store.append_history("T-1", {"actor": "horizon", "field": "status", "from": "running", "to": "done"})
            orch.task_store.put(dataclasses.replace(current, status=TaskStatus.DONE))
            return original.run_task(context)

    orch.horizon = Writer()
    orch.run(RunRecord(id="", focus=Focus(tasks=("T-1",)), rounds_requested=1))
    assert (tmp_path / "forbidden.lean").read_text() == "original"
    assert (tmp_path / "allowed.lean").read_text() == "legitimate"
    assert not (tmp_path / "new.lean").exists()
    assert orch.task_store.get("T-1").status is TaskStatus.QUEUED
    sessions = list((tmp_path / ".archon-horizon/runs/0001/sessions").glob("*horizon*"))
    meta = json.loads((sessions[0] / "meta.json").read_text())
    assert meta["status"] == "scope-violation"
    assert len(meta["start_sha"]) == len(meta["end_sha"]) == 40
    assert meta["start_sha"] != meta["end_sha"]
    assert len((sessions[0] / "hooks.log").read_text().splitlines()) == 2
    assert "scope revert: T-1" in WorkspaceGit(tmp_path)._run(["log", "--format=%s"]).splitlines()
    issues = FilesystemInboxProvider(tmp_path / ".archon-horizon/inbox/local").list_items()
    assert any(i.author == "homalg" and "forbidden.lean" in i.body for i in issues)
    assert (tmp_path / ".git").is_dir()


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape", ".git/config", "a/../b", ".archon-horizon/vcs/workspace.git/HEAD", "bad\\name"])
def test_scope_rejects_untrusted_paths(tmp_path, path):
    result = HookResult("test", 4, json.dumps({"violations": [path]}))
    with pytest.raises(ValueError):
        violation_paths(result, Workspace(name="test", root=tmp_path))


def test_scope_rejects_parent_symlink_escape(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        violation_paths(HookResult("test", 4, '{"violations":["link/x"]}'), Workspace(name="test", root=tmp_path))


def test_hook_argument_expansion_never_injects_shell(tmp_path):
    result = run_hook(f'{shlex.quote(sys.executable)} -c "import sys;print(sys.argv[1])" {{task_id}}',
                      Workspace(name="test", root=tmp_path),
                      values={"task_id": "a ; touch PWNED"}, log_path=tmp_path / "hooks.log")
    assert result.returncode == 0
    assert result.stdout.strip() == "a ; touch PWNED"
    assert not (tmp_path / "PWNED").exists()


def test_hook_timeout_recorded(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 1800
        raise subprocess.TimeoutExpired("test", 1800)
    monkeypatch.setattr(subprocess, "run", timeout)
    result = run_hook("test", Workspace(name="test", root=tmp_path), values={}, log_path=tmp_path / "hooks.log")
    assert result.returncode == 124
    assert "timed out" in result.stderr


def test_config_rejects_malformed_hooks_and_boolean():
    for ws in ({"hooks": {"other": []}}, {"hooks": {"before_round": "rm"}}, {"manage_gitignore": "false"}):
        with pytest.raises(ConfigError):
            WorkspaceConfig.from_raw({"workspace": {"name": "x", **ws}})


def test_new_inbox_kinds_keep_issue_attention(tmp_path):
    provider = FilesystemInboxProvider(tmp_path)
    items = [provider.create_item(InboxDraft(kind=k, body="Title\n\nDetails")) for k in (InboxKind.ISSUE, InboxKind.DISPUTE, InboxKind.DOCUMENT_ERROR)]
    assert len({inbox_attention_rank(i) for i in items}) == 1
    assert {i.kind for i in provider.list_items()} == {InboxKind.ISSUE, InboxKind.DISPUTE, InboxKind.DOCUMENT_ERROR}


def test_no_commit_session_has_equal_hook_shas_and_failure_keeps_running(tmp_path):
    script = tmp_path / "hook.py"
    script.write_text('import sys\nprint("failure output")\nraise SystemExit(7)\n')
    workspace(tmp_path, {"after_session": [f"{shlex.quote(sys.executable)} hook.py {{start_sha}} {{end_sha}}"]})
    orch = build_orchestrator(tmp_path, harnesses={"hor": NullHarness("")})
    task(orch)
    assert len(orch.run(RunRecord(id="", focus=Focus(tasks=("T-1",)), rounds_requested=2))) == 2
    sessions = list((tmp_path / ".archon-horizon/runs/0001/sessions").glob("*horizon*"))
    assert len(sessions) == 2
    for session in sessions:
        meta = json.loads((session / "meta.json").read_text())
        assert meta["start_sha"] == meta["end_sha"]
    assert len(FilesystemInboxProvider(tmp_path / ".archon-horizon/inbox/local").list_items()) == 2


def test_init_manage_gitignore_false_preserves_bytes(tmp_path):
    from archon_horizon.commands.init import InitCommand
    workspace(tmp_path)
    before = (tmp_path / ".gitignore").read_bytes()
    InitCommand(tmp_path, interactive=False, update=True).run()
    assert (tmp_path / ".gitignore").read_bytes() == before


def test_init_manage_gitignore_false_refuses_missing_git(tmp_path):
    from archon_horizon.commands.init import InitCommand
    workspace(tmp_path)
    import shutil
    shutil.rmtree(tmp_path / ".git")
    with pytest.raises(ValueError, match="requires an existing"):
        InitCommand(tmp_path, interactive=False, update=True).run()
    assert not (tmp_path / ".git").exists()


def test_private_dashboard_rejects_unignored_export_and_workflow(tmp_path):
    from archon_horizon.commands.dashboard import DashboardCommand
    workspace(tmp_path)
    with pytest.raises(ValueError, match="gitignored"):
        DashboardCommand(tmp_path, out="dashboard").run()
    with pytest.raises(ValueError, match="private"):
        DashboardCommand(tmp_path, out="private", workflow=True).run()
    assert not (tmp_path / "dashboard").exists()
    assert not (tmp_path / ".github").exists()


def test_private_dashboard_ignored_export_never_commits(tmp_path):
    from archon_horizon.commands.dashboard import DashboardCommand
    workspace(tmp_path)
    git = WorkspaceGit(tmp_path)
    git.init()
    git.commit("initial", paths=["."])
    sha = git.current_sha()
    DashboardCommand(tmp_path, out="private/dashboard").run()
    assert (tmp_path / "private/dashboard/index.html").is_file()
    assert git.current_sha() == sha
    assert subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=tmp_path, capture_output=True).returncode != 0


def test_registered_homalg_remarks_and_examples_remain_published(tmp_path):
    from archon_horizon.blueprint.checks import is_countable
    from archon_horizon.blueprint.workspace import published_dag
    from archon_horizon.core.workspace import Project
    workspace(tmp_path)
    cache = tmp_path / ".archon-horizon/blueprints/HomAlg.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"nodes": [
        {"id": "Book1945.Rem_1", "kind": "remark"},
        {"id": "Book1945.Ex_2", "kind": "example"},
        {"id": "plain-prose", "kind": "remark"}],
        "edges": [{"source": "Book1945.Rem_1", "target": "Book1945.Ex_2"}]}))
    ws = Workspace(name="test", root=tmp_path, projects={"HomAlg": Project(name="HomAlg", path=Path("."))})
    dag = published_dag(ws, "HomAlg")
    assert [n["id"] for n in dag["nodes"]] == ["Book1945.Rem_1", "Book1945.Ex_2"]
    assert len(dag["edges"]) == 1
    assert is_countable({"content_type": "remark", "label": "Book1945.Rem_1"})
