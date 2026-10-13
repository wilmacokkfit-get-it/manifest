const { test, expect } = require('@playwright/test');
const { buildDocuments } = require('../../scripts/ios-waiting-check.cjs');

// Automation validates the disposable harness only. These are not iOS evidence.
for (const operation of ['venture', 'path', 'refinement']) {
  test(`device harness: ${operation} isolates networking, storage and navigation`, async ({ page, context }) => {
    const { controls, app } = buildDocuments();
    const unexpected = [], errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === 'https://device.test' && ['/', '/app.html'].includes(url.pathname))
        return route.fulfill({ body: url.pathname === '/' ? controls : app, contentType: 'text/html',
          headers: { 'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src 'self'; base-uri 'self'; form-action 'none'" } });
      unexpected.push(url.href);
      return route.abort();
    });
    await page.goto('https://device.test/');
    await page.locator('#ios').fill('0.0'); // Explicit smoke-test metadata, not a device claim.
    await page.locator('#model').fill('Automated harness smoke check');
    await page.locator('#operation').selectOption(operation);
    await page.locator('#start').click();
    await expect(page.locator('#summary')).toContainText('"reason": "started"');
    await expect(page.locator('#error')).toBeEmpty();
    const frame = page.frameLocator('#app');
    const status = operation === 'venture' ? '#forming-status' :
      operation === 'path' ? '.path-sub' : '#refinement-status';
    await expect(frame.locator(status)).toContainText('Considering your idea');
    await expect(page.locator('#error')).toBeEmpty();
    expect(await page.evaluate(() => {
      const w = document.getElementById('app').contentWindow;
      return { count: w.__synthetic.requests.length, timers: w.__synthetic.timers(),
        storedOutsideFrame: localStorage.getItem('manifest.pending-request.v1') };
    })).toEqual({ count: 1, timers: { intervals: 1, deadlines: 1 }, storedOutsideFrame: null });
    // Native timers/clocks in this test; no Playwright clock manipulation.
    await page.locator('#navigate').click();
    await expect(page.locator('#summary')).toContainText('"route": "/about"');
    expect(await page.evaluate(() => {
      const w = document.getElementById('app').contentWindow;
      return { aborted: w.__synthetic.requests[0].aborted, timers: w.__synthetic.timers() };
    })).toEqual({ aborted: true, timers: { intervals: 0, deadlines: 0 } });
    await page.locator('#start').click();
    await expect(frame.locator(status)).toContainText('Considering your idea');
    await page.locator('#complete').click();
    await expect(frame.locator('.v-name')).toHaveText(operation === 'refinement' ? 'Refined fixture' : 'Synthetic venture');
    await expect(page.locator('#error')).toBeEmpty();
    await page.locator('#snapshot').click();
    const sample = JSON.parse(await page.locator('#summary').textContent());
    expect(sample.errors).toEqual([]);
    expect(sample.unexpectedRequests).toEqual([]);
    expect(sample.timers).toEqual({ intervals: 0, deadlines: 0 });
    expect(sample.measures).toContainEqual(expect.objectContaining({
      name: `manifest.${operation}.completed`, native: true,
    }));
    expect(unexpected).toEqual([]);
    expect(errors).toEqual([]);
  });
}
