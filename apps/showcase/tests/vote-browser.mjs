import { chromium } from '../../web/node_modules/@playwright/test/index.mjs';
import assert from 'node:assert/strict';

const base = process.env.SHOWCASE_QA_ORIGIN;
assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(base || ''), 'Owned local test origin required.');
const browser = await chromium.launch({ headless: true });
try {
  const admin = await browser.newContext();
  const request = async (path, data, method = 'POST') => {
    const response = await admin.request.fetch(base + path, {
      method,
      headers: { Origin: base },
      ...(data === undefined ? {} : { data }),
    });
    assert.equal(response.status(), 200, path);
    return response.json();
  };
  await request('/api/login', { password: 'synthetic-test-password' });
  let state = await request('/api/admin', undefined, 'GET');
  state = await request('/api/admin/new-round', { revision: state.revision });
  state.content.phase = 'vote';
  state = await request(
    '/api/admin/draft',
    { revision: state.revision, content: state.content },
    'PUT',
  );
  await request('/api/admin/publish', { revision: state.revision });
  const display = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await display.goto(base);
  await display.locator('.vote-qr').waitFor();
  await display.waitForFunction(async () => (await (await fetch('/api/state')).json()).collecting);
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto(base + '/?mode=vote');
  await phone.locator('#vote-form').waitFor();
  assert.equal(await phone.locator('input[name="winner"]').count(), 2);
  assert.equal(await phone.locator('input[name="best"]').count(), 8);
  assert.equal(await phone.locator('input:checked').count(), 0);
  await phone.locator('input[name="winner"][value="AFFIRMATIVE"]').check();
  await phone.locator('input[name="best"][value="aff-1"]').check();
  await phone.getByRole('button', { name: '提交投票' }).click();
  await phone.getByRole('heading', { name: '投票已提交' }).waitFor();
  const publicState = await (await phone.request.get(base + '/api/state')).json();
  assert.equal(publicState.total_votes, 1);
  assert.equal(publicState.has_voted, true);
  const duplicate = await phone.request.post(base + '/api/vote', {
    headers: { Origin: base },
    data: { round_id: publicState.round_id, winner: 'AFFIRMATIVE', best: 'aff-1' },
  });
  assert.equal(duplicate.status(), 409);
  await phone.reload();
  await phone.getByRole('heading', { name: '投票已提交' }).waitFor();
  assert.equal(await phone.locator('#vote-form').count(), 0);
  console.log(
    JSON.stringify({ local_mobile_vote: true, exact_single_best: true, duplicate_rejected: true }),
  );
} finally {
  await browser.close();
}
