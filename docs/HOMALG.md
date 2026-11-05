# HomAlg engine integration

Install the wheel attached to `v0.1.5-homalg.1` after it is published. Local development:

```bash
uv sync --locked --extra dev
npm --prefix src/archon_horizon/frontend ci
npm --prefix src/archon_horizon/frontend exec -- tsc --noEmit
npm --prefix src/archon_horizon/frontend run build
uv run pytest -q
uv run python scripts/version.py --check
uv build
uv run python scripts/check_wheel.py
```

Run TypeScript checking from the frontend directory if your npm version does not
forward the project location (`cd src/archon_horizon/frontend && npx tsc --noEmit`).
The wheel must contain `archon_horizon/frontend/dist/index.html`; source-only builds
without the frontend are not a releasable installation.

## Workspace configuration

The `homalg` CLI owns creating this configuration and the work branch. Initialize
only after the user repository and its `.gitignore` exist. A root-path project keeps
the user `.git` independent of the out-of-tree ledger.

```yaml
workspace:
  name: HomAlgDB
  manage_gitignore: false
  horizon_agent: {harness: agent}
  scheduler: {max_parallel_sessions: 1}
  hooks:
    before_round:
      - homalg blueprint-tex --active
      - homalg private-snapshot
    after_session:
      - homalg after-session --task {task_id} --session {session_dir} --since {start_sha} --until {end_sha}
harnesses:
  agent: {kind: "null"}
projects:
  HomAlg:
    path: .
    blueprint: {path: blueprint/src}
    build: {command: homalg build-active}
github: {enabled: false}
```

Use `horizon --root <repo> init --update --no-interactive` to install the bundled
skills and descriptors without changing existing configuration. Run
`horizon --root <repo> run '*' --rounds N`; use `--no-dashboard` for automated tests.
`horizon check HomAlg.<K>.<LocalId>` runs from inside the workspace and shares
`.archon-horizon/cache/checks/workspace.lock` with HomAlg export/build operations.
`horizon check` without explicit targets honors the root project build command;
explicit targets still use `lake build`. The child receives `HOMALG_BUILD_LOCK_HELD=1`
only while the shared lock is held, so `homalg build-active` must not acquire it again.
Agents never build directly or operate on the user's Git history. `hgit` selects
the private ledger; only `homalg submit` publishes reviewed work.

## HomAlg draft preparation and review batches

The companion HomAlgDB repository documents its isolated draft-worker and frozen
handoff workflow in `docs/PARALLEL_WORKFLOW.md`. These are HomAlg CLI capabilities,
separate from the native concurrent-session mode below. They do not change this
fork's default scheduler, task-launch permissions or human guards.

Writable draft workers need explicit disjoint ownership, their own source/ledger
and build state, and fixed dependency inputs. The native lead verifies author
handoffs, obtains real independent per-node reports, then integrates and registers
them at the guarded canonical boundary. A source snapshot grants no approval.
Batch review registration shares one fresh compilation/export operation while
preserving each node's evidence and hashes; it is not cached review or a blanket
mathematical verdict. Author preparation and worker checks remain separate work.

The companion CLI supports `workspaces create --native --task TASK` for the actual
active lead, normally without copying a large cache. The native-only `workspaces
collect ID --by LEAD` stages frozen owned source after task, scope, baseline and
dependency checks; it grants no compilation or review and requires no worker build.
Native source authors can therefore run in parallel while canonical checks use
one serialized build lane. Current canonical checks/export, independent fidelity
review, stage acceptance and the outer hook remain required. Checked workers can
use `promote --native`; external creation/promotion retain the idle guard and
external promotion still requires current worker compilation. Worker source is a
public projection with its own ledger, without runtime config/history/private
state. Native authorization checks the actual run/session and process ancestry,
not just environment variable claims. Workers do not collect or promote themselves.

Mathematical sessions use scoped content/stage checks with required fresh export
and final validation. Full `make test`, `make validate` and `make site` regressions
are required when tooling, schemas or site implementation changes, not each
unchanged-tooling mathematics task. Complete source proof prose, DAG annotations,
comments, metadata, generated-data/blueprint freshness and independent mathematical
review remain required. Dispatch all independent ready workers before joining;
housekeeping pauses only work whose safe execution depends on its result.

After the prescribed stage check and task-status action, the native lead preserves
its evidence and returns control to Horizon. It must not manually invoke
`homalg after-session` or add an extra `homalg plan --advance` as finalization.
The engine runs the configured outer hook; HomAlg's hook includes eligible advance
planning. The outer coordinator verifies its actual private/scope, validation,
evaluation and resulting task-state records before reporting acceptance. Explicitly
authorized external recovery retains its separate provenance and completion
protocol. A successful worker, promotion or hook subprocess alone is insufficient.

