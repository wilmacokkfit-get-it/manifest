/* Real browser networking and real clocks. NEVER sends a generation POST.
 * Obtain --url from getDeploymentInfo(), not a guessed domain.
 * Outputs only synthetic diagnostic timings to ignored generated-artifacts.
 */
const { chromium } = require('@playwright/test');
const { readFileSync, mkdirSync, writeFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { spawnSync } = require('node:child_process');
const { randomUUID } = require('node:crypto');

function options(argv) {
  const values = {};
  for (let i = 0; i < argv.length; i += 2) values[argv[i]] = argv[i + 1];
  const url = new URL(values['--url']);
  const localTest = values['--local-test'] === 'true';
  if (localTest && (url.hostname !== '127.0.0.1' || url.protocol !== 'http:')) {
    throw Error('--local-test is restricted to http://127.0.0.1.');
  }
  if ((!localTest && url.protocol !== 'https:') || url.username || url.password || url.search || url.hash) {
    throw Error('--url must be a clean, verified HTTPS published origin.');
  }
  const delay = Number(values['--delay'] || 185);
  if (!Number.isInteger(delay) || delay < 1 || delay > 310) throw Error('--delay must be 1–310 seconds.');
  const phase = values['--phase'] || 'all';
  if (!['all', 'raw', 'ui'].includes(phase)) throw Error('--phase must be raw, ui, or all.');
  return { origin: url.origin, delay, phase, localTest };
}

async function main() {
  const config = options(process.argv.slice(2));
  const directory = resolve(__dirname, '../generated-artifacts/published-waits');
  mkdirSync(directory, { recursive: true });
  const output = `${directory}/${Date.now()}.json`;
  const report = { started: new Date().toISOString(), ...config,
    note: config.localTest ? 'LOCAL FIXTURE ONLY: no published-host evidence.' :
      'Workspace UI, published probe network, real clocks. No providers or durable app records.',
    results: [] };
  const save = result => {
    report.results.push(result);
    writeFileSync(output, JSON.stringify(report, null, 2));
    console.log(JSON.stringify(result));
  };
  const browser = await chromium.launch({ executablePath:
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ||
    spawnSync('which', ['chromium'], { encoding: 'utf8' }).stdout.trim() || undefined });
  const context = await browser.newContext({ serviceWorkers: 'block' });
  const blocked = [];
  // Fail closed: only the synthetic endpoint may reach any server.
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin === config.origin && url.pathname === '/api/diagnostics/wait-probe' && request.method() === 'GET') {
      return route.continue();
    }
    if (url.origin !== config.origin) return route.abort();
    if (['/api/owner-auth/status', '/api/owner-auth/session'].includes(url.pathname)) {
      return route.fulfill({ json: { authenticated: false, is_owner: false } });
    }
    if (url.pathname === '/api/owner-auth/config') return route.fulfill({ status: 503, json: { error: 'Isolated diagnostic browser' } });
    if (/^\/api\/manifest\/requests\/[a-z0-9-]+$/.test(url.pathname)) {
      return route.fulfill({ json: { state: 'failed', message: 'Synthetic only; nothing was saved.' } });
    }
    if (url.pathname.startsWith('/api/')) {
      blocked.push(`${request.method()} ${url.pathname}`);
      return route.abort();
    }
    const file = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
    const types = { 'index.html': 'text/html', 'app.js': 'application/javascript',
      'owner-auth.js': 'application/javascript', 'styles.css': 'text/css', 'favicon.svg': 'image/svg+xml' };
    if (!types[file]) return route.abort();
    let body = readFileSync(resolve(__dirname, '../public', file), 'utf8');
    if (file === 'index.html') body = body.replace('<!--BASE-->', '<base href="/"><script>window.__MANIFEST_SERVED__=true</script>');
    return route.fulfill({ body, contentType: types[file] });
  });
  const page = await context.newPage();
  try {
    await page.goto(config.origin);
    // Actual upstream check before spending minutes waiting. No fake response
    // is accepted as evidence that the deployment contains the probe.
    const preflight = await page.evaluate(async () => {
      const response = await fetch('/api/diagnostics/wait-probe?scenario=early-eof&delay=1');
      await response.text();
      return { status: response.status, version: response.headers.get('x-manifest-wait-probe') };
    });
    if (preflight.status !== 200 || preflight.version !== 'wait-probe-1') {
      save({ kind: 'preflight', ...preflight, outcome: 'unavailable' });
      throw Error(`Published probe unavailable (${preflight.status}). Enable the short test window and republish first.`);
    }
    save({ kind: 'preflight', ...preflight });
    if (config.phase !== 'ui') {
      const cases = ['first-byte', 'json', 'partial-gap', 'heartbeat', 'early-eof'].map(scenario =>
        ({ scenario, delay: config.delay, abortAfter: null }));
      for (const scenario of ['first-byte', 'partial-gap', 'heartbeat']) {
        cases.push({ scenario, delay: config.delay, abortAfter: Math.min(3000, config.delay * 500) });
      }
      for (const input of cases) {
        const probeId = randomUUID();
        save(await page.evaluate(async ({ input, probeId }) => {
          const started = performance.now(), controller = new AbortController();
          const result = { kind: 'raw', probeId, ...input, chunks: [] };
          const timeout = setTimeout(() => controller.abort(), input.abortAfter || 335000);
          try {
            const response = await fetch(`/api/diagnostics/wait-probe?scenario=${input.scenario}&delay=${input.delay}&probe_id=${probeId}`,
              { signal: controller.signal });
            result.headersMs = performance.now() - started;
            result.status = response.status;
            result.version = response.headers.get('x-manifest-wait-probe');
            const reader = response.body.getReader(), decoder = new TextDecoder();
            let text = '';
            while (true) {
              const chunk = await reader.read();
              if (chunk.done) break;
              const value = decoder.decode(chunk.value, { stream: true });
              text += value;
              result.chunks.push({ ms: performance.now() - started, bytes: chunk.value.byteLength,
                heartbeat: value.includes(': still-working') });
            }
            result.outcome = input.scenario === 'early-eof' ? 'early-eof' :
              text.includes('"synthetic":true') || text.includes('"synthetic": true') ? 'completed' : 'unexpected-body';
          } catch (error) {
            result.outcome = error.name === 'AbortError' ? (input.abortAfter ? 'client-abort' : 'probe-deadline') : 'network-error';
            result.error = error.message;
          } finally { clearTimeout(timeout); }
          result.elapsedMs = performance.now() - started;
          return result;
        }, { input, probeId }));
      }
    }
    if (config.phase !== 'raw') {
      for (const scenario of ['first-byte', 'partial-gap', 'early-eof', 'heartbeat']) {
        const probeId = randomUUID();
        await page.goto(config.origin);
        await page.evaluate(({ scenario, delay, probeId }) => {
          const original = window.fetch.bind(window);
          window.__probeSubmissions = 0;
          window.fetch = async (url, options) => {
            if (new URL(url, location.origin).pathname !== '/api/manifest') return original(url, options);
            window.__probeSubmissions++;
            const response = await original(`/api/diagnostics/wait-probe?scenario=${scenario}&delay=${delay}&probe_id=${probeId}`,
              { signal: options.signal, credentials: 'omit' });
            if (!response.ok || response.headers.get('x-manifest-wait-probe') !== 'wait-probe-1') {
              throw Error(`Diagnostic endpoint unavailable (${response.status})`);
            }
            // Deliver real incremental upstream bytes. The only adaptation is
            // treating synthetic completion as an error: NEVER construct/save
            // a venture or call a real recovery endpoint.
            const decoder = new TextDecoder();
            let buffer = '';
            const transform = new TransformStream({
              transform(chunk, controller) {
                buffer += decoder.decode(chunk, { stream: true });
                let end;
                while ((end = buffer.indexOf('\n\n')) >= 0) {
                  let frame = buffer.slice(0, end + 2);
                  buffer = buffer.slice(end + 2);
                  if (frame.startsWith('event: done')) frame =
                    'event: error\ndata: {"message":"Synthetic proxy probe finished. No venture was created."}\n\n';
                  controller.enqueue(new TextEncoder().encode(frame));
                }
              },
            });
            return new Response(response.body.pipeThrough(transform), { headers: { 'content-type': 'text/event-stream' } });
          };
        }, { scenario, delay: config.delay, probeId });
        const started = Date.now(), samples = [];
        await page.locator('#idea').fill('Synthetic hosting probe; never sent to generation.');
        await page.getByRole('button', { name: 'Manifest it' }).click();
        let complete = false;
        while (Date.now() - started < 235000) {
          await page.waitForTimeout(1000);
          const snapshot = await page.evaluate(() => ({
            feedback: document.querySelector('.venture > [role=status]')?.textContent ||
              document.querySelector('#forming-status')?.textContent,
            error: document.querySelector('#hero-error')?.textContent,
            retryEnabled: [...document.querySelectorAll('button')].some(b => b.textContent.includes('Manifest it') && !b.disabled),
            measures: performance.getEntriesByType('measure').filter(m => m.name.startsWith('manifest.venture.')).map(m => ({
              name: m.name, duration: m.duration,
            })),
          }));
          samples.push({ ms: Date.now() - started, ...snapshot });
          if (snapshot.measures.some(m => m.name === 'manifest.venture.failed')) { complete = true; break; }
        }
        await page.waitForTimeout(10000); // Persistent feedback after transient toast expires.
        const final = await page.evaluate(() => ({
          error: document.querySelector('#hero-error')?.textContent,
          feedback: document.querySelector('.venture > [role=status]')?.textContent,
          submissions: window.__probeSubmissions,
          returnLink: Boolean(document.querySelector('.venture > [role=status] a[data-route="/"]')),
          retryEnabled: [...document.querySelectorAll('button')].some(b => b.textContent.toLowerCase().includes('manifest it') && !b.disabled),
        }));
        await page.screenshot({ path: `${directory}/${probeId}.jpg`, fullPage: true });
        save({ kind: 'creator-ui', scenario, delay: config.delay, probeId, complete, samples, final });
        if (!complete || final.submissions !== 1 || !(final.error || final.feedback)) {
          throw Error(`Creator feedback check failed for ${scenario}.`);
        }
        if (!final.returnLink && !final.retryEnabled) throw Error(`No recovery control for ${scenario}.`);
        if (config.delay >= 35 && scenario !== 'early-eof' &&
            !samples.some(s => s.feedback?.includes('Still working'))) {
          throw Error(`Missing elapsed waiting feedback for ${scenario}.`);
        }
        if (config.delay >= 5 && scenario === 'partial-gap' &&
            !samples.some(s => s.feedback?.includes('Writing'))) {
          throw Error('Missing feedback after partial text.');
        }
      }
    }
    if (blocked.length) throw Error(`Unexpected backend calls blocked: ${blocked.join(', ')}`);
    report.finished = new Date().toISOString();
  } finally {
    report.blocked = blocked;
    writeFileSync(output, JSON.stringify(report, null, 2));
    await browser.close();
    console.log(`Evidence: ${output}`);
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
