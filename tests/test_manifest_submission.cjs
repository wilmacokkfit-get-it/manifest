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
    remove() {}, append() {}, prepend() {},
    replaceChildren(...children) { this.textContent = children.map(c => c.textContent || '').join(' '); },
    querySelectorAll: () => [] });
  const context = {
    localStorage: { getItem: k => storage.get(k) || null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) },
    API: '', AbortSignal, TextDecoder, crypto: { randomUUID },
    setTimeout: fn => { fn(); return 1; }, addEventListener() {}, confirm: () => true,
    manifestBusy: false, pendingMixSentence: null, pendingMixId: null,
    orbWrap: node(), heroTag: node(), formingName: node(), formingStatus: node(), heroError: node(),
    h: (tag, attrs) => Object.assign(node(), { textContent: attrs?.text || '' }),
    mount() {}, href: path => path, toast: message => errors.push(message),
    history: { replaceState() {} },
    VentureView: () => ({ ...node(), update() {}, complete: data => completions.push(data) }),
    generationWait: () => ({ signal: new AbortController().signal, first() {}, finish() {}, pause() {}, dispose() {} }),
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

  // A genuine server rejection must not be replaced by a generic connection
  // failure even if its terminal recovery lookup is briefly still pending.
  f = fixture();
  f.respond([streamResponse('event: error\ndata: {"message":"Qualified review must precede functional testing"}\n\n'),
    { ok: true, json: async () => ({ state: 'unconfirmed' }) }]);
  await f.context.startManifest(idea);
  assert.match(f.context.heroError.textContent, /Qualified review must precede/);
  assert.match(f.context.heroError.textContent, /Completion is unconfirmed/);
  assert.ok(f.context.pendingManifest());
  assert.equal(f.requests.filter(r => r.options.method === 'POST').length, 1);

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
  f.respond([setupResponse()]);
  await f.context.startManifest(idea);
  assert.equal(f.requests.filter(r => r.options.method === 'POST').length, 0);
  assert.match(f.requests[0].url, new RegExp(pending.request_id));
  assert.equal(f.context.pendingManifest().legacy, undefined);

  // Existing pending work gets one visible lookup, not another generation POST,
  // even when the sentence is unchanged. Busy clicks must not silently vanish.
  f = fixture();
  f.context.rememberManifest(pending);
  f.respond([{ ok: true, json: async () => ({ state: 'pending' }) }]);
  await f.context.startManifest(idea);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].options.method, undefined);
  assert.equal(f.context.pendingManifest().request_id, pending.request_id);
  assert.equal(f.context.manifestBusy, false);
  assert.match(f.context.heroError.textContent, /No new generation/);
  f.context.manifestBusy = true;
  await f.context.startManifest(idea);
  assert.match(f.context.heroError.textContent, /already being checked or generated/);
  assert.equal(f.requests.length, 1);

  // Retirement is explicit and lookup failures never discard the pending ID.
  f = fixture();
  f.context.rememberManifest(pending);
  f.respond([new Error('Retirement response lost after commit')]);
  await f.context.retireManifest(pending.request_id);
  assert.equal(f.context.pendingManifest().request_id, pending.request_id);
  assert.equal(f.requests[0].options.method, 'POST');
  assert.match(f.requests[0].url, /\/retire$/);
  f.respond([{ ok: true, json: async () => ({ state: 'retired' }) }, streamResponse(done)]);
  await f.context.startManifest('A different workbench organizer');
  assert.equal(f.completions.length, 1);
  assert.notEqual(JSON.parse(f.requests.at(-1).options.body).request_id, pending.request_id);

  // A successful retirement clears only that ID; a live request stays pending.
  f = fixture();
  f.context.rememberManifest(pending);
  f.respond([{ ok: true, json: async () => ({ state: 'pending' }) }]);
  await f.context.retireManifest(pending.request_id);
  assert.equal(f.context.pendingManifest().request_id, pending.request_id);
  f.respond([{ ok: true, json: async () => ({ state: 'retired' }) }]);
  await f.context.retireManifest(pending.request_id);
  assert.equal(f.context.pendingManifest(), null);

  // Save-wins returns the original result, never POSTs another generation.
  f = fixture();
  f.context.rememberManifest(pending);
  f.respond([{ ok: true, json: async () => ({ state: 'completed', venture }) }]);
  await f.context.retireManifest(pending.request_id);
  assert.equal(f.completions.length, 1);
  assert.equal(f.context.pendingManifest(), null);
  assert.equal(f.requests.length, 1);

  // No retirement without the creator's explicit confirmation.
  f = fixture();
  f.context.rememberManifest(pending);
  f.context.confirm = () => false;
  await f.context.retireManifest(pending.request_id);
  assert.equal(f.requests.length, 0);
  assert.equal(f.context.pendingManifest().request_id, pending.request_id);

  // The recoverable message exposes the action, not a new generation button.
  f.context.showUnconfirmedManifest({ state: 'unconfirmed' }, pending.request_id);
  assert.match(source, /text: 'Retire abandoned request'/);
  console.log('Submission recovery and safe pre-migration compatibility fixtures passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
