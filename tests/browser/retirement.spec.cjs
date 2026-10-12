const { test, expect } = require('./generation.fixture.cjs');

// These cases load/reload two pages and sometimes render two saved ventures.
// Give the whole scenario room to run; deadlines/assertions remain unchanged
// and identical across engines (no retries, sleeps, or platform skips).
test.setTimeout(60000);

const origin = 'https://manifest.test';
const key = 'manifest.pending-request.v1';
const expired = {
  request_id: '11111111-1111-4111-8111-111111111111',
  idea: 'An expired synthetic workbench organizer', mix_id: null,
};
const differentIdea = 'A different synthetic irrigation controller';
const pending = page => page.evaluate(key => JSON.parse(localStorage.getItem(key)), key);
const retireButton = page => page.getByRole('button', { name: 'Retire abandoned request', exact: true });

async function seed(page, harness) {
  harness.setRecoveryResult({ state: 'unconfirmed' });
  await page.evaluate(({ key, expired }) => {
    localStorage.setItem(key, JSON.stringify(expired));
    localStorage.setItem('unrelated-access-setting', 'keep');
    document.cookie = 'test_creator_access=keep; path=/';
  }, { key, expired });
  await page.reload();
  await expect(retireButton(page)).toBeVisible();
}

async function confirmRetirement(page, accept = true) {
  let confirmed = false;
  page.once('dialog', async dialog => {
    expect(dialog.type()).toBe('confirm');
    expect(dialog.message()).toContain('already saved');
    expect(dialog.message()).toContain('permanently blocked from saving');
    confirmed = true;
    if (accept) await dialog.accept(); else await dialog.dismiss();
  });
  await retireButton(page).click();
  expect(confirmed, 'Retirement requires an explicit creator confirmation').toBe(true);
}

async function startFresh(page, harness) {
  const before = harness.generations.length;
  await page.locator('#idea').fill(differentIdea);
  await page.getByRole('button', { name: 'Manifest it' }).click();
  await expect.poll(() => harness.generations.length).toBe(before + 1);
  const request = harness.generations.at(-1);
  expect(request.page).toBe(page);
  expect(request.path).toBe('/api/manifest');
  expect(request.body.idea).toBe(differentIdea);
  expect(request.body.request_id).toMatch(/^[0-9a-f-]{36}$/);
  expect(request.body.request_id).not.toBe(expired.request_id);
  expect((await pending(page)).request_id).toBe(request.body.request_id);
  return request.body.request_id;
}

async function finishFresh(page, harness) {
  await page.evaluate(venture => {
    window.__synthetic.requests.at(-1).send('done', venture);
    window.__synthetic.requests.at(-1).close();
  }, harness.venture);
  await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
}

async function home(page) {
  await page.locator('a[data-route="/"]:visible').first().click();
  await page.clock.runFor(1000);
  await expect(page.locator('#idea')).toBeVisible();
}

function noRetiredGeneration(harness) {
  expect(harness.generations.every(request => request.body.request_id !== expired.request_id)).toBe(true);
}

test('retirement from home requires confirmation; reload and a different sentence use a fresh UUID', async ({ page, context, harness }) => {
  await seed(page, harness);
  const other = await context.newPage();
  await other.goto(origin);
  await expect(retireButton(other)).toBeVisible();
  await confirmRetirement(page, false);
  expect(harness.manifestRequests.filter(r => r.method === 'POST')).toHaveLength(0);
  expect(await pending(other)).toEqual(expired);
  await confirmRetirement(page);
  await expect(page.locator('#hero-error')).toContainText('permanently retired');
  expect(await pending(other)).toBeNull();
  expect(harness.generations).toHaveLength(0);
  await other.reload();
  const id = await startFresh(other, harness);
  expect((await pending(page)).request_id).toBe(id);
  expect(await other.evaluate(() => localStorage.getItem('unrelated-access-setting'))).toBe('keep');
  expect(await other.evaluate(() => document.cookie)).toContain('test_creator_access=keep');
  await finishFresh(other, harness);
  noRetiredGeneration(harness);
});

for (const state of ['retired', 'completed']) {
  for (const trigger of ['reload', 'different sentence']) {
    test(`late ${state} lookup after another tab retires: ${trigger} cannot erase or replace a newer UUID`, async ({ page, context, harness }) => {
      await seed(page, harness);
      const other = await context.newPage();
      if (trigger === 'different sentence') {
        await other.goto(origin);
        await expect(retireButton(other)).toBeVisible();
      }
      harness.holdNextLookup(other);
      if (trigger === 'reload') await other.goto(origin);
      else {
        await other.locator('#idea').fill(differentIdea);
        await other.getByRole('button', { name: 'Manifest it' }).click();
      }
      await expect.poll(() => harness.heldRequests()).toBe(1);
      expect(await pending(other)).toEqual(expired);
      // Retirement is a save-wins operation: a previously saved result must
      // still be recovered, never treated as permission to regenerate it.
      const terminal = state === 'completed' ? { state, venture: harness.venture } : { state };
      harness.setRetirementResult(terminal);
      await confirmRetirement(page);
      if (state === 'completed') {
        await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
        await expect(page).toHaveURL(`${origin}/v/browser-fixture`);
        await home(page);
      } else await expect(page.locator('#hero-error')).toContainText('permanently retired');
      expect(harness.generations).toHaveLength(0);
      const newId = await startFresh(page, harness);
      await harness.releaseHeld(other, terminal);
      if (state === 'completed') {
        await expect(other.locator('.v-name')).toHaveText(harness.venture.name);
        await expect(other).toHaveURL(`${origin}/v/browser-fixture`);
        // Saved results survive reload; this uses only the intercepted GET.
        await other.reload();
        await expect(other.locator('.v-name')).toHaveText(harness.venture.name);
      } else await expect(other.locator('#hero-error')).not.toContainText('Checking your previous request');
      expect((await pending(other)).request_id).toBe(newId);
      expect(harness.generations).toHaveLength(1);
      noRetiredGeneration(harness);
      await finishFresh(page, harness);
    });
  }
}

