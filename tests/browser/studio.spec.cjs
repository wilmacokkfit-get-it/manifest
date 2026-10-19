/* Isolated browser fixture only. No production backend or AI calls are mocked
   by application code, and no request here reaches a real service. */
const { test, expect } = require('@playwright/test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { randomUUID } = require('node:crypto');

const appHtml = label => `<!DOCTYPE html><html><head><style>body{background:#10171b;color:#e8edeb;font:16px sans-serif;padding:24px}button{padding:12px}</style></head><body><h1>${label} (QA fixture only)</h1><button id="add">Increase</button><output id="count">0</output><script>let n=0;document.querySelector('#add').onclick=()=>document.querySelector('#count').textContent=++n;</script></body></html>`;
const matches = ['one', 'two', 'three'].map(name => ({ name: `Fixture ${name}`, summary: 'Synthetic search result for isolated browser QA.',
  url: `https://evidence.test/${name}`, source_url: `https://evidence.test/${name}` }));
const directions = ['a', 'b', 'c'].map(id => ({ id, title: `Direction ${id}`, pitch: 'A distinct fixture interaction.',
  difference: 'A fixture comparison, not actual research.' }));

async function harness(page, context) {
  const projects = new Map(), calls = [], errors = [];
  let fail = null, held = null, holdKind = null;
  page.on('pageerror', error => errors.push(error.message));
  const addVersion = (p, label, fixes = []) => {
    const versions = p.payload.versions.slice(0, p.payload.cursor + 1);
    versions.push({ id: randomUUID(), label, html: appHtml(label), fixes, created_at: new Date().toISOString() });
    p.payload.versions = versions.slice(-5);
    p.payload.cursor = p.payload.versions.length - 1;
  };
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.hostname === 'evidence.test') return route.fulfill({ contentType: 'text/html', body: '<h1>Isolated evidence fixture</h1>' });
    if (url.origin !== 'https://manifest.test') return route.fulfill({ body: '' });
    if (url.pathname.startsWith('/api/owner-auth/')) {
      return route.fulfill({ json: url.pathname.endsWith('/config') ? { enabled: false } : { authenticated: false } });
    }
    if (url.pathname.startsWith('/api/studio/')) {
      calls.push(req.method() + ' ' + url.pathname);
      const parts = url.pathname.split('/'), id = parts[4], kind = parts[5];
      if (!id) {
        if (req.method() === 'GET') return route.fulfill({ json: { projects: [...projects.values()].map(p => ({ id: p.id, idea: p.idea, updated_at: p.updated_at })) } });
        const body = req.postDataJSON();
        const p = { id: body.id, idea: body.idea, venture_slug: body.venture_slug, revision: 0,
          created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
          payload: { matches: [], directions: [], selected_id: null, versions: [], cursor: -1, autopsy: 'none', operation: null, error: null } };
        projects.set(p.id, p);
        return route.fulfill({ json: p });
      }
      const p = projects.get(id);
      if (!p) return route.fulfill({ status: 404, json: { error: 'Project not found.' } });
      if (req.method() === 'GET') return route.fulfill({ json: p });
      if (holdKind === kind) {
        held = route; return;
      }
      if (fail === kind) {
        p.payload.error = 'AI call failed. No template was substituted.';
        if (kind === 'autopsy') p.payload.autopsy = 'failed';
        fail = null;
        return route.fulfill({ status: 502, json: { error: p.payload.error } });
      }
      const body = req.postDataJSON();
      p.payload.error = null;
      if (kind === 'scout') { p.payload.matches = matches; p.payload.researched_at = new Date().toISOString(); }
      if (kind === 'diverge') p.payload.directions = directions;
      if (kind === 'build') { p.payload.selected_id = body.direction_id; p.payload.autopsy = 'pending'; addVersion(p, 'Initial build'); }
      if (kind === 'autopsy') { p.payload.autopsy = 'complete'; addVersion(p, 'Autopsy fixes', [{ category: 'state', issue: 'Missing state fixture', fixed: 'Added the missing state fixture.' }]); }
      if (kind === 'mutate') addVersion(p, body.change);
      if (kind === 'restore') p.payload.cursor = p.payload.versions.findIndex(v => v.id === body.version_id);
      p.revision++; p.updated_at = new Date().toISOString();
      return route.fulfill({ json: p });
    }
    const file = url.pathname === '/' || url.pathname.startsWith('/studio') && !/\.(js|css)$/.test(url.pathname)
      ? 'index.html' : url.pathname.slice(1);
    if (!['index.html', 'app.js', 'studio.js', 'studio.css', 'styles.css', 'owner-auth.js', 'favicon.svg'].includes(file)) return route.fulfill({ status: 404, body: '' });
    let body = readFileSync(join(__dirname, '../../public', file), 'utf8');
    if (file === 'index.html') body = body.replace('<!--BASE-->', '<base href="/"><script>window.__MANIFEST_SERVED__=true</script>');
    const contentType = file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({ body, contentType });
  });
  await page.goto('https://manifest.test/studio');
  await expect(page.getByTestId('input-studio-idea')).toBeVisible();
  return { projects, calls, errors, fail: kind => { fail = kind; }, hold: kind => { holdKind = kind; },
    async releaseHeld() { holdKind = null; await held.fulfill({ status: 502, json: { error: 'Interrupted fixture request.' } }); } };
}

