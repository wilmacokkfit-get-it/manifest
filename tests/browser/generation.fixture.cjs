const { test: base, expect } = require('@playwright/test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { venture, installSyntheticRuntime } = require('./synthetic-runtime.cjs');

const test = base.extend({
  harness: async ({ page, context }, use) => {
    const errors = [], unexpected = [];
    let holdRecovery = false;
    const recoveryRequests = [];
    let recoveryResult = { state: 'failed' };
    let recoveryLookups = 0;
    let retirementResult = { state: 'retired' }, loseRetirement = false;
    const manifestRequests = [], generations = [], held = [], holds = [];
    page.on('pageerror', e => errors.push(e.message));
    context.on('page', tab => tab.on('pageerror', e => errors.push(e.message)));
    await context.exposeBinding('__recordSyntheticGeneration', ({ page: tab }, request) => {
      generations.push({ page: tab, ...request });
    });
    // No request is allowed through to a backend or third-party service.
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://manifest.test') return route.fulfill({ body: '' });
      if (url.pathname.startsWith('/api/')) {
        let body;
        if (url.pathname === '/api/ventures/browser-fixture') body = venture;
        else if (url.pathname.startsWith('/api/manifest/requests/')) {
          const request = route.request();
          const tab = request.frame().page();
          const retirement = url.pathname.endsWith('/retire');
          if (request.method() !== (retirement ? 'POST' : 'GET')) {
            unexpected.push(`${request.method()} ${url.pathname}`);
            return route.abort();
          }
          manifestRequests.push({ page: tab, method: request.method(), path: url.pathname });
          const hold = holds.findIndex(item => item.page === tab && item.retirement === retirement);
          if (hold !== -1) {
            holds.splice(hold, 1);
            held.push({ page: tab, retirement, route });
            return;
          }
          if (retirement) {
            if (loseRetirement) return route.abort('failed');
            return route.fulfill({ json: retirementResult });
          }
          recoveryLookups++;
          if (holdRecovery) { recoveryRequests.push(route); return; }
          body = recoveryResult;
        }
        else if (url.pathname === '/api/owner-auth/config') {
          return route.fulfill({ status: 503, json: { error: 'Authentication disabled in isolated tests' } });
        }
        else if (url.pathname === '/api/owner-auth/status') body = { authenticated: false };
        else {
          unexpected.push(`${route.request().method()} ${url.pathname}`);
          return route.fulfill({ status: 500, json: { error: 'Unmocked API request' } });
        }
        return route.fulfill({ json: body });
      }
      const file = url.pathname === '/' || url.pathname.startsWith('/v/') ? 'index.html' : url.pathname.slice(1);
      if (!['index.html', 'app.js', 'owner-auth.js', 'styles.css', 'favicon.svg'].includes(file)) {
        unexpected.push(url.pathname);
        return route.abort();
      }
      let body = readFileSync(join(__dirname, '../../public', file), 'utf8');
      if (file === 'index.html') body = body.replace('<!--BASE-->', '<base href="/"><script>window.__MANIFEST_SERVED__=true</script>');
      return route.fulfill({ body, contentType: {
        'index.html': 'text/html', 'app.js': 'application/javascript',
        'owner-auth.js': 'application/javascript', 'styles.css': 'text/css', 'favicon.svg': 'image/svg+xml',
      }[file] });
    });
    // Clock initialization is context-scoped. Keep these scripts in that same
    // scope: Firefox runs context scripts before page scripts.
    await context.addInitScript(() => {
      window.__nativePerf = {
        measure: performance.measure.bind(performance),
        getEntriesByType: performance.getEntriesByType.bind(performance),
      };
    });
    await page.clock.install({ time: new Date('2026-01-01T00:00:00Z') });
    await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
    // Route.fulfill buffers a whole response. Intercept only generation fetches
    // in the browser instead, preserving real Response/ReadableStream parsing
    // and AbortSignal behavior, while allowing incremental SSE and stalled JSON.
    await context.addInitScript(() => {
      // Playwright stubs measure/getEntries while faking performance.now.
      // Keep the engine's real User Timing buffer, with fake numeric start/end.
      performance.measure = window.__nativePerf.measure;
      performance.getEntriesByType = window.__nativePerf.getEntriesByType;
    });
    await context.addInitScript(installSyntheticRuntime);
    await page.goto('https://manifest.test/');
    await use({
      venture,
      holdRecovery() { holdRecovery = true; },
      setRecoveryResult(result) { recoveryResult = result; },
      recoveryLookups: () => recoveryLookups,
      recoveryRequests: () => recoveryRequests.length,
      manifestRequests,
      generations,
      setRetirementResult(result) { retirementResult = result; },
      loseRetirementResponse() { loseRetirement = true; },
      holdNextLookup(tab) { holds.push({ page: tab, retirement: false }); },
      holdNextRetirement(tab) { holds.push({ page: tab, retirement: true }); },
      heldRequests: () => held.length,
      async releaseHeld(tab, data, retirement = false) {
        const index = held.findIndex(item => item.page === tab && item.retirement === retirement);
        if (index === -1) throw new Error('No matching held manifest response');
        const [{ route }] = held.splice(index, 1);
        await route.fulfill({ json: data });
      },
      async resolveRecovery(data) {
        await recoveryRequests.at(-1).fulfill({ json: data });
        await expect.poll(() => page.evaluate(() => window.__synthetic.recoveryBodies)).toBe(1);
      },
      async start(operation) {
        if (operation === 'venture') {
          await page.locator('#idea').fill('A synthetic test idea');
          await page.getByRole('button', { name: 'Manifest it' }).click();
        } else {
          await page.goto('https://manifest.test/v/browser-fixture');
          await expect(page.locator('.v-name')).toHaveText(venture.name);
          if (operation === 'refinement') {
            await page.locator('#refinement-context').fill('Make it compact');
            await page.getByRole('button', { name: 'Refine once' }).click();
          } else await page.getByRole('button', { name: 'Walk this path' }).first().click();
        }
        await expect.poll(() => page.evaluate(() => window.__synthetic.requests.length)).toBe(1);
      },
      async send(event, data) {
        await page.evaluate(({ event, data }) => window.__synthetic.requests.at(-1).send(event, data), { event, data });
      },
      async close() { await page.evaluate(() => window.__synthetic.requests.at(-1).close()); },
      async json(data, status = 200) {
        await page.evaluate(({ data, status }) => window.__synthetic.requests.at(-1).json(data, status), { data, status });
      },
      async openJsonBody() {
        await page.evaluate(() => {
          window.__synthetic.requests[0].headers();
          window.__synthetic.requests[0].chunk('{"name":');
        });
      },
      timers: () => page.evaluate(() => window.__synthetic.timers()),
      measures: operation => page.evaluate(operation => performance.getEntriesByType('measure')
        .filter(e => e.name.startsWith(`manifest.${operation}.`))
        .map(e => ({ name: e.name, duration: e.duration })), operation),
    });
    expect(errors, 'Browser JavaScript exceptions').toEqual([]);
    expect(unexpected, 'All API requests must be explicitly mocked').toEqual([]);
  },
});

module.exports = { test, expect };
