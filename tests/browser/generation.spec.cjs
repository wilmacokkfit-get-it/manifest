const { test, expect, sseFrame } = require('./generation.fixture.cjs');

test('pending recovery: one visible lookup and Manifest checks instead of silently ignoring clicks', async ({ page, harness }) => {
  test.setTimeout(60000);
  harness.setRecoveryResult({ state: 'pending' });
  await page.evaluate(() => localStorage.setItem('manifest.pending-request.v1', JSON.stringify({
    request_id: 'previous-fixture', idea: 'New drip irrigation line', mix_id: null,
  })));
  await page.reload();
  await expect(page.locator('#hero-error')).toContainText('No new generation');
  await expect(page.getByRole('button', { name: 'Check previous request' })).toBeVisible();
  expect(harness.recoveryLookups()).toBe(1);
  expect(await page.evaluate(() => window.__synthetic.requests.length)).toBe(0);
  const errorTop = await page.locator('#hero-error').evaluate(el => el.getBoundingClientRect().top);
  const formTop = await page.locator('#orb-wrap').evaluate(el => el.getBoundingClientRect().top);
  expect(errorTop).toBeLessThan(formTop);
  await page.getByRole('button', { name: 'Manifest it' }).click();
  await expect.poll(() => harness.recoveryLookups()).toBe(2);
  await expect(page.locator('#hero-error')).toContainText('No new generation');
  expect(await page.evaluate(() => window.__synthetic.requests.length)).toBe(0);
  harness.setRecoveryResult({ state: 'unconfirmed' });
  await page.getByRole('button', { name: 'Check previous request' }).click();
  await expect(page.getByRole('button', { name: 'Retire abandoned request' })).toBeVisible();
  expect(harness.recoveryLookups()).toBe(3);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('manifest.pending-request.v1')).request_id)).toBe('previous-fixture');
});

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

const unicodeText = 'Café 水 🌱 — "quoted" \\ trail';
const unicodePartial = (operation, text = unicodeText) => operation === 'venture' ?
  { data: { name: 'Synthetic venture', tagline: text }, streaming: 'tagline' } :
  { data: { first_move: text }, streaming: 'first_move' };
const streamText = (page, operation) => page.locator(operation === 'venture' ?
  '.v-tagline' : '.first-move span');
const streamFinal = (harness, operation) => operation === 'venture' ?
  { ...harness.venture, tagline: unicodeText } :
  { ...harness.venture, path: { tier: 0, tier_label: 'Bootstrap', first_move: unicodeText,
    steps: [{ n: 1, title: 'Synthetic step', status: 'todo' }] } };

async function retryStream(page, harness, operation) {
  if (operation === 'venture') {
    await page.getByRole('link', { name: 'Return to your ventures' }).click();
    await page.clock.runFor(1000);
    await page.getByRole('button', { name: 'Manifest it' }).click();
  } else await retry(page, operation).click();
  await expect.poll(() => page.evaluate(() => window.__synthetic.requests.length)).toBe(2);
  await page.clock.runFor(2000);
  await harness.sendChunks(sseFrame('partial', unicodePartial(operation)), [1, 7, 2, 13]);
  await expect(streamText(page, operation)).toHaveText(unicodeText);
  await page.clock.runFor(3000);
  await harness.sendChunks(sseFrame('done', streamFinal(harness, operation)), [3, 1, 11, 2]);
  await harness.close();
  await expect(streamText(page, operation)).toHaveText(unicodeText);
  await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
  await expect.poll(() => harness.measures(operation)).toContainEqual({
    name: `manifest.${operation}.completed`, duration: 5000,
  });
  expect((await harness.measures(operation)).filter(e => e.name.endsWith('.first-visible-text')))
    .toEqual([
      { name: `manifest.${operation}.first-visible-text`, duration: 0 },
      { name: `manifest.${operation}.first-visible-text`, duration: 2000 },
    ]);
  expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
}

