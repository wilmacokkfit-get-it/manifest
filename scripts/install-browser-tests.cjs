const { readdirSync, readFileSync, writeFileSync, existsSync } = require('node:fs');
const { join, dirname } = require('node:path');
const { spawnSync } = require('node:child_process');
const { configureBrowserEnvironment } = require('./browser-environment.cjs');

configureBrowserEnvironment();
const nix = process.platform === 'linux' && existsSync('/nix/store');
const browsers = nix ? ['firefox', 'webkit'] : ['chromium', 'firefox', 'webkit'];
// Supported Linux CI hosts can install OS dependencies with --with-deps.
// Replit uses .replit's Nix packages, not apt or a container.
const args = [...(process.argv.includes('--with-deps') && !nix ? ['--with-deps'] : []), ...browsers];
const result = spawnSync(process.execPath, [
  join(dirname(require.resolve('playwright/package.json')), 'cli.js'), 'install', ...args,
], { stdio: 'inherit' });
if (result.status !== 0) process.exit(result.status || 1);

if (nix) {
  // Upstream WebKit's wrapper replaces LD_LIBRARY_PATH. Retain Nix's library
  // path after the bundled libraries. Idempotent; never alter tracked sources.
  for (const revision of readdirSync(process.env.PLAYWRIGHT_BROWSERS_PATH)
    .filter(name => name.startsWith('webkit-'))) {
    for (const engine of ['minibrowser-wpe', 'minibrowser-gtk']) {
      const file = join(process.env.PLAYWRIGHT_BROWSERS_PATH, revision, engine, 'MiniBrowser');
      if (!existsSync(file)) continue;
      const source = readFileSync(file, 'utf8');
      const original = 'export LD_LIBRARY_PATH="${MYDIR}/lib:${MYDIR}/sys/lib"';
      const patched = 'export LD_LIBRARY_PATH="${MYDIR}/lib:${MYDIR}/sys/lib:${LD_LIBRARY_PATH:-}"';
      if (!source.includes(original) && !source.includes(patched)) {
        throw new Error(`WebKit wrapper changed; review library setup in ${file}`);
      }
      writeFileSync(file, source.replace(original, patched));
    }
  }
}
