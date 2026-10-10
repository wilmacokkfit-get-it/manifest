const { test, expect } = require('./generation.fixture.cjs');

for (const accept of [false, true]) {
  test(`legacy pending attempt: ${accept ? 'confirmed dismissal allows a separate manual attempt' : 'cancel preserves the pending attempt'}`, async ({ page, harness }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => {
      localStorage.setItem('manifest.pending-request.v1', JSON.stringify({
        request_id: 'legacy-fixture', idea: 'New drip irrigation line', mix_id: null, legacy: true,
      }));
      localStorage.setItem('unrelated-access-setting', 'keep');
      document.cookie = 'test_creator_access=keep; path=/';
    });
    await page.reload();
    const dismiss = page.getByRole('button', { name: 'Dismiss this old pending attempt' });
    await expect(dismiss).toBeVisible();
    expect(await page.evaluate(() => window.__synthetic.requests.length)).toBe(0);
    page.once('dialog', async dialog => {
      expect(dialog.message()).toContain('duplicate');
      expect(dialog.message()).toContain('generation allowance');
      if (accept) await dialog.accept(); else await dialog.dismiss();
    });
    await dismiss.click();
    expect(await page.evaluate(() => window.__synthetic.requests.length)).toBe(0);
    expect(await page.evaluate(() => localStorage.getItem('unrelated-access-setting'))).toBe('keep');
    expect(await page.evaluate(() => document.cookie)).toContain('test_creator_access=keep');
    if (!accept) {
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem('manifest.pending-request.v1')).request_id)).toBe('legacy-fixture');
      await expect(dismiss).toBeVisible();
    } else {
      expect(await page.evaluate(() => localStorage.getItem('manifest.pending-request.v1'))).toBeNull();
      await expect(page.locator('#hero-error')).toContainText('No new request has been sent');
      await page.getByRole('button', { name: 'Manifest it' }).click();
      await expect.poll(() => page.evaluate(() => window.__synthetic.requests.length)).toBe(1);
      const pending = await page.evaluate(() => JSON.parse(localStorage.getItem('manifest.pending-request.v1')));
      expect(pending.request_id).not.toBe('legacy-fixture');
      expect(pending.legacy).toBeUndefined();
      await harness.send('done', harness.venture);
      await harness.close();
      await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
    }
  });
}

const status = (page, operation) => page.locator(operation === 'venture' ?
  '#forming-status' : operation === 'path' ? '.path-sub' : '#refinement-status');
const retry = (page, operation) => operation === 'path' ?
  page.getByRole('button', { name: 'Walk this path' }).first() :
  page.getByRole('button', { name: 'Refine once' });
const partial = operation => operation === 'venture' ?
  { data: { name: 'Synthetic venture', tagline: 'Partial text' }, streaming: 'tagline' } :
  { data: { first_move: 'Synthetic first step' }, streaming: 'first_move' };

