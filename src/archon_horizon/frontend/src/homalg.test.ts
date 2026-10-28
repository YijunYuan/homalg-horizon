import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  documentOf,
  sourcePages,
  filterWorkspace,
  filterDag,
  nodeAppearance,
  currentDag,
  documentBlocks,
} from './homalg.ts';
const a = {
    id: 'Fixture2000.Thm_1',
    homalg: {
      headline: 'in-progress',
      status: 'proved-pending-deps',
      approx: true,
    },
  },
  b = { id: 'Fixture2001.Def_1' };
test('source anchors split comma lists, deduplicate and reject traversal', () => {
  assert.deepEqual(
    sourcePages([
      'Fixture2000:page-0001, Fixture2000:page-0002',
      'Fixture2000:page-0001',
      '../secret:page-0001',
      'Fixture2000:page-0000',
    ]),
    [
      { slug: 'Fixture2000', page: '0001' },
      { slug: 'Fixture2000', page: '0002' },
    ],
  );
  assert.equal(documentOf(a.id), 'Fixture2000');
  assert.equal(documentOf('other'), '');
});
test('document scope follows metadata, node scopes and run tasks without mutating input', () => {
  const state = {
    tasks: [
      { id: 'T1', metadata: { homalg: { doc: 'Fixture2000' } } },
      { id: 'T2', metadata: { homalg: { doc: 'Fixture2001' } } },
    ],
    roadmap: {
      items: [
        { id: 'R1', metadata: { homalg: { doc: 'Fixture2000' } } },
        { id: 'R2' },
      ],
    },
    local_inbox: [
      { id: 'I1', scope: { blueprint_nodes: [a.id] } },
      { id: 'I2', scope: { blueprint_nodes: [b.id] } },
    ],
    github_inbox: [],
    runs: [
      { id: '1', focus: { task: 'T1' } },
      { id: '2', focus: { tasks: ['T2'] } },
      { id: '3', sessions: [{ children: [{ meta: { task_id: 'T1' } }] }] },
    ],
    blueprints: {
      HomAlg: { nodes: [a, b], edges: [{ source: a.id, target: b.id }] },
    },
  };
  const result = filterWorkspace(state, 'Fixture2000');
  assert.deepEqual(
    result.tasks.map((n: any) => n.id),
    ['T1'],
  );
  assert.deepEqual(
    result.roadmap.items.map((n: any) => n.id),
    ['R1'],
  );
  assert.deepEqual(
    result.local_inbox.map((n: any) => n.id),
    ['I1'],
  );
  assert.deepEqual(
    result.runs.map((n: any) => n.id),
    ['1', '3'],
  );
  assert.equal(result.blueprints.HomAlg.edges.length, 0);
  assert.equal(state.blueprints.HomAlg.nodes.length, 2);
  assert.equal(filterWorkspace(state, ''), state);
});
test('overlay status wins over upstream completion and updates heavy DAG immediately', () => {
  assert.equal(
    nodeAppearance({ ...a, lean_status: 'lean_ok' })?.fill,
    '#ffffff',
  );
  const latest = currentDag(
    { nodes: [{ ...a, statement: 'heavy' }] },
    {
      nodes: [
        { id: a.id, homalg: { headline: 'proof-complete', status: 'proved' } },
      ],
    },
  );
  assert.equal(latest.nodes[0].statement, 'heavy');
  assert.equal(nodeAppearance(latest.nodes[0])?.fill, '#247347');
  assert.equal(
    nodeAppearance(currentDag(latest, { nodes: [{ id: a.id }] }).nodes[0]),
    null,
  );
  assert.equal(
    nodeAppearance({ ...a, homalg: { headline: 'error', status: 'disputed' } })
      ?.fill,
    '#ffe0dc',
  );
  assert.equal(
    nodeAppearance({
      ...a,
      homalg: { headline: 'in-progress', disputed: true },
    })?.border,
    '#8052a3',
  );
  assert.equal(
    nodeAppearance({ ...a, homalg: { aux_kind: 'bridge' } })?.shape,
    'diamond',
  );
  assert.equal(
    nodeAppearance({ id: 'Fixture2000.Cited_x', homalg: {} })?.shape,
    'hexagon',
  );
});
test('document graph and reader exclude other documents', () => {
  assert.deepEqual(
    filterDag({ nodes: [a, b], edges: [] }, 'Fixture2001').nodes,
    [b],
  );
  const blocks = [
    { meta: { label: a.id }, body: [{ t: 'text', text: 'A' }] },
    { meta: { label: b.id }, body: [] },
  ];
  assert.deepEqual(documentBlocks(blocks, 'Fixture2000'), [blocks[0]]);
});