Update the tracked descriptor and skill sources, then test/install the matching
fork and refresh resources at an idle boundary. Do not patch installed copies in
a live formalization workspace. The package version remains `0.1.5+homalg.1` here;
that string alone does not prove an older published wheel contains these later
instruction changes. Record the actual source revision and installed artifacts.

## Companion planning and status-history reads

The companion HomAlg tooling reuses authored node headers and inventory occurrence
ranks within each `unlocked_tasks` call. Every proposal keeps its original node
selection, source order, `node_ids` (including T and INV) and rendered objective.
The next planning call reads those inputs again. A validation `Context` similarly
resolves each document's already-fetched status ref to an immutable commit object
ID and reads its schema-valid history once; a missing ref remains absent in that
context. New contexts read the then-available refs with their own schema stores.

Each node's applicable scope test still evaluates claim, policy, time and effective
expiry. These are in-memory input reads, with no network fetch or persistent/
process-wide cache; they do not cache claim decisions or validation verdicts.
Task completion evaluation, fast/full validation, transcription reconciliation,
proof deferral, review gates and all three export input snapshots are unchanged.
This is a HomAlg implementation change, not a change to this fork's scheduler,
concurrent-session mode or version `0.1.5+homalg.1`.

The companion source checkout's `docs/IMPLEMENTATION.md` separates actual isolated
microbenchmarks from final acceptance. The [public main copy](https://github.com/YijunYuan/homalg-db/blob/main/docs/IMPLEMENTATION.md)
may lag these unpublished source changes; that moving URL does not attest to an
installed revision. The reported proposal/scope timings use fictional fixed inputs
and do not measure a whole production hook or CE task.

## Companion HomAlg backup routing

Horizon journaling and the external `homalg after-session` hook have separate Git
responsibilities. The local HomAlgDB backup follow-up selects
`.archon-horizon/vcs/workspace.git` when present: archive commits and the exact
source-commit push to `backup/<login>/work` use that ledger, preserving user Git
HEAD/index on this route. Without a ledger, the ordinary user-Git backup path
remains. Invalid/symlinked ledgers or inherited Git repository/index redirection
are refused rather than silently falling back.

Remote names are resolved read-only from the user checkout to a single work-backup
push destination. Multiple targets, or a target matching any configured public-origin
fetch/push URL, are refused. The private literature repository pushes its current
named branch separately, from its own checkout to its own origin. These checks do
not establish remote visibility, access rights or all possible URL equivalences.

No backup push is forced. A non-fast-forward rejection reports `backed_up: false`,
`reason: non-fast-forward`, the failed `operation`, `source` and `source_commit`;
local archive commits and an earlier private push can remain. Preserve the evidence
and resolve divergence deliberately. `--force` only bypasses the backup interval.
The change is confined to the companion backup path, not claims, submission or
numeric status publication. See the companion
[publication-boundary documentation](https://github.com/YijunYuan/homalg-db/blob/main/docs/ENGINE.md#publication-boundary).

The companion HomAlgDB fix was locally tested at source-ledger revision
`aa869dc88c7b933cbe4b9c8bc1a21702823de2ae`; its `docs/IMPLEMENTATION.md` records
full validation and the independent review's actual Git scenarios. The moving
documentation link and unchanged fork version `0.1.5+homalg.1` do not prove live
installation, publication or completed recovery. The legacy run0012 HomAlg hook
did create a discussion-archive commit in user Git before its later backup failed.
That history was retained. Core-ledger and worker-test preservation results do not
assert that the old external hook left user Git unchanged throughout that run.

## Hook contract

Hooks are lists of commands under `workspace.hooks.before_round` and
`workspace.hooks.after_session`. Each command is split into arguments, then each
argument is expanded with `str.format`: `task_id`, `run_id`, `session_dir`,
`start_sha`, `end_sha`. Quoting in the template groups arguments; inserted values
cannot add command arguments. No shell is invoked. Literal braces must be escaped
as `{{` and `}}` in command templates.

Commands run from the workspace root with a 1,800-second timeout and
`ARCHON_HORIZON_TASK` / `ARCHON_HORIZON_SESSION_DIR` set. Before-round hooks use
empty task/session values and log under the run directory. Session hooks log under
`<session_dir>/hooks.log` as JSON lines preserving stdout, stderr and exit code.
Events are `hook.completed` or `hook.failed`. Failures create a local `issue`
from `homalg` to `human`. A failed before-round hook stops dispatch. A failed
after-session hook holds only its task: task metadata records `boundary` and
`finalization_hold`, and an agent's premature `done` returns to `queued`.
Independent ready tasks may continue. A failed session is `hook-failed` (or
`scope-violation`), never accepted merely because the agent exited successfully.
`task.agent_finished` precedes finalization; `task.finished` and the session end
timestamp follow it. A roadmap completion explicitly synced from this failing
task is restored to its preceding status; unrelated completions are preserved.

Inspect the retained hook log and partial effects before recovery. Explicit
`horizon run <task>`, focused resume, or `horizon task set <task> --status queued`
releases the finalization hold; none supplies missing approval or bypasses hooks.
Within one run, blocked/failed tasks are no longer reopened each round. A new
explicit start/resume can retry them once; cancelled tasks require an explicit
requeue. Planner-owned `dispatch_hold` is separate and is cleared only when the
planner's prerequisites permit work. The scheduler uses the current task priority.

The start SHA is captured immediately before the agent invocation. End SHA is
captured after session integration; they are equal if there was no commit. Exit 4
requires stdout to contain a JSON object with a nonempty `violations` list of
workspace-relative file paths. The engine validates the complete path set, restores
those files from the start tree (deleting newly created files), commits
`scope revert: <task>`, requeues the task with system history, records
`scope-violation` metadata and sends a human issue. It reruns that hook once with
the restored end SHA. Absolute/traversal paths, engine-state paths, parent symlink
escapes and unsafe restored symlinks are rejected. Private-library restoration is
handled separately by HomAlg's hook; the engine does not follow it outside the root.

## Private dashboard and updates

Static exports contain private logs and can target only an external directory or
a directory ignored by the user repository. They never commit or generate Pages
workflows. The local live dashboard remains the normal inspection path.

`horizon update` reads the latest stable release of `YijunYuan/homalg-horizon` and
installs its wheel with the compiled frontend. `HOMALG_HORIZON_REPOSITORY=owner/repo`
can select another HomAlg fork. Tags must be `vMAJOR.MINOR.PATCH-homalg.N`; an asset
from a different repository or branch is refused. `ARCHON_HORIZON_NO_VERSION_CHECK=1`
disables all background version fetches. Updates are never attempted by normal tests.

## Optional concurrent sessions (M7-T6)

The reliable default is one run per workspace. An OS run-policy lock enforces this,
including when `horizon run` is called directly. To enable concurrent runs, set
`workspace.session_ledger: true`. Each task must have a nonempty explicit file scope;
the user repository must already have a committed HEAD. This mode requires POSIX
`flock`. Conflicting scope leases are refused before an agent starts.

`scheduler.max_parallel_sessions` limits the selected batch; the current run loop
executes that batch sequentially. Raising it does not create concurrent sessions.
Use native subagent delegation within the lead or explicitly isolated separate
runs for actual parallel execution; keep canonical acceptance coordinated.

On Linux, `horizon ps --kill <run>` captures descendants before terminating the
parent, including children in separate process groups. It checks process start
identities and uses pidfds where available; unrelated processes are untouched.
New process markers retain the parent's start identity to reject PID reuse.
Other platforms retain parent-only termination. Inspect interrupted hook evidence
and remaining processes before recovery; killing a run does not finalize it.

Every session gets a separate source checkout and its own `hgit` ledger under
`.archon-horizon/session-worktrees/<id>/`. The user repository's HEAD stays untouched.
The source snapshot includes current main-ledger work, rather than only the last
public commit. Runtime state, the build cache and its lock remain shared. The agent
receives `HOMALG_SESSION_MAIN_ROOT` identifying the canonical workspace and works
inside its isolated root. Native resume IDs are cleared in this mode: a fresh engine session reads the latest
landed snapshot rather than reusing a conversation bound to a reclaimed checkout.

Landing, boundary hooks and graph generation are serialized. The session's source
interval is imported as `refs/homalg-sessions/<id>` so another run's changes cannot
appear in its scope check. Each changed path must still match its starting contents
and mode in the canonical worktree. A changed parent file causes the entire landing
to be refused; the source checkout remains available at the recovery path recorded
in session metadata and the human inbox. No conflicting parent edit is overwritten.
Successful checkouts are removed after their audit interval has been retained.

Scope rollback restores only this session's paths. Its hook retry receives a
corrected isolated audit tree, not the global ledger HEAD, which might already
contain another run's work. Generated output is refreshed against the combined
canonical sources after landing. Task/roadmap/inbox/graph CLI access is serialized
with these boundaries. Hook subprocesses carry `HOMALG_BOUNDARY_LOCK_HELD=1` so nested
state CLI calls do not reacquire their parent's lock.

Private transcription (`references/` or `literature/.private/`) and explicitly shared
authored scopes such as `taxonomy/` are refused in concurrent mode; use the serial
mode for those tasks. Incidental edits to shared authored files are subject to the
same optimistic conflict check and may require a new session if another run lands
first. Do not manually regenerate shared hgraph/blueprint data from an isolated
checkout: boundary hooks own generation. This is source isolation, not an operating
system sandbox; agents must follow the repository's private-file and tool policies.

Regression coverage runs overlapping sessions in both threads and independent
processes. It verifies disjoint landing, immutable audit ranges, recovery on parent
conflict, conflicting lease refusal, preserved root `.git`, combined generated output
and scope rollback after the other run has already finished.
