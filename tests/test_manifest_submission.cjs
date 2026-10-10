// Exercise the real submission/reconciliation functions in an isolated DOM fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const source = fs.readFileSync('public/app.js', 'utf8');
function extract(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
function streamResponse(text) {
  let read = false;
  return {
    ok: true, status: 200, headers: { get: () => 'text/event-stream' },
    body: { getReader: () => ({
      read: async () => read ? { done: true } : (read = true, { value: new TextEncoder().encode(text), done: false }),
      cancel: async () => {},
    }) },
  };
}
function setupResponse() {
  const body = { code: 'manifest_recovery_unavailable', error: 'Apply manifest-recovery.sql' };
  return { ok: false, status: 503, clone: () => ({ json: async () => body }), json: async () => body };
}
function fixture() {
  const storage = new Map(), requests = [], completions = [], errors = [];
  let responses = [];
  const node = () => ({ textContent: '', style: {}, classList: { add() {}, remove() {}, toggle() {} },
    remove() {}, append() {}, prepend() {}, querySelectorAll: () => [] });
  const context = {
    localStorage: { getItem: k => storage.get(k) || null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
    API: '', AbortSignal, TextDecoder, crypto: { randomUUID },
    setTimeout: fn => { fn(); return 1; }, addEventListener() {},
    manifestBusy: false, pendingMixSentence: null, pendingMixId: null,
    orbWrap: node(), heroTag: node(), formingName: node(), formingStatus: node(), heroError: node(),
    h: node, mount() {}, href: path => path, toast: message => errors.push(message),
    history: { replaceState() {} },
    VentureView: () => ({ ...node(), update() {}, complete: data => completions.push(data) }),
    generationWait: () => ({ signal: new AbortController().signal, first() {}, finish() {}, dispose() {} }),
    fetch: async (url, options) => {
      requests.push({ url, options });
      const next = responses.shift();
      if (next instanceof Error) throw next;
      if (!next) throw new Error('No fixture response');
      return next;
    },
  };
  vm.createContext(context);
  vm.runInContext(extract('  const MANIFEST_REQUEST_KEY', '  // ---------- the reveal ----------'), context);
  vm.runInContext(extract('  async function startManifest(', '  // ---------- venture view ----------'), context);
  return { context, requests, completions, errors, respond: values => { responses = [...values]; } };
}
const idea = 'A compact workbench organizer';
const venture = { id: 'saved-venture', slug: 'saved-once', name: 'Saved venture' };
const done = `event: done\ndata: ${JSON.stringify(venture)}\n\n`;
(async () => {
  // Lost stream responses recover from GET only, not another POST.
  let f = fixture();
  f.respond([streamResponse('event: meta\ndata: {}\n\n'),
    { ok: true, json: async () => ({ state: 'completed', venture }) }]);
  await f.context.startManifest(idea);
  assert.equal(f.completions.length, 1);
  assert.equal(f.requests.filter(r => r.options.method === 'POST').length, 1);
  assert.match(f.requests[1].url, /\/api\/manifest\/requests\//);
  assert.equal(f.context.pendingManifest(), null);
  assert.equal(f.context.manifestBusy, false);

  // Before migration, explicit setup failure precedes a single legacy generation.
  f = fixture();
  f.respond([setupResponse(), streamResponse(done)]);
  await f.context.startManifest(idea);
  assert.equal(f.requests.length, 2);
  assert.ok(JSON.parse(f.requests[0].options.body).request_id);
  assert.ok(!JSON.parse(f.requests[1].options.body).request_id);
  assert.equal(f.completions.length, 1);
  assert.match(f.errors[0], /Automatic recovery is unavailable/);
  assert.equal(f.context.pendingManifest(), null);

  // A lost legacy response must never downgrade/replay again, even on submit.
  f = fixture();
  f.respond([setupResponse(), new Error('Connection dropped after save')]);
  await f.context.startManifest(idea);
  assert.equal(f.context.pendingManifest().legacy, true);
  const count = f.requests.length;
  await f.context.startManifest(idea);
  await f.context.startManifest('A different workbench organizer');
  assert.equal(f.requests.length, count);
  assert.equal(f.completions.length, 0);
  assert.equal(f.context.manifestBusy, false);

  // An already unconfirmed durable ID cannot downgrade when setup disappears.
  f = fixture();
  const pending = { request_id: randomUUID(), idea, mix_id: null };
  f.context.rememberManifest(pending);
  f.respond([setupResponse(), setupResponse()]);
  await f.context.startManifest(idea);
  assert.equal(f.requests.filter(r => r.options.method === 'POST').length, 1);
  assert.equal(JSON.parse(f.requests[0].options.body).request_id, pending.request_id);
  assert.equal(f.context.pendingManifest().legacy, undefined);
  console.log('Submission recovery and safe pre-migration compatibility fixtures passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