for (const state of ['retired', 'completed']) {
  test(`late ${state} retirement reply cannot clear another tab's newer pending UUID`, async ({ page, context, harness }) => {
    await seed(page, harness);
    const other = await context.newPage();
    await other.goto(origin);
    await expect(retireButton(other)).toBeVisible();
    harness.holdNextRetirement(other);
    await confirmRetirement(other);
    await expect.poll(() => harness.heldRequests()).toBe(1);
    const terminal = state === 'completed' ? { state, venture: harness.venture } : { state };
    harness.setRetirementResult(terminal);
    await confirmRetirement(page);
    if (state === 'completed') {
      await expect(page.locator('.v-name')).toHaveText(harness.venture.name);
      await home(page);
    } else await expect(page.locator('#hero-error')).toContainText('permanently retired');
    const newId = await startFresh(page, harness);
    await harness.releaseHeld(other, terminal, true);
    if (state === 'completed') await expect(other.locator('.v-name')).toHaveText(harness.venture.name);
    else await expect(other.locator('#hero-error')).toContainText('permanently retired');
    expect((await pending(other)).request_id).toBe(newId);
    expect(harness.generations).toHaveLength(1);
    noRetiredGeneration(harness);
    await finishFresh(page, harness);
  });
}

test('lost retirement response preserves shared identity through reload until lookup confirms the fence', async ({ page, context, harness }) => {
  await seed(page, harness);
  const other = await context.newPage();
  await other.goto(origin);
  await expect(retireButton(other)).toBeVisible();
  harness.loseRetirementResponse();
  await confirmRetirement(page);
  await expect(page.locator('#hero-error')).toContainText('Retirement is unconfirmed');
  expect(await pending(page)).toEqual(expired);
  expect(await pending(other)).toEqual(expired);
  await page.reload();
  await expect(retireButton(page)).toBeVisible();
  await other.locator('#idea').fill(differentIdea);
  await other.getByRole('button', { name: 'Manifest it' }).click();
  await expect(retireButton(other)).toBeVisible();
  expect(await pending(other)).toEqual(expired);
  expect(harness.generations).toHaveLength(0);
  // Only a successful lookup may unblock a new submission.
  harness.holdNextLookup(other);
  await other.getByRole('button', { name: 'Manifest it' }).click();
  await expect.poll(() => harness.heldRequests()).toBe(1);
  expect(await pending(page)).toEqual(expired);
  expect(harness.generations).toHaveLength(0);
  await harness.releaseHeld(other, { state: 'retired' });
  await expect.poll(() => harness.generations.length).toBe(1);
  expect(harness.generations[0].body.idea).toBe(differentIdea);
  expect((await pending(page)).request_id).toBe(harness.generations[0].body.request_id);
  noRetiredGeneration(harness);
  await finishFresh(other, harness);
});

for (const accept of [false, true]) {
test(`interrupted partial preview: ${accept ? 'confirmed retirement keeps navigation usable' : 'cancel keeps retirement reachable after returning home'}`, async ({ page, context, harness }) => {
  harness.setRecoveryResult({ state: 'unconfirmed' });
  await harness.start('venture');
  const id = (await pending(page)).request_id;
  await harness.send('partial', { data: { name: 'Interrupted synthetic draft' }, streaming: 'tagline' });
  await harness.close();
  const note = page.locator('.venture > [role=status]');
  await expect(note).toContainText('not a confirmed result');
  await expect(retireButton(page)).toBeVisible();
  await confirmRetirement(page, accept);
  if (accept) {
    await expect(note).toContainText('permanently retired');
    expect(await pending(page)).toBeNull();
  } else {
    expect((await pending(page)).request_id).toBe(id);
    expect(harness.manifestRequests.filter(r => r.method === 'POST')).toHaveLength(0);
  }
  await page.getByRole('link', { name: 'Return to your ventures' }).click();
  await page.clock.runFor(1000);
  if (accept) {
    expect(harness.generations).toHaveLength(1);
    await startFresh(page, harness);
    expect(harness.generations.at(-1).body.request_id).not.toBe(id);
    await finishFresh(page, harness);
    return;
  }
  await expect(retireButton(page)).toBeVisible();
  const other = await context.newPage();
  await other.goto(origin);
  await expect(retireButton(other)).toBeVisible();
  await confirmRetirement(page);
  await expect(page.locator('#hero-error')).toContainText('permanently retired');
  expect(await pending(other)).toBeNull();
  // Retirement itself must never send a second generation.
  expect(harness.generations).toHaveLength(1);
});
}
