/** Pure HomAlgDB presentation and document-scope rules. */
export const documentOf = (id: unknown) =>
  /^([A-Z][A-Za-z]*[0-9]{4}[A-Za-z0-9]*)\./.exec(String(id || ''))?.[1] || '';
export const overlayOf = (node: any) =>
  node?.homalg && typeof node.homalg === 'object' ? node.homalg : null;
export function sourcePages(
  sources: unknown,
): { slug: string; page: string }[] {
  const values = Array.isArray(sources) ? sources : [sources];
  const found = new Map<string, { slug: string; page: string }>();
  for (const value of values)
    for (const part of String(value || '').split(',')) {
      const match = /^([A-Za-z0-9][A-Za-z0-9_-]*):page-([0-9]{4})$/.exec(
        part.trim(),
      );
      if (match && match[2] !== '0000')
        found.set(`${match[1]}:${match[2]}`, {
          slug: match[1],
          page: match[2],
        });
    }
  return [...found.values()];
}
export function filterDag(dag: any, doc: string) {
  if (!doc || !dag) return dag;
  const nodes = (dag.nodes || []).filter(
    (node: any) => documentOf(node.id) === doc,
  );
  const ids = new Set(nodes.map((node: any) => String(node.id)));
  return {
    ...dag,
    nodes,
    edges: (dag.edges || []).filter(
      (edge: any) =>
        ids.has(String(edge.source)) && ids.has(String(edge.target)),
    ),
  };
}
export function documentsIn(state: any): string[] {
  const docs = new Set<string>();
  for (const dag of Object.values(state.blueprints || {}) as any[])
    for (const node of dag.nodes || [])
      if (documentOf(node.id)) docs.add(documentOf(node.id));
  for (const item of [...(state.tasks || []), ...(state.roadmap?.items || [])])
    if (item.metadata?.homalg?.doc) docs.add(item.metadata.homalg.doc);
  for (const item of [
    ...(state.local_inbox || []),
    ...(state.github_inbox || []),
  ])
    for (const id of item.scope?.blueprint_nodes || [])
      if (documentOf(id)) docs.add(documentOf(id));
  return [...docs].sort();
}
export function filterWorkspace(state: any, doc: string) {
  if (!doc) return state;
  const tasks = (state.tasks || []).filter(
    (task: any) => task.metadata?.homalg?.doc === doc,
  );
  const taskIds = new Set(tasks.map((task: any) => task.id));
  const inbox = (items: any[]) =>
    (items || []).filter((item) =>
      (item.scope?.blueprint_nodes || []).some(
        (id: string) => documentOf(id) === doc,
      ),
    );
  const sessionMatches = (session: any): boolean =>
    taskIds.has(
      session.task_id || session.meta?.task_id || session.meta?.task,
    ) || (session.children || []).some(sessionMatches);
  const runs = (state.runs || []).filter(
    (run: any) =>
      taskIds.has(run.focus?.task) ||
      (run.focus?.tasks || []).some((id: string) => taskIds.has(id)) ||
      (run.sessions || []).some(sessionMatches),
  );
  return {
    ...state,
    tasks,
    runs,
    roadmap: {
      ...state.roadmap,
      items: (state.roadmap?.items || []).filter(
        (item: any) => item.metadata?.homalg?.doc === doc,
      ),
    },
    local_inbox: inbox(state.local_inbox),
    github_inbox: inbox(state.github_inbox),
    blueprints: Object.fromEntries(
      Object.entries(state.blueprints || {}).map(([key, dag]) => [
        key,
        filterDag(dag, doc),
      ]),
    ),
  };
}
const GLYPHS: Record<string, string> = {
  planned: '○',
  stated: '◐',
  'proved-pending-deps': '◑',
  'proved-modulo-citations': '◕',
  proved: '●',
  'proved-with-axioms': '⚠',
  disputed: '✕',
  pruned: '⊘',
};
export function nodeAppearance(node: any) {
  const status = overlayOf(node);
  if (!status) return null;
  const fill =
    (
      {
        'in-progress': '#ffffff',
        'statement-complete': '#d6efdc',
        'proof-complete': '#247347',
        error: '#ffe0dc',
      } as Record<string, string>
    )[status.headline] || '#d5dcd7';
  const fidelity =
    status.fidelity_status || status.fidelity?.status || status.fidelity;
  const raised =
    (status.dispute_raised || status.disputed) && status.headline !== 'error';
  const border = raised
    ? '#8052a3'
    : status.status === 'proved-with-axioms'
      ? '#9b6800'
      : fidelity === 'deviates'
        ? '#bc5c00'
        : fidelity === 'foundation'
          ? '#3c4650'
          : status.headline === 'error'
            ? '#b02e22'
            : '#3f7850';
  return {
    fill,
    border,
    color: status.headline === 'proof-complete' ? '#ffffff' : '#183426',
    glyph: GLYPHS[status.status] || '○',
    dashed:
      raised || status.status === 'proved-modulo-citations' || status.cited,
    shape:
      status.aux_kind === 'bridge'
        ? 'diamond'
        : status.cited || String(node.id).split('.')[1]?.startsWith('Cited_')
          ? 'hexagon'
          : 'box',
    label: String(status.headline || status.status || 'in-progress').replace(
      /-/g,
      ' ',
    ),
  };
}
/** Keep heavy source text while replacing request-time status from the latest poll. */
export function currentDag(full: any, light: any) {
  if (!full) return light || {};
  const current = new Map(
    (light?.nodes || []).map((node: any) => [String(node.id), node]),
  );
  return {
    ...full,
    nodes: (full.nodes || []).map((node: any) =>
      current.has(String(node.id))
        ? {
            ...node,
            ...(current.get(String(node.id)) as object),
            homalg: (current.get(String(node.id)) as any).homalg,
          }
        : node,
    ),
  };
}
export function documentBlocks(blocks: any[], doc: string): any[] {
  if (!doc) return blocks;
  return blocks.flatMap((block) => {
    if (
      block.meta?.label &&
      documentOf(block.meta.label) &&
      documentOf(block.meta.label) !== doc
    )
      return [];
    return [
      {
        ...block,
        ...(Array.isArray(block.body)
          ? { body: documentBlocks(block.body, doc) }
          : {}),
        ...(Array.isArray(block.items)
          ? {
              items: block.items.map((items: any[]) =>
                documentBlocks(items, doc),
              ),
            }
          : {}),
      },
    ];
  });
}