for (const operation of ['venture', 'path']) {
  test(`${operation}: byte boundaries gate JSON, Unicode and the SSE delimiter`, async ({ page, harness }) => {
    await harness.start(operation);
    const bytes = Buffer.from(sseFrame('partial', unicodePartial(operation)), 'utf8');
    // Hold after an incomplete JSON key, then inside 2-, 3- and 4-byte code points,
    // then between the delimiter's newlines. Nothing should paint prematurely.
    const cuts = [
      bytes.indexOf(Buffer.from('"data"')) + 3,
      ...['é', '水', '🌱'].map(char => bytes.indexOf(Buffer.from(char)) + 1),
      bytes.length - 1,
    ];
    let offset = 0;
    for (const cut of cuts) {
      await harness.sendBytes(bytes.subarray(offset, cut));
      offset = cut;
      await page.clock.runFor(1000);
      await expect(status(page, operation)).toContainText('Considering your idea');
      expect(await harness.measures(operation)).toEqual([]);
      await expect(streamText(page, operation)).toHaveCount(0);
    }
    await harness.sendBytes(bytes.subarray(offset));
    await expect(streamText(page, operation)).toHaveText(unicodeText);
    expect(await harness.measures(operation)).toEqual([
      { name: `manifest.${operation}.first-visible-text`, duration: 5000 },
    ]);
    await page.clock.runFor(2000);
    const done = Buffer.from(sseFrame('done', streamFinal(harness, operation)));
    await harness.sendBytes(done.subarray(0, -1));
    expect(await harness.measures(operation)).toHaveLength(1);
    expect(await harness.timers()).toEqual({ intervals: 1, deadlines: 1 });
    await page.clock.runFor(1000);
    await harness.sendBytes(done.subarray(-1));
    await harness.close();
    await expect.poll(() => harness.measures(operation)).toEqual([
      { name: `manifest.${operation}.first-visible-text`, duration: 5000 },
      { name: `manifest.${operation}.completed`, duration: 8000 },
    ]);
    await expect(streamText(page, operation)).toHaveText(unicodeText);
    expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
  });

  for (const sizes of [[1], [7, 2, 19, 1, 5]]) {
    test(`${operation}: arbitrary byte chunks (${sizes.join(',')}) preserve visible text and timings`, async ({ page, harness }) => {
      await harness.start(operation);
      await page.clock.runFor(3000);
      await harness.sendFrames([
        { event: 'meta', data: { provenance: harness.venture.provenance } },
        { event: 'partial', data: unicodePartial(operation) },
      ], sizes);
      await expect(streamText(page, operation)).toHaveText(unicodeText);
      await page.clock.runFor(4000);
      await harness.sendFrames([
        { event: 'partial', data: unicodePartial(operation) },
        { event: 'done', data: streamFinal(harness, operation) },
      ], sizes);
      await harness.close();
      await expect.poll(() => harness.measures(operation)).toEqual([
        { name: `manifest.${operation}.first-visible-text`, duration: 3000 },
        { name: `manifest.${operation}.completed`, duration: 7000 },
      ]);
      await expect(streamText(page, operation)).toHaveText(unicodeText);
      expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
    });
  }

  test(`${operation}: multiple events in one chunk are consumed in order`, async ({ page, harness }) => {
    await harness.start(operation);
    await page.clock.runFor(2000);
    await harness.sendFrames([
      { event: 'meta', data: { provenance: harness.venture.provenance } },
      { event: 'partial', data: unicodePartial(operation, 'Earlier draft') },
      { event: 'partial', data: unicodePartial(operation) },
    ]);
    await expect(streamText(page, operation)).toHaveText(unicodeText);
    expect(await harness.measures(operation)).toEqual([
      { name: `manifest.${operation}.first-visible-text`, duration: 2000 },
    ]);
    await page.clock.runFor(3000);
    await harness.sendFrames([
      { event: 'partial', data: unicodePartial(operation, 'Last draft') },
      { event: 'done', data: streamFinal(harness, operation) },
    ]);
    await harness.close();
    await expect(streamText(page, operation)).toHaveText(unicodeText);
    await expect.poll(() => harness.measures(operation)).toEqual([
      { name: `manifest.${operation}.first-visible-text`, duration: 2000 },
      { name: `manifest.${operation}.completed`, duration: 5000 },
    ]);
    expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
  });

  for (const failure of ['missing delimiter', 'truncated JSON', 'truncated Unicode', 'fragmented error']) {
    test(`${operation}: ${failure} stays failed and a fragmented retry completes`, async ({ page, harness }) => {
      await harness.start(operation);
      await harness.sendChunks(sseFrame('partial', unicodePartial(operation)), [1, 3, 8]);
      await expect(streamText(page, operation)).toHaveText(unicodeText);
      await page.clock.runFor(6000);
      if (failure === 'fragmented error') {
        await harness.sendChunks(sseFrame('error', { message: 'Synthetic outage — 水 🌱' }), [1]);
      } else {
        const done = Buffer.from(sseFrame('done', streamFinal(harness, operation)));
        const cut = failure === 'missing delimiter' ? done.length - 1 :
          failure === 'truncated JSON' ? done.indexOf(Buffer.from('"tagline"')) + 6 :
          done.indexOf(Buffer.from('🌱')) + 2;
        await harness.sendBytes(done.subarray(0, cut));
        await harness.close();
      }
      const note = page.locator('.venture > [role=status]');
      const message = failure === 'fragmented error' ? 'Synthetic outage — 水 🌱' :
        operation === 'venture' ? 'before completion' : 'stream ended early';
      await expect(note).toContainText(message);
      expect(await harness.measures(operation)).toEqual([
        { name: `manifest.${operation}.first-visible-text`, duration: 0 },
        { name: `manifest.${operation}.failed`, duration: 6000 },
      ]);
      expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
      await page.clock.runFor(10000);
      await expect(note).toContainText(message);
      expect(await page.evaluate(() => window.__synthetic.requests.length)).toBe(1);
      await retryStream(page, harness, operation);
      expect((await harness.measures(operation)).filter(e => e.name.endsWith('.failed'))).toHaveLength(1);
    });
  }
}

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
    // These must be native PerformanceMeasure entries, not fake-clock objects.
    expect(await page.evaluate(() => performance.getEntriesByType('measure')
      .filter(entry => entry.name.startsWith('manifest.'))
      .every(entry => entry instanceof PerformanceMeasure && entry.entryType === 'measure'))).toBe(true);
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
    // Retry must actually finish, not merely enable a button/send new headers.
    if (operation === 'refinement') {
      await harness.json({ ...harness.venture, name: 'Retried fixture', refinement: { state: 'completed' } });
      await expect(page.locator('.v-name')).toHaveText('Retried fixture');
    } else {
      await harness.send('done', harness.venture);
      await harness.close();
      await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
    }
    await expect.poll(() => harness.measures(operation)).toContainEqual({
      name: `manifest.${operation}.completed`, duration: 0,
    });
    expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
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

