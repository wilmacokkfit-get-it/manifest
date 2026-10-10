// Deterministic browser logic fixtures; no network, models, images or workspace data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/app.js', 'utf8');
const start = source.indexOf('  const MANIFEST_REQUEST_KEY');
const end = source.indexOf('  // ---------- the reveal ----------', start);
const storage = new Map();
const requests = [];
const response = (state, venture) => ({ ok: true, json: async () => ({ state, ...(venture ? { venture } : {}) }) });
let fixtures = [];
let failStorage = false;
const context = {
  localStorage: {
    getItem: key => { if (failStorage) throw Error('disabled'); return storage.get(key) || null; },
    setItem: (key, value) => { if (failStorage) throw Error('disabled'); storage.set(key, value); },
    removeItem: key => storage.delete(key),
  },
  API: '',
  AbortSignal,
  setTimeout: fn => { fn(); return 1; },
  addEventListener: () => {},
  fetch: async (url, options) => {
    requests.push({ url, options });
    const fixture = fixtures.shift();
    if (fixture instanceof Error) throw fixture;
    return fixture || { ok: false, status: 404 };
  },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
const request = { request_id: 'fixture-request-id', idea: 'A compact workbench organizer', mix_id: null };
(async () => {
  context.rememberManifest(request);
  assert.equal(context.pendingManifest().request_id, request.request_id);
  const result = { id: 'saved-once', slug: 'original-venture' };
  fixtures = [new Error('offline'), { ok: false, status: 404 }, response('pending'), response('completed', result)];
  const recovered = await context.reconcileManifest(request.request_id, 4);
  assert.equal(recovered.state, 'completed');
  assert.equal(recovered.venture.id, result.id);
  assert.equal(requests.length, 4);
  for (const { url, options } of requests) {
    assert.match(url, /\/api\/manifest\/requests\/fixture-request-id$/);
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    assert.ok(!options.method || options.method === 'GET'); // Never generate during reconciliation.
    assert.equal(options.signal.aborted, false);
  }
  // An unavailable lookup must retain the ID, never invent a new attempt.
  fixtures = [new Error('offline')];
  assert.equal((await context.reconcileManifest(request.request_id, 1)).state, 'unconfirmed');
  assert.equal(context.pendingManifest().request_id, request.request_id);
  fixtures = [response('failed')];
  assert.equal((await context.reconcileManifest(request.request_id)).state, 'failed');
  fixtures = [{ ok: false, status: 503, json: async () => ({
    code: 'manifest_recovery_unavailable', error: 'Apply manifest-recovery.sql',
  }) }];
  assert.equal((await context.reconcileManifest(request.request_id)).message, 'Apply manifest-recovery.sql');
  context.clearManifest('a-different-request');
  assert.equal(context.pendingManifest().request_id, request.request_id);
  context.clearManifest(request.request_id);
  assert.equal(context.pendingManifest(), null);

  // Storage survives a page reload; malformed or disabled storage is safe.
  context.rememberManifest(request);
  const reload = vm.createContext({ ...context });
  vm.runInContext(source.slice(start, end), reload);
  assert.equal(reload.pendingManifest().request_id, request.request_id);
  storage.set('manifest.pending-request.v1', 'broken JSON');
  assert.equal(reload.pendingManifest(), null);
  failStorage = true;
  context.rememberManifest(request);
  assert.equal(context.pendingManifest().request_id, request.request_id);
  assert.ok(source.includes('resumeManifestRecovery();'));
  assert.ok(source.includes("addEventListener('online', resumeManifestRecovery)"));
  assert.ok(source.includes('request_id: requestId'));
  console.log('Manifest lookup, offline handling, durable identity and reload fixtures passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
