"""Workspace-tunable health limits and a closing audit limited to proof claims."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from archon_horizon.config.loader import load_config
from archon_horizon.config.schema import ConfigError
from archon_horizon.core.collection_health import (
    DEFAULT_HEALTH_LIMITS,
    health_limits,
    task_health_warnings,
)
from archon_horizon.core.tasks import HorizonTask, TaskStatus


def write_config(root: Path, health=None) -> None:
    workspace = {"name": "w", "horizon_agent": {"harness": "h"}}
    if health is not None:
        workspace["health"] = health
    (root / "config.yaml").write_text(yaml.safe_dump({
        "workspace": workspace, "harnesses": {"h": {"kind": "null"}},
        "projects": {"P": {"path": "."}},
    }))


def queued(count: int) -> list[HorizonTask]:
    return [HorizonTask(id=f"t{index}", project="P", objective="x", status=TaskStatus.QUEUED)
            for index in range(count)]


def test_planned_queue_within_workspace_threshold_is_not_a_warning(tmp_path):
    write_config(tmp_path, {"open_tasks": 200})
    limits = health_limits(load_config(tmp_path).health)
    assert limits.open_tasks == 200
    assert limits.open_conversations == DEFAULT_HEALTH_LIMITS.open_conversations
    assert not task_health_warnings(queued(90), limits)
    assert task_health_warnings(queued(201), limits)
    assert task_health_warnings(queued(90))  # the default threshold is unchanged


def test_unknown_or_invalid_health_values_are_ignored_or_refused(tmp_path):
    assert health_limits({"unknown": 5, "open_tasks": 0}) == DEFAULT_HEALTH_LIMITS
    write_config(tmp_path, {"open_tasks": "many"})
    with pytest.raises(ConfigError, match="workspace.health"):
        load_config(tmp_path)


def test_task_list_uses_the_workspace_threshold(tmp_path):
    write_config(tmp_path, {"open_tasks": 500})
    from archon_horizon.store.filesystem import FilesystemTaskStore  # noqa: F401
    result = subprocess.run(
        [str(Path(__file__).resolve().parents[1] / ".venv/bin/horizon"), "--root", str(tmp_path),
         "task", "list", "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"warnings": []' in result.stdout


def test_closing_honesty_audit_is_tied_to_proof_and_certificate_claims():
    source = (Path(__file__).resolve().parents[1]
              / "src/archon_horizon/agents/prompts.py").read_text()
    assert "proof\\n" in source and "`honesty-reviewer`" in source
    assert "completion claim" not in source
