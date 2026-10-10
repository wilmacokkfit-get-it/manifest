const { defineConfig } = require('@playwright/test');
const { existsSync } = require('node:fs');
const { spawnSync } = require('node:child_process');
const systemChromium = spawnSync('which', ['chromium'], { encoding: 'utf8' }).stdout?.trim();

module.exports = defineConfig({
  testDir: './tests/browser',
  fullyParallel: true,
  timeout: 15000,
  outputDir: 'generated-artifacts/browser-results',
  reporter: 'list',
  use: {
    browserName: 'chromium',
    headless: true,
    launchOptions: {
      // Replit/Nix Chromium has its shared libraries wired up already.
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ||
        (systemChromium || (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined)),
    },
    trace: 'retain-on-failure',
  },
});
