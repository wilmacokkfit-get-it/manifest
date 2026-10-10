const { existsSync } = require('node:fs');
const { resolve } = require('node:path');
const { spawnSync } = require('node:child_process');

// Keep large downloads with the ignored test outputs, not in Git/deployments.
function configureBrowserEnvironment() {
  process.env.PLAYWRIGHT_BROWSERS_PATH ||= resolve(__dirname, '../generated-artifacts/playwright-browsers');
  if (process.platform !== 'linux' || (!existsSync('/etc/NIXOS') &&
      !process.env.REPLIT_NIX_CHANNEL && !existsSync('/nix/store'))) return { ...process.env };

  // Don't point a new Playwright version at old Nix-packaged browser revisions.
  // Use the matching upstream build with Nix's libraries instead.
  if (process.arch !== 'x64') throw new Error('The Replit browser library setup requires x64');
  process.env.PLAYWRIGHT_HOST_PLATFORM_OVERRIDE ||= 'ubuntu24.04-x64';
  const result = spawnSync('nix-instantiate', [
    '--eval', '--strict', '--json', resolve(__dirname, 'browser-libraries.nix'),
  ], { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`Cannot resolve Playwright's Nix libraries: ${result.stderr || result.error}`);
  }
  // Playwright's generic host check uses the parent process's search path,
  // not launchOptions.env. The actual browser launch still fails on missing libs.
  process.env.PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS = '1';
  const { libraryPath: nixLibraries, mesaDrivers } = JSON.parse(result.stdout);
  const libraryPath = [
    nixLibraries, process.env.LD_LIBRARY_PATH,
  ].filter(Boolean).join(':');
  // Browser libraries must not override Node's own sqlite/ICU libraries.
  return {
    ...process.env,
    LD_LIBRARY_PATH: libraryPath,
    LIBGL_DRIVERS_PATH: `${mesaDrivers}/lib/dri`,
    __EGL_VENDOR_LIBRARY_FILENAMES: `${mesaDrivers}/share/glvnd/egl_vendor.d/50_mesa.json`,
    LIBGL_ALWAYS_SOFTWARE: '1',
  };
}

module.exports = { configureBrowserEnvironment };
