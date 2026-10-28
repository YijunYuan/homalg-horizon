# Upstream provenance

This is the HomAlgDB adaptation of [frenzymath/Archon-Horizon](https://github.com/frenzymath/Archon-Horizon).
The upstream Apache-2.0 `LICENSE`, `NOTICE` and third-party attribution remain intact.
Fork version: **0.1.5+homalg.1**. Intended release tag: **v0.1.5-homalg.1**.

The requirements identify upstream `v0.1.5` with commit `da5e685`. The actual annotated
upstream tag peels to `5d603f8ee7d66de81eff2863cbb6f29b4440712a`; the explicitly requested
commit is its immediate child, `da5e685a3c9096e6e18a743771594db1d210c682`
(`skills: treat a missing or stub blueprint as unfinished orientation`). This fork
uses that explicit commit as its baseline. The original shared checkout is untouched.

## Patch ledger

| Patch | Change | Regression evidence |
| --- | --- | --- |
| P0 | Extract Lean declarations whose names follow the keyword on another line | `test_homalg_runtime.py` |
| P1 | Preserve the user's `.git` when a project resolves to the workspace root | Two null-engine sessions; nested repositories still neutralized |
| P2 | Configured boundary hooks, argv interpolation, SHA range, timeout, logs and inbox failures | Placeholder/no-change/failure/timeout tests |
| P3 | Restore only reported violating files from the session start ledger, requeue even a completed task, retry hook once | Real ledger rollback with preserved legitimate edits and traversal rejection |
| P4 | Preserve `.gitignore` byte for byte when `manage_gitignore: false`; require existing user repository | Init tests |
| P5 | Live overlay, document filtering and private reference viewer | Dashboard patch and dedicated endpoint/frontend tests |
| P6 | `dispute` / `document-error` inbox kinds and repeated node scope option | Inbox taxonomy and CLI tests |
| P7 | HomAlg instructions and scoped node-writer; generated blueprint writer disabled | Skill contract/drift and descriptor compilation tests |
| P8 | Private static exports only; no automatic commits or Pages workflow | Static export/CLI privacy tests |
| P9 | `.` means the whole source worktree in the ledger allowlist | Root-project integration and pre-commit tests |
| P10 | Update/version checks use the fork's tagged release wheel | Tag, wheel origin, revision ordering and disabled-check tests |
| M7-T6 | Optional isolated session sources, disjoint leases, audited landing and serialized hooks | Overlapping thread/process sessions preserve attribution, rollback and generated output |

The generated dashboard bundle is built before packaging. The release workflow
verifies the package/tag version, runs tests and checks wheel contents before
attaching artifacts. No releases or remote repository settings are created by a
local build. The inherited demo Pages publisher is removed because engine logs and
reference data belong in private local storage.

The HomAlg owner approved independent agent cross-review on 2026-10-28.
Packaged skills and reviewer descriptors respect each repository's explicit
`horizon/review-policy.yaml`: enabled repositories can advance routine foundation,
first-definition and explained-deviation gates using actual independent sessions
and current evidence. Missing/disabled policy retains the original human gates;
genuine human records and unresolved source disputes remain protected.

## Upstream issue record

These are reproducible local findings; no GitHub issue has been filed automatically.

- **Root project disables user repository:** `neutralize_nested_git(root)` renamed
  the root `.git` when a project used `path: .`. P1 adds the workspace-root guard.
- **Whole-worktree allowlist drops all files:** the string `.` was treated as a
  literal filename/prefix by both Python staging and the ledger pre-commit hook.
  P9 recognizes it as the whole worktree while retaining ignore/exclusion policy.
- **Split declaration keyword/name is missed:** the single-line declaration regex
  failed valid Lean declarations. P0 joins only keyword-only lines for matching.
- **Tag/SHA discrepancy:** `v0.1.5` and `da5e685` are adjacent revisions, not synonyms.

Upstream general-purpose behavior remains the baseline outside documented patches.
Tests that asserted public static publication, the old exact inbox enum, or
blueprint authorship were updated to assert the corresponding HomAlg behavior.
