"""Inspect and land isolated session checkouts left behind by interrupted runs."""

from __future__ import annotations

from pathlib import Path

import typer

from archon_horizon.commands.shared import emit_json
from archon_horizon.log import log
from archon_horizon.vcs.session import SessionConflict

app = typer.Typer(
    help=(
        "Isolated session checkouts (workspace.session_ledger). A run that dies before "
        "landing keeps its checkout under session-worktrees/; land it here."
    ),
    no_args_is_help=True,
)


def _orchestrator(root: Path):
    from archon_horizon.config.loader import build_orchestrator
    from archon_horizon.harnesses.null import NullHarness
    from archon_horizon.config.loader import load_config

    cfg = load_config(root)
    # Landing never runs an agent; a null harness avoids engine setup.
    harnesses = {name: NullHarness("") for name in cfg.harnesses}
    return build_orchestrator(root, harnesses=harnesses)


def _refuse_live(root: Path, orch) -> None:
    from .ps import live_runs

    live = live_runs(orch.workspace.state_path / "runs")
    if live:
        log.error("A run is live (" + ", ".join(row["run"] for row in live)
                  + "); stop it before landing preserved sessions.")
        raise typer.Exit(1)


@app.command("list")
def list_sessions(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List preserved checkouts, their owning session and whether they landed."""
    from archon_horizon.orchestration.recovery import preserved_sessions

    root: Path = ctx.obj["root"]
    rows = preserved_sessions(_orchestrator(root))
    if as_json:
        emit_json({"checkouts": rows})
        return
    if not rows:
        log.info("No preserved session checkouts.")
        return
    for row in rows:
        owner = (f"{row['run']}/{row['session']} ({row.get('session_status')})"
                 if "task" in row else f"owner {row['owner']} {row.get('candidates', [])}")
        state = f"landed {row['landed'][:7]}" if row["landed"] else "not landed"
        log.info(f"{row['checkout']}  {state}  {owner}")


@app.command("land")
def land(
    ctx: typer.Context,
    checkout: str = typer.Argument(..., help="Checkout id under session-worktrees/."),
    session: str | None = typer.Option(
        None, "--session", help="Owning session as RUN/SESSION (e.g. 0026/0002) when it cannot be inferred."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would land; change nothing."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Land a preserved checkout with the normal conflict checks, then run after_session hooks.

    Already-landed checkouts only get the hooks they missed. The task's status is
    never set to done here: an interrupted task returns to queued and the hooks
    judge it exactly as at the end of a normal session.
    """
    from archon_horizon.orchestration.recovery import land_checkout

    root: Path = ctx.obj["root"]
    orch = _orchestrator(root)
    _refuse_live(root, orch)
    try:
        result = land_checkout(orch, checkout, session_ref=session, dry_run=dry_run)
    except (SessionConflict, KeyError) as exc:
        if as_json:
            emit_json({"ok": False, "error": str(exc)})
        log.error(str(exc))
        raise typer.Exit(1) from exc
    if as_json:
        emit_json({"ok": True, **result})
        return
    if dry_run:
        log.info(f"{checkout}: task {result['task']} from {result['run']}/{result['session']}; "
                 + ("already landed" if result["already_landed"] else f"{len(result['changed'])} changed file(s)")
                 + ("; after_session hooks pending" if result["hooks_pending"] else ""))
        for path in result["changed"]:
            log.info(f"  {path}")
        return
    log.success(f"Landed {checkout} for {result['task']}: {len(result['landed_files'])} file(s); "
                f"status {result['status']}" + ("; hooks failed (see the inbox)" if result["hooks_failed"] else ""))
