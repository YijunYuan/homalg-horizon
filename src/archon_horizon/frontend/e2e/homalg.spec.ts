import { test, expect, type Page } from '@playwright/test';
const node = (id: string) => ({
  id,
  title: `Result ${id}`,
  type: 'theorem',
  chapter: id.split('.')[0],
  lean_status: 'lean_ok',
  proved: true,
  statement: 'For every $x$, $x=x$.',
  sources: [`${id.split('.')[0]}:page-0001, ${id.split('.')[0]}:page-0002`],
  homalg: {
    headline: 'in-progress',
    status: 'proved-pending-deps',
    fidelity_status: 'faithful',
    approx: true,
    blocked_by: [],
  },
});
async function fixture(page: Page) {
  const nodes = [node('Fixture2000.Thm_1'), node('Fixture2001.Thm_1')];
  let complete = false;
  const task = (doc: string) => ({
    id: `${doc}.STMT`,
    title: `Work ${doc}`,
    status: 'queued',
    priority: 'normal',
    projects: ['HomAlg'],
    write_set: { files: [] },
    metadata: { homalg: { doc } },
    roadmap_refs: [],
  });
  const state = {
    workspace: 'HomAlg fixture',
    projects: ['HomAlg'],
    tasks: [task('Fixture2000'), task('Fixture2001')],
    roadmap: { items: [] },
    runs: [],
    local_inbox: [],
    github_inbox: [],
    events: [],
    reports: [],
    memory: '',
    harnesses: {},
    inbox_providers: { local: { enabled: true } },
  };
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const current = nodes.map((n, i) =>
      i === 0 && complete
        ? {
            ...n,
            homalg: {
              ...n.homalg,
              headline: 'proof-complete',
              status: 'proved',
              approx: false,
            },
          }
        : n,
    );
    let json: any = {};
    if (url.pathname === '/api/state') json = state;
    else if (url.pathname === '/api/blueprints')
      json = { HomAlg: { nodes: current, edges: [] } };
    else if (url.pathname === '/api/blueprint/dag')
      json = { nodes: current, edges: [] };
    else if (url.pathname === '/api/blueprint/chapters')
      json = {
        hasBlueprint: true,
        error: null,
        chapters: ['Fixture2000', 'Fixture2001'].map((doc) => ({
          slug: doc,
          title: doc,
          tex: `\\chapter{${doc}}\n\\begin{theorem}\\label{${doc}.Thm_1}\\leanok For every $x$, $x=x$.\\end{theorem}`,
        })),
      };
    else if (url.pathname === '/api/projects') json = { projects: [] };
    else if (url.pathname === '/api/reference-page')
      json = {
        slug: url.searchParams.get('slug'),
        page: url.searchParams.get('page'),
        text: '\\newcommand{\\C}{C}\nSynthetic <script>alert(1)</script> transcription.',
        status: {
          status: 'reviewed',
          reviews: [
            {
              status: 'satisfactory',
              by: 'synthetic-reviewer',
              at: '2026-10-28T00:00:00Z',
            },
          ],
        },
        stale: false,
        sha256: '0'.repeat(64),
      };
    else if (url.pathname === '/api/reference-page.png') {
      await route.fulfill({
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6ar8AAAAASUVORK5CYII=',
          'base64',
        ),
      });
      return;
    }
    await route.fulfill({ json });
  });
  return {
    complete: () => {
      complete = true;
    },
  };
}
test('document switch filters work and survives primary navigation', async ({
  page,
}) => {
  await fixture(page);
  await page.goto('/tasks');
  await expect(
    page.getByText('Work Fixture2000', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('Work Fixture2001', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('combobox', { name: 'Document', exact: true })
    .selectOption('Fixture2000');
  await expect(page).toHaveURL(/doc=Fixture2000/);
  await expect(page.getByText('Work Fixture2001', { exact: true })).toHaveCount(
    0,
  );
  await page.getByRole('link', { name: 'DAG', exact: true }).click();
  await expect(page).toHaveURL(/dag\?doc=Fixture2000/);
  await expect(
    page.getByRole('combobox', { name: 'Document', exact: true }),
  ).toHaveValue('Fixture2000');
});
test('authoritative overlay and private source viewer are available from the DAG', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const controls = await fixture(page);
  await page.goto(
    '/dag?project=HomAlg&focus=Fixture2000.Thm_1&doc=Fixture2000',
  );
  await expect(
    page.locator('.homalg-status').filter({ hasText: 'in progress' }),
  ).toBeVisible();
  await expect(page.locator('.homalg-badges')).toContainText('approximate');
  await expect(page.locator('.chapter-graph svg').first()).toBeVisible();
  controls.complete();
  await expect(
    page.locator('.homalg-status').filter({ hasText: 'proof complete' }),
  ).toBeVisible({ timeout: 10000 });
  await page
    .getByRole('link', { name: 'Fixture2000:page-0001', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Vision transcription' }),
  ).toBeVisible();
  await expect(page.locator('.homalg-reference pre')).toContainText(
    '<script>alert(1)</script>',
  );
  await expect(page.getByText('satisfactory', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('img', {
      name: 'Source scan of Fixture2000, PDF page 0001',
    }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
test('Blueprint reader shows overlay and source anchors in the selected document', async ({
  page,
}) => {
  await fixture(page);
  await page.goto(
    '/blueprint?project=HomAlg&focus=Fixture2000.Thm_1&doc=Fixture2000',
  );
  await expect(
    page
      .getByRole('link', { name: 'Fixture2000:page-0001', exact: true })
      .first(),
  ).toBeVisible();
  await expect(
    page.locator('.homalg-status').filter({ hasText: 'in progress' }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Fixture2001:page-0001', exact: true }),
  ).toHaveCount(0);
});
