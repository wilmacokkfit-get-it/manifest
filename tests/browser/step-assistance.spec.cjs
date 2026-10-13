const { test, expect } = require('@playwright/test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

function serveAsset(route, pathname) {
  const file = pathname === '/' || pathname.startsWith('/v/') ? 'index.html' : pathname.slice(1);
  let body = readFileSync(join(__dirname, '../../public', file), 'utf8');
  if (file === 'index.html') body = body.replace('<!--BASE-->', '<base href="/"><script>window.__MANIFEST_SERVED__=true</script>');
  const contentType = file.endsWith('.html') ? 'text/html' : file.endsWith('.js') ? 'application/javascript'
    : file.endsWith('.css') ? 'text/css' : 'image/svg+xml';
  return route.fulfill({ contentType, body });
}

const baseVenture = () => ({
  slug: 'step-fixture', name: 'A quiet mechanism', tagline: 'A test fixture',
  description: 'Browser regression fixture only.', idea_raw: 'Fixture idea',
  category: 'Test', is_owner: true, visibility: 'private', images: [],
  images_status: 'failed', refinement: { state: 'available' },
  provenance: { code: 'TEST', hash: 'fixture', minted_at: '2026-01-01T00:00:00Z' },
  tiers: [], roadmap: [], grounding: {}, crossbreeds: [], needs: [],
  path: {
    tier: 0, tier_label: 'The Lean Build', total: 1, done: 0,
    steps: [{ n: 1, title: 'Map a first version', status: 'todo', why: 'Keep it small.', how: ['Write a brief.'], cost: '$20', time: 'One afternoon', done_when: 'A brief exists.', suppliers: [], note: '' }],
  },
});

test('step assistance requires explicit approval, preserves decline, and renders prepared text safely', async ({ page }) => {
  let venture = baseVenture();
  const requests = [];
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://manifest.test') return route.fulfill({ status: 200, body: '' });
    if (!url.pathname.startsWith('/api/')) {
      return serveAsset(route, url.pathname);
    }
    if (url.pathname === '/api/owner-auth/config') return route.fulfill({ status: 503, json: { error: 'Fixture' } });
    if (url.pathname === '/api/owner-auth/status') return route.fulfill({ json: { authenticated: false } });
    if (url.pathname === '/api/ventures/step-fixture' && route.request().method() === 'GET')
      return route.fulfill({ json: venture });
    if (url.pathname.endsWith('/path/steps/1/assist') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      requests.push(body);
      if (!body.approve) {
        venture.path.steps[0].automation = { state: 'declined' };
        return route.fulfill({ json: venture });
      }
      venture.path.steps[0].automation = {
        state: 'ready',
        result: {
          summary: 'A draft brief for human review.',
          deliverables: [{ title: 'Starter brief', content: 'Draft text <img src=x onerror=alert(1)>; do not send.' }],
          human_handoff: ['A person must verify the assumptions.'],
          limitations: ['No suppliers or safety claims were checked.'],
        },
      };
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          `event: meta\ndata: ${JSON.stringify({ step_n: 1 })}\n\n`,
          `event: status\ndata: ${JSON.stringify({ message: 'Preparing a draft' })}\n\n`,
          `event: done\ndata: ${JSON.stringify(venture)}\n\n`,
        ].join(''),
      });
    }
    return route.fulfill({ status: 500, json: { error: `Unexpected ${route.request().method()} ${url.pathname}` } });
  });

  await page.goto('https://manifest.test/v/step-fixture');
  const step = page.locator('.pstep');
  await expect(step.getByRole('button', { name: 'Yes — prepare drafts' })).toBeVisible();
  await expect(step.getByRole('button', { name: 'No — decline AI help' })).toBeVisible();
  await step.getByRole('button', { name: 'No — decline AI help' }).click();
  await expect(step.locator('.step-assist-state')).toHaveText('declined');
  expect(requests).toEqual([{ approve: false }]);
  expect(venture.path.steps[0].status).toBe('todo');
});

test('approved step consumes the stream and exposes draft copy/download without interpreting markup', async ({ page }) => {
  let venture = baseVenture();
  let requestBody;
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://manifest.test') return route.fulfill({ status: 200, body: '' });
    if (!url.pathname.startsWith('/api/')) {
      return serveAsset(route, url.pathname);
    }
    if (url.pathname === '/api/owner-auth/config') return route.fulfill({ status: 503, json: { error: 'Fixture' } });
    if (url.pathname === '/api/owner-auth/status') return route.fulfill({ json: { authenticated: false } });
    if (url.pathname === '/api/ventures/step-fixture' && route.request().method() === 'GET')
      return route.fulfill({ json: venture });
    if (url.pathname.endsWith('/path/steps/1/assist') && route.request().method() === 'POST') {
      requestBody = route.request().postDataJSON();
      venture.path.steps[0].automation = {
        state: 'ready',
        result: {
          summary: 'A draft brief for human review.',
          deliverables: [{ title: 'Starter brief', content: 'Draft text <img src=x onerror=alert(1)>; do not send.' }],
          human_handoff: ['A person must verify the assumptions.'],
          limitations: ['No suppliers or safety claims were checked.'],
        },
      };
      return route.fulfill({
        status: 200, contentType: 'text/event-stream',
        body: [
          `event: meta\ndata: ${JSON.stringify({ step_n: 1 })}\n\n`,
          `event: status\ndata: ${JSON.stringify({ message: 'Preparing a draft' })}\n\n`,
          `event: done\ndata: ${JSON.stringify(venture)}\n\n`,
        ].join(''),
      });
    }
    return route.fulfill({ status: 500, json: { error: `Unexpected ${route.request().method()} ${url.pathname}` } });
  });
  await page.goto('https://manifest.test/v/step-fixture');
  const step = page.locator('.pstep');
  await step.getByRole('button', { name: 'Yes — prepare drafts' }).click();
  await expect(step.locator('.step-assist-state')).toHaveText('drafts ready');
  expect(requestBody).toEqual({ approve: true });
  const draft = step.locator('.step-deliverable-content');
  await expect(draft).toContainText('<img src=x onerror=alert(1)>');
  expect(await draft.locator('img').count()).toBe(0);
  await expect(step).toContainText('A person must verify the assumptions.');
  const download = page.waitForEvent('download');
  await step.getByRole('button', { name: 'Download' }).click();
  expect((await download).suggestedFilename()).toBe('step-1-draft-1.txt');
});