for (const operation of ['venture', 'path', 'refinement']) {
  test(`${operation}: elapsed feedback and completion measurements`, async ({ page, harness }) => {
    await harness.start(operation);
    await expect(status(page, operation)).toContainText('Considering your idea… 0s');
    await page.clock.runFor(31000);
    await expect(status(page, operation)).toContainText('31s. Still working');
    expect(await harness.measures(operation)).toEqual([]);
    if (operation !== 'refinement') {
      await harness.send('partial', partial(operation));
      const waiting = operation === 'venture' ? page.locator('.venture > [role=status]') : status(page, operation);
      await expect(waiting).toContainText('Writing… 31s');
      await expect(page.getByText(operation === 'venture' ? 'Partial text' : 'Synthetic first step', { exact: true })).toBeVisible();
      await page.clock.runFor(43000);
      await expect(waiting).toContainText('Writing… 74s');
      expect(await harness.measures(operation)).toEqual([
        { name: `manifest.${operation}.first-visible-text`, duration: 31000 },
      ]);
      // Additional partials must not duplicate the first-text measure.
      await harness.send('partial', partial(operation));
      const final = operation === 'path' ? { ...harness.venture,
        path: { tier: 0, tier_label: 'Bootstrap', first_move: 'Synthetic first step',
          steps: [{ n: 1, title: 'Synthetic step', status: 'todo' }] } } : harness.venture;
      await harness.send('done', final);
      await harness.close();
    } else {
      // Refinement is JSON, so it has no first-visible-text entry.
      await page.clock.runFor(43000);
      await harness.json({ ...harness.venture, name: 'Refined fixture', refinement: { state: 'completed' } });
      await expect(page.locator('.v-name')).toHaveText('Refined fixture');
    }
    await expect.poll(() => harness.measures(operation)).toContainEqual({
      name: `manifest.${operation}.completed`, duration: 74000,
    });
    expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
    await page.clock.runFor(300000);
    expect(await page.evaluate(() => window.__synthetic.requests[0].aborted)).toBe(true);
    expect((await harness.measures(operation)).filter(e => e.name.endsWith('.failed'))).toHaveLength(0);
  });

  test(`${operation}: watchdog abort and safe retry`, async ({ page, harness }) => {
    await harness.start(operation);
    await page.clock.runFor(operation === 'refinement' ? 240000 : 210000);
    await expect.poll(() => page.evaluate(() => window.__synthetic.requests[0].aborted)).toBe(true);
    if (operation === 'venture') {
      await expect(page.locator('#hero-error')).toContainText('before completion');
      await expect(page.getByRole('button', { name: 'Manifest it' })).toBeEnabled();
    } else {
      await expect(page.locator(operation === 'refinement' ? '#refinement-status' : '.venture > [role=status]')).toContainText('timed out');
      await expect(retry(page, operation)).toBeEnabled();
    }
    expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
    expect(await harness.measures(operation)).toEqual([{
      name: `manifest.${operation}.failed`, duration: operation === 'refinement' ? 240000 : 210000,
    }]);
    await page.clock.runFor(10000);
    if (operation === 'venture') {
      await page.getByRole('button', { name: 'Manifest it' }).click();
    } else await retry(page, operation).click();
    await expect.poll(() => page.evaluate(() => window.__synthetic.requests.length)).toBe(2);
  });

  for (const phase of operation === 'refinement' ? ['before text'] : ['before text', 'after partial text']) {
  test(`${operation}: navigation ${phase} aborts waiting and removes timers`, async ({ page, harness }) => {
    await harness.start(operation);
    if (phase === 'after partial text') await harness.send('partial', partial(operation));
    await page.clock.runFor(5000);
    expect(await harness.timers()).toEqual({ intervals: 1, deadlines: 1 });
    await page.locator('a[data-route="/about"]:visible').first().click();
    await expect(page).toHaveURL('https://manifest.test/about');
    await expect.poll(() => harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
    expect(await page.evaluate(() => window.__synthetic.requests[0].aborted)).toBe(true);
    const entries = await harness.measures(operation);
    expect(entries).toContainEqual({ name: `manifest.${operation}.interrupted`, duration: 5000 });
    await page.clock.runFor(300000);
    expect(await harness.measures(operation)).toEqual(entries);
    await expect(page).toHaveURL('https://manifest.test/about');
  });
  }
}

for (const operation of ['venture', 'path']) {
  for (const failure of ['early EOF', 'provider error']) {
    test(`${operation}: ${failure} persists beyond toast and exposes retry`, async ({ page, harness }) => {
      await harness.start(operation);
      await harness.send('partial', partial(operation));
      await page.clock.runFor(10000);
      if (failure === 'early EOF') await harness.close();
      else await harness.send('error', { message: 'Synthetic provider unavailable' });
      const note = page.locator('.venture > [role=status]');
      const message = failure === 'provider error' ? 'Synthetic provider unavailable' :
        operation === 'venture' ? 'before completion' : 'stream ended early';
      await expect(note).toContainText(message);
      expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
      await page.clock.runFor(10000);
      await expect(note).toContainText(message);
      if (operation === 'venture') {
        await page.getByRole('link', { name: 'Return to your ventures' }).click();
        await page.clock.runFor(1000); // finish the hero's return transition
        await expect(page.getByRole('button', { name: 'Manifest it' })).toBeEnabled();
      } else await expect(retry(page, operation)).toBeEnabled();
    });
  }
}

test('refinement: provider error preserves result, context, and retry', async ({ page, harness }) => {
  await harness.start('refinement');
  await harness.json({ error: 'Synthetic provider unavailable' }, 502);
  await expect(page.locator('#refinement-status')).toContainText('Synthetic provider unavailable');
  await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
  await expect(page.locator('#refinement-context')).toHaveValue('Make it compact');
  await expect(retry(page, 'refinement')).toBeEnabled();
  expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
  await page.clock.runFor(10000);
  await expect(page.locator('#refinement-status')).toContainText('Synthetic provider unavailable');
});

test('refinement: watchdog interrupts a stalled JSON body, not just headers', async ({ page, harness }) => {
  await harness.start('refinement');
  await harness.openJsonBody();
  await page.clock.runFor(240000);
  await expect(page.locator('#refinement-status')).toContainText('timed out');
  await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
  await expect(retry(page, 'refinement')).toBeEnabled();
  expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
});

test('venture: navigation during disconnect recovery ignores a late saved result', async ({ page, harness }) => {
  harness.holdRecovery();
  await harness.start('venture');
  await harness.send('partial', partial('venture'));
  await page.clock.runFor(10000);
  await harness.close();
  await expect.poll(() => harness.recoveryRequests()).toBe(1);
  await expect(page.locator('.venture > [role=status]')).toContainText('Checking for your saved venture');
  expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
  await page.locator('a[data-route="/about"]:visible').first().click();
  await expect(page).toHaveURL('https://manifest.test/about');
  await harness.resolveRecovery({ state: 'completed', venture: harness.venture });
  await page.clock.runFor(300000);
  await expect(page).toHaveURL('https://manifest.test/about');
  await expect(page.locator('.venture')).toHaveCount(0);
  expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
  expect(await harness.measures('venture')).toEqual([
    { name: 'manifest.venture.first-visible-text', duration: 0 },
    { name: 'manifest.venture.interrupted', duration: 10000 },
  ]);
});
