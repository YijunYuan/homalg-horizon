# HomAlgDB dashboard patch (P5/P6)

The fork keeps upstream `lean_status` as the fallback and reads authoritative
HomAlgDB status at request time. `GET /api/overlay` reads
`.archon-horizon/overlay/HomAlg.json`, whose current shape is
`{"nodes":{"DocKey.LocalId":{...}}}`. The legacy direct node map is also accepted.
Only the known status fields are merged into `/api/blueprints` and
`/api/blueprint/dag`; ids, source anchors and graph structure cannot be overwritten.
The `homalg` member identifies a node with an overlay. Each request rereads the
file, and the cached published DAG is never modified. Removing an overlay restores
the upstream fallback. The browser merges the current light poll into its heavy
source record so a page already open updates without a graph resynchronization.

The document selector stores its value in `doc=` and preserves it in primary
navigation. It scopes graph labels by the `DocKey.` prefix, tasks and roadmap
items by `metadata.homalg.doc`, inbox items by `scope.blueprint_nodes`, and runs
by their focused tasks or session tasks. The blueprint reader removes other
documents' labeled blocks; graph edges are retained only when both endpoints
belong to the displayed document. Choosing All documents restores the workspace.
The two additional inbox kinds, `dispute` and `document-error`, are available in
the dashboard's filters and composer with distinct badge colors.

The graph and reader use the four HomAlgDB headline colors, subtype glyphs,
fidelity/dispute outlines, bridge/citation shapes, and status badges. Pending
citations, nonstandard axioms, repair, reopened, dependency mismatch, unreviewed
and approximate states remain visible. The approximate badge explicitly describes
its text-based estimate. Upstream formalization kinds remain unchanged for
ordinary blueprints; namespaced HomAlgDB remark/example nodes remain available.

Source anchors are split on commas and validated as `<slug>:page-NNNN`. Links
open `/reference?slug=...&page=NNNN`, displaying the original transcription text,
page image and review history. Endpoints are:

- `GET /api/reference-page?slug=...&page=NNNN`: JSON containing `text`, the page's
  `status` record, `stale` and its current SHA-256.
- `GET /api/reference-page.png?slug=...&page=NNNN`: the local PNG image.

These endpoints resolve `references/` once as the private-library root, allowing
the intended root symlink. The requested text, PNG and transcription status file
must resolve inside that root. Invalid identifiers, traversal and child symlink
escapes return 404. Responses use `Cache-Control: private, no-store`. Missing page
images have an explicit UI fallback. A changed reviewed transcription is displayed
as transcribed/stale without rewriting the private status file. Transcriptions
are rendered as escaped text, not executable HTML. Private page endpoints are not
included in static dashboard export registries.

Verification commands, from the fork root:

```sh
python -m pytest tests/test_homalg_dashboard.py tests/test_server.py tests/test_static_export.py
npm ci --prefix src/archon_horizon/frontend
npm run typecheck --prefix src/archon_horizon/frontend
npm test --prefix src/archon_horizon/frontend
npm run build --prefix src/archon_horizon/frontend
npm exec --prefix src/archon_horizon/frontend -- playwright install chromium
npm run test:e2e --prefix src/archon_horizon/frontend
```

The browser tests use invented fixture text and mock HTTP records. Python tests
exercise actual HTTP image delivery, filesystem containment and request-time
freshness. No real private document is embedded in the dashboard or its tests.
The dashboard version remains `0.1.5+homalg.1`; the frontend toolchain requires
Node 22.12 or newer. Vite, its React plugin and KaTeX are updated to patched,
locked versions; the KaTeX override ensures the Markdown renderer uses the same
patched version as direct imports.
