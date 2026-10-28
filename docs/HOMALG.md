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
Events are `hook.completed` or `hook.failed`. Ordinary failures create a local
`issue` from `homalg` to `human`; later sessions can continue.

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
