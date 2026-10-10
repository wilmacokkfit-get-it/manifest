const { test: base, expect } = require('@playwright/test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const venture = {
  slug: 'browser-fixture', name: 'Synthetic venture', tagline: 'A synthetic draft',
  description: 'Test only; never persisted.', idea_raw: 'A synthetic test idea',
  category: 'Test', is_owner: true, visibility: 'private', images: [],
  images_status: 'failed', refinement: { state: 'available' },
  provenance: { code: 'TEST', hash: 'test-only', minted_at: '2026-01-01T00:00:00Z' },
  tiers: [0, 1, 2].map(i => ({ name: `Tier ${i}`, cost: '$10', description: 'Test tier' })),
  roadmap: [], grounding: {}, crossbreeds: [], needs: [],
};

const test = base.extend({
  harness: async ({ page }, use) => {
    const errors = [], unexpected = [];
    let holdRecovery = false;
    const recoveryRequests = [];
    page.on('pageerror', e => errors.push(e.message));
    // No request is allowed through to a backend or third-party service.
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://manifest.test') return route.fulfill({ body: '' });
      if (url.pathname.startsWith('/api/')) {
        let body;
        if (url.pathname === '/api/ventures/browser-fixture') body = venture;
        else if (url.pathname.startsWith('/api/manifest/requests/')) {
          if (holdRecovery) { recoveryRequests.push(route); return; }
          body = { state: 'failed' };
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
    await page.addInitScript(() => {
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
    await page.addInitScript(() => {
      // Playwright stubs measure/getEntries while faking performance.now.
      // Keep Chromium's real User Timing buffer, with fake numeric start/end.
      performance.measure = window.__nativePerf.measure;
      performance.getEntriesByType = window.__nativePerf.getEntriesByType;
      const nativeFetch = window.fetch.bind(window);
      const intervals = new Set(), deadlines = new Set();
      const setI = window.setInterval.bind(window), clearI = window.clearInterval.bind(window);
      const setT = window.setTimeout.bind(window), clearT = window.clearTimeout.bind(window);
      window.setInterval = (fn, ms, ...args) => {
        const id = setI(fn, ms, ...args);
        if (ms === 1000) intervals.add(id);
        return id;
      };
      window.clearInterval = id => { intervals.delete(id); clearI(id); };
      window.setTimeout = (fn, ms, ...args) => {
        const id = setT(() => { deadlines.delete(id); fn(...args); }, ms);
        if (ms === 210000 || ms === 240000) deadlines.add(id);
        return id;
      };
      window.clearTimeout = id => { deadlines.delete(id); clearT(id); };
      window.__synthetic = {
        requests: [],
        recoveryBodies: 0,
        timers: () => ({ intervals: intervals.size, deadlines: deadlines.size }),
      };
      window.fetch = (url, options = {}) => {
        const path = new URL(url, location.href).pathname;
        if (options.method !== 'POST' || !(
          path === '/api/manifest' || path.endsWith('/refine') || path.endsWith('/path')
        )) return nativeFetch(url, options).then(response => {
          if (path.startsWith('/api/manifest/requests/')) {
            const json = response.json.bind(response);
            response.json = async () => {
              const data = await json();
              window.__synthetic.recoveryBodies++;
              return data;
            };
          }
          return response;
        });
        const request = { path, body: JSON.parse(options.body), aborted: false };
        window.__synthetic.requests.push(request);
        if (path.endsWith('/refine')) {
          return new Promise((resolve, reject) => {
            let controller;
            const body = new ReadableStream({ start(c) { controller = c; } });
            request.headers = () => resolve(new Response(body, {
              headers: { 'Content-Type': 'application/json' },
            }));
            request.chunk = text => controller.enqueue(new TextEncoder().encode(text));
            request.json = (value, status = 200) => resolve(new Response(JSON.stringify(value), {
              status, headers: { 'Content-Type': 'application/json' },
            }));
            options.signal.addEventListener('abort', () => {
              request.aborted = true;
              controller.error(new DOMException('Aborted', 'AbortError'));
              reject(new DOMException('Aborted', 'AbortError'));
            }, { once: true });
          });
        }
        let streamController;
        let ended = false;
        const stream = new ReadableStream({
          start(controller) { streamController = controller; },
          cancel() { ended = true; },
        });
        const encoder = new TextEncoder();
        request.send = (event, data) => streamController.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        request.close = () => { if (!ended) { ended = true; streamController.close(); } };
        request.json = value => {
          streamController.enqueue(encoder.encode(JSON.stringify(value)));
          request.close();
        };
        options.signal.addEventListener('abort', () => {
          request.aborted = true;
          if (!ended) { ended = true; streamController.error(new DOMException('Aborted', 'AbortError')); }
        }, { once: true });
        return Promise.resolve(new Response(stream, {
          headers: { 'Content-Type': path.endsWith('/refine') ? 'application/json' : 'text/event-stream' },
        }));
      };
    });
    await page.goto('https://manifest.test/');
    await use({
      venture,
      holdRecovery() { holdRecovery = true; },
      recoveryRequests: () => recoveryRequests.length,
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
