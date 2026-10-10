const browserEnvironment = require('./scripts/browser-environment.cjs').configureBrowserEnvironment();
const { defineConfig, devices } = require('@playwright/test');
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
    headless: true,
    // Routing cannot intercept service-worker requests. Never let one bypass
    // the synthetic-only fixture.
    serviceWorkers: 'block',
    launchOptions: { env: browserEnvironment },
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          env: browserEnvironment,
          executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ||
            (systemChromium || (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined)),
        },
      },
    },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
    { name: 'mobile-webkit', use: { ...devices['iPhone 13'] } },
  ],
});
