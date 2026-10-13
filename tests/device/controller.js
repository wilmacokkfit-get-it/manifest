(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const frame = $('app');
  const runs = [];
  let current = null;
  const app = () => frame.contentWindow;
  const doc = () => frame.contentDocument;
  const text = selector => [...doc().querySelectorAll(selector)].map(el => el.textContent.trim()).join(' | ');
  const button = label => [...doc().querySelectorAll('button')].find(el => el.textContent.includes(label));
  const report = () => ({
    schema: 'safari-waiting-observations-v1', status: 'UNVERIFIED_DEVICE_OBSERVATIONS',
    iosVersion: $('ios').value, model: $('model').value,
    physicalSafariConfirmed: $('physical').checked, userAgent: navigator.userAgent,
    secureContext: isSecureContext,
    boundary: 'Synthetic client lifecycle only; not a published-host or socket timeout experiment.',
    notes: $('notes').value, runs,
  });
  function snapshot(reason) {
    if (!current || !app().__synthetic) return;
    const w = app(), s = w.__synthetic;
    const sample = {
      reason, at: new Date().toISOString(), wallElapsedMs: Date.now() - current.started,
      performanceElapsedMs: w.performance.now() - current.performanceStarted,
      visibility: document.visibilityState, appVisibility: doc().visibilityState,
      route: w.location.pathname, timers: s.timers(),
      requests: s.requests.map(r => ({ path: r.path, aborted: r.aborted })),
      feedback: text('#forming-status, #refinement-status, .path-sub, .venture > [role=status], #hero-error'),
      result: text('.v-name'),
      controls: [...doc().querySelectorAll('button')].filter(el =>
        /Manifest it|Refine once|Walk this path/.test(el.textContent))
        .map(el => ({ text: el.textContent.trim(), disabled: el.disabled })),
      measures: w.performance.getEntriesByType('measure').filter(e => e.name.startsWith('manifest.'))
        .map(e => ({ name: e.name, duration: e.duration, native: e instanceof w.PerformanceMeasure })),
      errors: [...w.__device.errors], unexpectedRequests: [...w.__device.unexpected],
    };
    current.samples.push(sample);
    $('summary').textContent = JSON.stringify(sample, null, 2);
  }
  const settle = () => new Promise(resolve => setTimeout(resolve, 100));
  async function until(fn) {
    const deadline = Date.now() + 5000;
    while (!fn()) {
      if (Date.now() > deadline) throw new Error('Fixture action did not settle within five seconds.');
      await settle();
    }
  }
  function click(label) {
    const el = button(label);
    if (!el || el.disabled) throw new Error(`Usable ${label} control not found.`);
    el.click();
  }
  async function beginOperation(operation) {
    if (operation === 'venture') {
      const idea = doc().querySelector('#idea');
      idea.value = 'A synthetic test idea';
      idea.dispatchEvent(new (app().Event)('input', { bubbles: true }));
      click('Manifest it');
    } else {
      if (operation === 'refinement') {
        doc().querySelector('#refinement-context').value = 'Make it compact';
        click('Refine once');
      } else click('Walk this path');
    }
  }
  async function reset(operation) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture did not load.')), 5000);
      frame.onload = () => { clearTimeout(timer); resolve(); };
      frame.src = '/app.html';
    });
    if (!app().__device || !app().__synthetic || !app().crypto.randomUUID)
      throw new Error('Isolated runtime or secure crypto unavailable. Use HTTPS, not a LAN HTTP URL.');
    if (operation !== 'venture') {
      app().history.pushState(null, '', '/v/browser-fixture');
      app().dispatchEvent(new (app().PopStateEvent)('popstate'));
      await until(() => {
        const control = button(operation === 'refinement' ? 'Refine once' : 'Walk this path');
        return control && !control.disabled;
      });
    }
  }
  async function start() {
    if (!/^\d+\.\d+(?:\.\d+)?$/.test($('ios').value.trim()) || !$('model').value.trim())
      throw new Error('Record the exact iOS version and iPhone model first. Desktop smoke checks are not device evidence.');
    const operation = $('operation').value, scenario = $('scenario').value;
    if (operation === 'refinement' && /after$/.test(scenario))
      throw new Error('Refinement is JSON, not partial SSE. Select a before-text case.');
    if (operation !== 'refinement' && scenario === 'body-timeout')
      throw new Error('The stalled JSON body case is refinement only.');
    // Record the disposal of the previous case, never merge its requests.
    snapshot('before-fixture-disposal');
    await reset(operation);
    current = {
      operation, scenario, started: Date.now(), performanceStarted: app().performance.now(), samples: [],
    };
    runs.push(current);
    await beginOperation(operation);
    await until(() => app().__synthetic.requests.length === 1);
    if (/after$/.test(scenario)) {
      app().__synthetic.requests[0].send('partial', operation === 'venture'
        ? { data: { name: 'Synthetic venture', tagline: 'Partial text' }, streaming: 'tagline' }
        : { data: { first_move: 'Synthetic first step' }, streaming: 'first_move' });
      await settle();
    } else if (scenario === 'body-timeout') {
      const r = app().__synthetic.requests[0];
      r.headers(); r.chunk('{"name":');
    }
    snapshot('started');
  }
  async function complete() {
    const w = app(), r = w.__synthetic.requests.at(-1);
    if (!r || r.aborted) throw new Error('No live synthetic response. Use Retry and complete after timeout.');
    const result = current.operation === 'refinement'
      ? { ...w.__device.venture, name: 'Refined fixture', refinement: { state: 'completed' } }
      : current.operation === 'path'
        ? { ...w.__device.venture, path: { tier: 0, tier_label: 'Bootstrap',
          first_move: 'Synthetic first step', steps: [{ n: 1, title: 'Synthetic step', status: 'todo' }] } }
        : w.__device.venture;
    if (current.operation === 'refinement') r.json(result);
    else { r.send('done', result); r.close(); }
    await until(() => w.performance.getEntriesByType('measure').some(e =>
      e.name === `manifest.${current.operation}.completed`));
    snapshot('completed');
  }
  async function retry() {
    if (!current || !app().__synthetic.requests.at(-1)?.aborted)
      throw new Error('Wait for the watchdog abort before retrying.');
    const count = app().__synthetic.requests.length;
    await beginOperation(current.operation);
    await until(() => app().__synthetic.requests.length === count + 1);
    snapshot('retry-started');
    await complete();
  }
  async function navigate() {
    const el = [...doc().querySelectorAll('a[data-route="/about"]')].find(el => el.getClientRects().length);
    if (!el) throw new Error('About navigation is not available.');
    el.click(); await settle(); snapshot('navigated');
  }
  const guard = fn => async () => {
    $('error').textContent = '';
    try { await fn(); } catch (error) { $('error').textContent = error.message; }
  };
  $('start').onclick = guard(start);
  $('complete').onclick = guard(complete);
  $('retry').onclick = guard(retry);
  $('navigate').onclick = guard(navigate);
  $('snapshot').onclick = guard(() => snapshot('manual'));
  $('export').onclick = guard(() => {
    snapshot('export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(report(), null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = 'safari-waiting-observations.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  });
  document.addEventListener('visibilitychange', () => {
    snapshot(`visibility-${document.visibilityState}`);
    if (document.visibilityState === 'visible') setTimeout(() => snapshot('foreground-settled'), 2000);
  });
  window.addEventListener('pagehide', event => snapshot(`pagehide-persisted-${event.persisted}`));
  window.addEventListener('pageshow', event => snapshot(`pageshow-persisted-${event.persisted}`));
})();
