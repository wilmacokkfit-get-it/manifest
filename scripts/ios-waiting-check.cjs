// Disposable static fixture. Never import server.py or read environment secrets.
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const http = require('node:http');
const { venture, installSyntheticRuntime } = require('../tests/browser/synthetic-runtime.cjs');

function buildDocuments() {
  const read = file => readFileSync(resolve(__dirname, '..', file), 'utf8');
  const script = text => `<script>${text.replace(/<\/script/gi, '<\\/script')}</script>`;
  const setup = `(${function (venture) {
    'use strict';
    window.__MANIFEST_SERVED__ = true;
    history.replaceState(null, '', '/');
    const memory = () => {
      const values = new Map();
      return {
        getItem: key => values.get(String(key)) ?? null,
        setItem: (key, value) => values.set(String(key), String(value)),
        removeItem: key => values.delete(String(key)),
        clear: () => values.clear(),
        key: index => [...values.keys()][index] ?? null,
        get length() { return values.size; },
      };
    };
    // Fail closed if Safari will not let us isolate storage.
    Object.defineProperty(window, 'localStorage', { value: memory() });
    Object.defineProperty(window, 'sessionStorage', { value: memory() });
    window.__device = { errors: [], unexpected: [], venture };
    const problem = message => window.__device.errors.push(String(message));
    window.addEventListener('error', event => problem(event.message));
    window.addEventListener('unhandledrejection', event => problem(event.reason));
    window.addEventListener('securitypolicyviolation', event => problem(`CSP: ${event.violatedDirective}`));
    window.__recordSyntheticGeneration = () => {};
    window.fetch = async (url, options = {}) => {
      const path = new URL(url, location.href).pathname;
      const method = options.method || 'GET';
      let body, status = 200;
      if (method === 'GET' && path === '/api/ventures/browser-fixture') body = venture;
      else if (method === 'GET' && /^\/api\/manifest\/requests\/[^/]+$/.test(path)) body = { state: 'failed' };
      else if (method === 'GET' && path === '/api/owner-auth/config') {
        status = 503; body = { error: 'Authentication disabled in isolated tests' };
      } else if (method === 'GET' && path === '/api/owner-auth/status') body = { authenticated: false };
      else {
        window.__device.unexpected.push(`${method} ${path}`);
        throw new Error(`Unmocked request blocked: ${method} ${path}`);
      }
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    };
    // Block non-SPA navigation as well as provider fetches. This is a test, not
    // a way to sign in, follow suppliers, or interact with the live application.
    document.addEventListener('click', event => {
      const link = event.target.closest('a');
      if (link && !link.hasAttribute('data-route')) event.preventDefault();
    }, true);
    document.addEventListener('submit', event => {
      if (event.target.id !== 'idea-form') event.preventDefault();
    }, true);
  }.toString()})(${JSON.stringify(venture)});`;
  const app = read('public/index.html')
    .replace(/<link\b[^>]*>/g, '')
    .replace(/<script\b[^>]*src=[^>]*><\/script>/g, '')
    .replace('<!--BASE-->', '<base href="/">')
    .replace('</head>', () => `<style>${read('public/styles.css')}</style>${script(setup)}${script(`(${installSyntheticRuntime.toString()})();`)}</head>`)
    .replace('</body>', () => `${script(read('public/app.js'))}${script(read('public/owner-auth.js'))}</body>`);
  const controls = read('tests/device/controls.html')
    .replace('<!--CONTROLLER-->', () => script(read('tests/device/controller.js')));
  return { app, controls };
}

function createServer() {
  const documents = buildDocuments();
  return http.createServer((req, res) => {
    const path = new URL(req.url, 'http://fixture.invalid').pathname;
    const body = path === '/' ? documents.controls : path === '/app.html' ? documents.app : null;
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Security-Policy',
      "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
      "img-src data:; font-src 'none'; connect-src 'none'; frame-src 'self'; " +
      "form-action 'none'; object-src 'none'; base-uri 'self'; worker-src 'none'");
    res.writeHead(body && req.method === 'GET' ? 200 : 404, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(body && req.method === 'GET' ? body : 'Not served by the isolated device fixture.');
  });
}

if (require.main === module) {
  const port = Number(process.argv[2] || 8099);
  createServer().listen(port, '0.0.0.0', () => {
    console.log(`Synthetic-only device check on port ${port}. Use a dedicated HTTPS origin on iPhone.`);
    console.log('No app server, providers, database, environment secrets, or disk writes. Ctrl-C disposes it.');
  });
}
module.exports = { buildDocuments, createServer };