async function start(page) {
  await page.getByTestId('input-studio-idea').fill('A deliberately distinctive browser timer for isolated tests');
  await page.getByTestId('button-scout').click();
  await expect(page.getByTestId('button-direction-c')).toBeVisible();
}
async function build(page) {
  await start(page);
  await page.getByTestId('button-direction-b').click();
  await expect(page.getByTestId('button-direction-b')).toHaveAttribute('aria-pressed', 'true');
  await page.getByTestId('button-build').click();
  await expect(page.getByTestId('list-studio-fixes')).toContainText('Added the missing state fixture.');
  return page.frameLocator('[data-testid="iframe-studio-preview"]');
}

test('complete workflow, sandbox, live preview interaction, mutation, undo, reload and library', async ({ page, context }) => {
  const qa = await harness(page, context);
  await expect(page.getByTestId('button-scout')).toBeDisabled();
  await page.getByTestId('input-studio-idea').fill('   ');
  await expect(page.getByTestId('button-scout')).toBeDisabled();
  const frame = await build(page);
  await expect(page.getByTestId('iframe-studio-preview')).toHaveAttribute('sandbox', 'allow-scripts');
  expect(await frame.locator('body').evaluate(() => {
    try { void parent.document.body; return false; } catch (_) { return true; }
  })).toBe(true);
  await frame.getByRole('button', { name: 'Increase' }).click();
  await expect(frame.locator('#count')).toHaveText('1');
  await page.getByTestId('input-studio-change').fill('Make the count amber');
  await page.getByTestId('button-mutate').click();
  await expect(frame.getByRole('heading')).toContainText('Make the count amber');
  await page.getByTestId('button-undo').click();
  await expect(frame.getByRole('heading')).toContainText('Autopsy fixes');
  const projectUrl = page.url();
  const count = qa.calls.filter(c => c.endsWith('/mutate')).length;
  await page.reload();
  await expect(frame.getByRole('heading')).toContainText('Autopsy fixes');
  expect(qa.calls.filter(c => c.endsWith('/mutate')).length).toBe(count);
  await page.getByTestId('button-projects').click();
  await expect(page.locator('.studio-project-list a')).toHaveCount(1);
  await page.getByTestId('link-new-project').click();
  await page.getByTestId('button-projects').click();
  await page.locator('.studio-project-list a').click();
  await expect(page).toHaveURL(projectUrl);
  await expect(frame.getByRole('heading')).toContainText('Autopsy fixes');
  expect(qa.errors).toEqual([]);
});

test('autopsy and mutation failures retain the app, show clear errors, and can retry', async ({ page, context }) => {
  const qa = await harness(page, context);
  qa.fail('autopsy');
  await start(page);
  await page.getByTestId('button-direction-a').click();
  await page.getByTestId('button-build').click();
  await expect(page.getByTestId('status-error')).toContainText('No template');
  const frame = page.frameLocator('[data-testid="iframe-studio-preview"]');
  await expect(frame.getByRole('heading')).toContainText('Initial build');
  await page.getByTestId('button-autopsy').click();
  await expect(page.getByTestId('list-studio-fixes')).toBeVisible();
  qa.fail('mutate');
  await page.getByTestId('input-studio-change').fill('A change that fails');
  await page.getByTestId('button-mutate').click();
  await expect(page.getByTestId('status-error')).toContainText('No template');
  await expect(frame.getByRole('heading')).toContainText('Autopsy fixes');
  await expect(page.getByTestId('input-studio-change')).toHaveValue('A change that fails');
  await page.getByTestId('button-mutate').click();
  await expect(frame.getByRole('heading')).toContainText('A change that fails');
  expect(qa.errors).toEqual([]);
});

test('last five versions, branch from earlier version, and no duplicate clicks', async ({ page, context }) => {
  const qa = await harness(page, context);
  const frame = await build(page);
  for (let index = 0; index < 5; index++) {
    await page.getByTestId('input-studio-change').fill(`Change ${index}`);
    await page.getByTestId('button-mutate').click();
    await expect(frame.getByRole('heading')).toContainText(`Change ${index}`);
  }
  await expect(page.locator('.studio-versions li')).toHaveCount(5);
  await page.getByTestId('button-version-1').click();
  await expect(frame.getByRole('heading')).toContainText('Change 1');
  await page.getByTestId('input-studio-change').fill('New branch');
  await page.getByTestId('button-mutate').click();
  await expect(frame.getByRole('heading')).toContainText('New branch');
  await expect(page.locator('.studio-versions li')).toHaveCount(3);
  qa.hold('mutate');
  await page.getByTestId('input-studio-change').fill('Held change');
  await page.getByTestId('button-mutate').click();
  await expect(page.getByTestId('button-mutate')).toBeDisabled();
  await expect(page.getByTestId('button-undo')).toBeDisabled();
  expect(qa.calls.filter(c => c.endsWith('/mutate'))).toHaveLength(7);
  await qa.releaseHeld();
  expect(qa.errors).toEqual([]);
});

test('all direction choices, linked search evidence and mobile-desktop layout', async ({ page, context }) => {
  const qa = await harness(page, context);
  await page.setViewportSize({ width: 375, height: 850 });
  await start(page);
  for (const id of ['a', 'b', 'c']) {
    await page.getByTestId(`button-direction-${id}`).click();
    await expect(page.getByTestId(`button-direction-${id}`)).toHaveAttribute('aria-pressed', 'true');
  }
  const popupEvent = context.waitForEvent('page');
  await page.getByTestId('link-evidence-0').click();
  const popup = await popupEvent;
  await expect(popup).toHaveURL('https://evidence.test/one');
  await popup.close();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.setViewportSize({ width: 1440, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.setViewportSize({ width: 375, height: 850 });
  await expect(page.getByTestId('button-build')).toBeVisible();
  expect(qa.errors).toEqual([]);
});
