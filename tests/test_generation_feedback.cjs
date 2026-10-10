// The production helper is exercised with a deterministic clock; no network or DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public/app.js', 'utf8');
const start = source.indexOf('  function generationWait(');
const end = source.indexOf('\n  // ---------- routing', start);
assert.ok(start > 0 && end > start);
let now = 0;
const intervals = new Map(), deadlines = new Map(), measurements = [];
let id = 0;
const context = {
  performance: {
    now: () => now,
    measure: (name, timing) => measurements.push({ name, ...timing }),
  },
  AbortController,
  setInterval: fn => { intervals.set(++id, fn); return id; },
  clearInterval: i => intervals.delete(i),
  setTimeout: fn => { deadlines.set(++id, fn); return id; },
  clearTimeout: i => deadlines.delete(i),
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
for (const operation of ['venture', 'refinement', 'path']) {
  now = 0;
  const messages = [];
  const wait = context.generationWait(operation, text => messages.push(text));
  assert.match(messages.at(-1), /Considering your idea… 0s/);
  now = 31000; [...intervals.values()][0]();
  assert.match(messages.at(-1), /Still working.*two-minute limit/);
  wait.first(); wait.first();
  assert.match(messages.at(-1), /Writing… 31s/);
  now = 74000;
  wait.finish(); wait.finish('interrupted'); wait.dispose();
  assert.equal(intervals.size, 0);
  assert.equal(deadlines.size, 0);
  assert.equal(measurements.filter(m => m.name === `manifest.${operation}.first-visible-text`).length, 1);
  assert.equal(measurements.find(m => m.name === `manifest.${operation}.completed`).end, 74000);
}
const wait = context.generationWait('timeout', () => {});
[...deadlines.values()][0]();
assert.equal(wait.signal.aborted, true);
wait.finish('failed'); wait.dispose();
assert.equal(intervals.size, 0);
assert.equal(deadlines.size, 0);
assert.ok(source.includes("if (!done) throw new Error('The connection ended before completion."));
assert.ok(source.includes("if (pathStreaming) throw new Error('The stream ended early."));
console.log('Generation feedback clock, deadline, measurements and cleanup checks passed.');
