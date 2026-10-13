// Shared by Playwright and the real-clock Safari device check. No provider code.
const venture = {
  slug: 'browser-fixture', name: 'Synthetic venture', tagline: 'A synthetic draft',
  description: 'Test only; never persisted.', idea_raw: 'A synthetic test idea',
  category: 'Test', is_owner: true, visibility: 'private', images: [],
  images_status: 'failed', refinement: { state: 'available' },
  provenance: { code: 'TEST', hash: 'test-only', minted_at: '2026-01-01T00:00:00Z' },
  tiers: [0, 1, 2].map(i => ({ name: `Tier ${i}`, cost: '$10', description: 'Test tier' })),
  roadmap: [], grounding: {}, crossbreeds: [], needs: [],
};

function installSyntheticRuntime() {
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
    requests: [], recoveryBodies: 0,
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
    window.__recordSyntheticGeneration({ path, body: request.body });
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
    let streamController, ended = false;
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
      headers: { 'Content-Type': 'text/event-stream' },
    }));
  };
}

module.exports = { venture, installSyntheticRuntime };