test('venture: correction keeps feedback and replaces the rejected preview before completion', async ({ page, harness }) => {
  test.setTimeout(60000);
  await harness.start('venture');
  await harness.send('partial', {
    data: {
      name: 'Canopy', tagline: 'A speculative bench-clamped ladder',
      description: 'An unusual clamp mechanism.',
      tiers: [{ label: 'The Lean Build', title: 'Unsafe prototype', description: 'Synthetic rejected instructions.' }],
      roadmap: [{ step: 1, action: 'Rejected build step', where: 'Not instructions to execute.' }],
    },
    streaming: 'roadmap',
  });
  await expect(page.getByText('Unsafe prototype', { exact: true })).toBeVisible();
  await harness.send('status', { phase: 'correcting' });
  await expect(page.locator('.venture > [role=status]')).toContainText('Refining your draft');
  await expect(page.getByText('Draft · refining the plan', { exact: true })).toBeVisible();
  await expect(page.getByText('Unsafe prototype', { exact: true })).toBeHidden();
  await page.clock.runFor(12000);
  await expect(page.locator('.venture > [role=status]')).toContainText('Refining your draft… 12s');
  const repaired = {
    ...harness.venture, name: 'Canopy',
    tiers: [{ label: 'The Lean Build', title: 'Paper fit study', description: 'A nonfunctional concept and qualified review.' }],
    roadmap: [{ step: 1, action: 'Request qualified review', where: 'A nonfunctional professional design review.' }],
  };
  await harness.send('partial', { data: repaired, streaming: null, replace: true });
  await expect(page.getByText('Paper fit study', { exact: true })).toBeVisible();
  await expect(page.getByText('Unsafe prototype', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Rejected build step', { exact: true })).toHaveCount(0);
  await harness.send('done', repaired);
  await expect(page).toHaveURL('https://manifest.test/v/browser-fixture');
  expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
  const measures = await harness.measures('venture');
  expect(measures.filter(entry => entry.name.endsWith('.first-visible-text'))).toHaveLength(1);
  expect(measures.filter(entry => entry.name.endsWith('.completed'))).toHaveLength(1);
});

test('venture: rejected draft is not shown as minting or a stalled connection', async ({ page, harness }) => {
  test.setTimeout(60000);
  await harness.start('venture');
  await harness.send('partial', {
    data: {
      name: 'Canopy', tagline: 'A speculative bench-clamped ladder',
      description: 'An unconventional clamp mechanism; not a validated load-bearing design.',
      tiers: [{ label: 'The Lean Build', title: 'Unsafe prototype', description: 'Synthetic rejected construction instructions.' }],
      roadmap: [{ step: 1, action: 'Synthetic rejected build', where: 'Not instructions to execute.' }],
    },
    streaming: 'roadmap',
  });
  await expect(page.getByText('Unsafe prototype', { exact: true })).toBeVisible();
  const message = 'Draft needs correction—not saved. Qualified safety review must precede construction.';
  await harness.send('error', { code: 'draft_rejected', message });
  await expect(page.getByText('Unreviewed draft · not saved', { exact: true })).toBeVisible();
  await expect(page.getByText('Unsafe prototype', { exact: true })).toBeHidden();
  await expect(page.getByText('Synthetic rejected build', { exact: true })).toBeHidden();
  await expect(page.locator('.venture > [role=status]')).toContainText(message);
  await expect(page.getByText(/minting…/)).toHaveCount(0);
  expect(await harness.timers()).toEqual({ intervals: 0, deadlines: 0 });
  await page.getByRole('link', { name: 'Return to your ventures' }).click();
  await page.clock.runFor(1000);
  await expect(page.getByRole('button', { name: 'Manifest it' })).toBeEnabled();
});

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
  expect(await page.evaluate(() => window.__synthetic.requests[0].aborted)).toBe(true);
  expect(await harness.measures('refinement')).toEqual([
    { name: 'manifest.refinement.failed', duration: 240000 },
  ]);
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
