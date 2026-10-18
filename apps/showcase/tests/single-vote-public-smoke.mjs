import { chromium } from '../../web/node_modules/@playwright/test/index.mjs';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const base = process.env.SHOWCASE_QA_ORIGIN;
assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(base || ''), 'Owned local test origin required.');
const dir = process.env.SHOWCASE_QA_OUTPUT || 'apps/showcase/test-results';
await mkdir(dir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const errors = [];
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('pageerror', (e) => errors.push(e.message));
const getState = async () => (await page.request.get(base + '/api/state')).json();
try {
  const before = await getState();
  await page.goto(base + '/');
  await page.waitForFunction(() => {
    const e = document.querySelector('.brand img');
    return e?.complete && e.naturalWidth > 0;
  });
  if (before.published) await page.locator('.seat').first().waitFor();
  if (before.content?.phase === 'vote') {
    await page.locator('.vote-qr').waitFor();
    await page.waitForFunction(async () => (await (await fetch('/api/state')).json()).collecting);
    await page.locator('.vote-qr').screenshot({ path: dir + '/rendered-qr.png' });
  }
  if (before.content?.phase === 'results') {
    await page.locator('.award-row').first().waitFor();
    await page.waitForFunction(() =>
      [...document.querySelectorAll('.award-art')].every((e) => e.complete && e.naturalWidth > 0),
    );
    const rows = await page.locator('.award-row').evaluateAll((nodes) =>
      nodes.map((n) => {
        const r = n.getBoundingClientRect();
        return { y: r.y, bottom: r.bottom };
      }),
    );
    assert.equal(rows.length, 2);
    assert(rows[1].y >= rows[0].bottom);
  }
  if (before.content?.phase === 'intro') {
    await page.locator('.opening-stage').waitFor();
    assert.equal(
      await page.locator('.main-stage .speaker-card,.main-stage .programme-person').count(),
      0,
    );
    assert.equal(await page.locator('.seat').count(), 8);
    await page.locator('#enable-audio').waitFor();
  }
  await page.getByRole('button', { name: '进入全屏展示' }).click();
  await page.waitForFunction(() => !!document.fullscreenElement);
  await page.getByRole('button', { name: '退出全屏展示' }).click();
  await page.waitForFunction(() => !document.fullscreenElement);
  await page.screenshot({ path: dir + '/screen.png' });
  await page.goto(base + '/control');
  await page.locator('#login-panel h1').waitFor();
  assert.equal(await page.locator('#password').count(), 1);
  await page.screenshot({ path: dir + '/control.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/?mode=vote');
  await page.locator('.mobile-wrap').waitFor();
  assert.equal(await page.locator('input[type=password]').count(), 0);
  assert.equal(await page.locator('.award-row').count(), 0);
  const ballotVisible = (await page.locator('#vote-form').count()) === 1;
  if (ballotVisible) {
    assert.equal(await page.locator('input[name="winner"]').count(), 2);
    assert.equal(await page.locator('input[value="DRAW"]').count(), 0);
    assert.equal(await page.locator('input[name="best"]').count(), 8);
    assert.equal(await page.locator('[data-move],.rank-editor').count(), 0);
    assert.equal(await page.locator('input:checked').count(), 0);
  }
  await page.screenshot({ path: dir + '/phone.png' });
  const after = await getState();
  assert.equal(after.round_id, before.round_id);
  assert.equal(after.revision, before.revision);
  assert.equal(after.total_votes, before.total_votes);
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      synthetic_local_browser_pass: true,
      phase: before.content?.phase,
      brand_loaded: true,
      fullscreen_enter_exit: true,
      control_login_visible: true,
      anonymous_vote_no_login: true,
      public_single_choice_controls_checked: ballotVisible,
      local_round_and_votes_unchanged: true,
      page_errors: errors,
    }),
  );
} finally {
  await browser.close();
}
