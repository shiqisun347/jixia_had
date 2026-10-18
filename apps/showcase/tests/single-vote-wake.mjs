import { chromium } from '../../web/node_modules/@playwright/test/index.mjs';
import assert from 'node:assert/strict';
const base = process.env.SHOWCASE_QA_ORIGIN;
assert.ok(/^http:\/\/127\.0\.0\.1:\d+$/.test(base || ''), 'Owned local test origin required.');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const errors = [];
context.on('page', (p) => p.on('pageerror', (e) => errors.push(e.message)));
const req = async (path, data, method = 'POST') => {
  const r = await context.request.fetch(base + path, {
    method,
    headers: { Origin: base },
    ...(data === undefined ? {} : { data }),
  });
  assert.equal(r.status(), 200, await r.text());
  return r.json();
};
const read = () => req('/api/admin', undefined, 'GET');
async function until(fn, label) {
  const end = Date.now() + 15000;
  while (Date.now() < end) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw Error(label);
}
await context.addInitScript(() => {
  // Observe native WebAudio calls without replacing buffers or rendering sound.
  window.audioEvidence = { starts: [], stops: 0, ended: 0, active: 0, maxActive: 0, contexts: [] };
  const Native = window.AudioContext;
  window.AudioContext = class extends Native {
    constructor(...args) {
      super(...args);
      window.audioEvidence.contexts.push(this);
    }
    createBufferSource() {
      const node = super.createBufferSource();
      const start = node.start.bind(node),
        stop = node.stop.bind(node),
        connect = node.connect.bind(node);
      const analyser = this.createAnalyser();
      analyser.fftSize = 1024;
      const samples = new Float32Array(analyser.fftSize);
      node.connect = (destination, ...args) => {
        if (destination === this.destination) {
          connect(analyser);
          analyser.connect(destination);
          return destination;
        }
        return connect(destination, ...args);
      };
      let record = null,
        meter = null;
      let active = false;
      node.start = (...args) => {
        start(...args);
        active = true;
        window.audioEvidence.active++;
        window.audioEvidence.maxActive = Math.max(
          window.audioEvidence.maxActive,
          window.audioEvidence.active,
        );
        record = {
          duration: node.buffer?.duration,
          channels: node.buffer?.numberOfChannels,
          samples: node.buffer?.length,
          maxRms: 0,
        };
        window.audioEvidence.starts.push(record);
        meter = setInterval(() => {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(
            samples.reduce((sum, value) => sum + value * value, 0) / samples.length,
          );
          record.maxRms = Math.max(record.maxRms, rms);
        }, 20);
      };
      node.stop = (...args) => {
        stop(...args);
        clearInterval(meter);
        if (active) {
          active = false;
          window.audioEvidence.active--;
          window.audioEvidence.stops++;
        }
      };
      node.addEventListener('ended', () => {
        clearInterval(meter);
        if (active) {
          active = false;
          window.audioEvidence.active--;
          window.audioEvidence.ended++;
        }
      });
      return node;
    }
  };
  // A presentation computer with a skewed wall clock must still play valid commands.
  const nativeNow = Date.now;
  Date.now = () => nativeNow() + 3600000;
});

await context.addInitScript(() => {
  const nativeInterval = window.setInterval.bind(window);
  window.setInterval = (fn, delay, ...args) => {
    const id = nativeInterval(fn, delay, ...args);
    if (delay === 1200) window.displayPollTimer = id;
    return id;
  };
});
try {
  await req('/api/login', { password: 'synthetic-test-password' });
  let s = await read();
  s = await req('/api/admin/new-round', { revision: s.revision });
  s.content.phase = 'intro';
  s = await req('/api/admin/draft', { revision: s.revision, content: s.content }, 'PUT');
  await req('/api/admin/publish', { revision: s.revision });
  const admin = await context.newPage();
  await admin.goto(base + '/control');
  await admin.locator('#workspace').waitFor({ state: 'visible' });
  const display = await context.newPage();
  await display.goto(base + '/');
  await display.locator('#enable-audio').waitFor();
  await admin.locator('[data-greeting="aff-2"]').click();
  await until(
    async () => (await admin.locator('#playback-feedback').innerText()).includes('声音尚未启用'),
    'actionable unready feedback',
  );
  assert.equal((await read()).playback, null, 'unready click queues no command');
  await display.locator('#enable-audio').click();
  await until(async () => (await read()).audio_displays.some((d) => d.ready), 'enabled');
  const displayId = (await read()).audio_displays.find((d) => d.ready).display_id;
  await display.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await until(async () => (await read()).audio_displays.some((d) => d.ready), 'hidden ready');
  assert.equal((await read()).audio_displays.find((d) => d.ready).display_id, displayId);
  await until(
    async () => (await admin.locator('#audio-control-state').innerText()).includes('声音已就绪'),
    'no foreground warning',
  );
  await display.evaluate(() => {
    assertTimer();
    function assertTimer() {
      if (!window.displayPollTimer) throw Error('poll timer not recorded');
    }
    clearInterval(window.displayPollTimer);
  });
  // The current production SSE heartbeat keeps a hidden connection alive even
  // after interval polling stops; it must not be treated as an expired lease.
  await new Promise((resolve) => setTimeout(resolve, 7000));
  assert.ok(
    (await read()).audio_displays.some((d) => d.ready),
    'SSE keeps hidden display ready',
  );
  const issuedAt = Date.now();
  await admin.locator('[data-greeting="aff-2"]').click();
  await until(
    async () => (await display.evaluate(() => window.audioEvidence.starts.length)) === 1,
    'BroadcastChannel wakes canonical fetch and playback',
  );
  await until(
    async () => (await read()).audio_displays.some((d) => d.status === 'playing'),
    'actual playing acknowledgment',
  );
  const wakeMs = Date.now() - issuedAt;
  assert.equal((await read()).audio_displays.find((d) => d.ready).display_id, displayId);
  const command = (await read()).playback.id;
  await until(
    async () => (await read()).audio_displays.some((d) => d.status === 'ended'),
    'natural completion without poll',
  );
  const voice = await display.evaluate(() => window.audioEvidence.starts[0]);
  assert.ok(voice.maxRms > 0.0001);
  await admin.evaluate(() => {
    window.testWake = new BroadcastChannel('jx-showcase-audio');
    window.testWake.postMessage({ action: 'play', participant_id: 'neg-4', id: 999999 });
    window.testWake.postMessage('refresh');
  });
  await new Promise((r) => setTimeout(r, 500));
  assert.equal(
    await display.evaluate(() => window.audioEvidence.starts.length),
    1,
    'forged wake cannot play',
  );
  assert.equal((await read()).playback.id, command);
  await display.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await new Promise((r) => setTimeout(r, 400));
  assert.equal(
    await display.evaluate(() => window.audioEvidence.starts.length),
    1,
    'show does not replay',
  );
  await display.close();
  await until(async () => (await read()).audio_displays.length === 0, 'close releases');
  assert.deepEqual(errors, []);
  const report = {
    same_origin_wake_with_interval_polling_disabled: true,
    sse_keeps_hidden_connection_ready: true,
    controlled_hidden_event: true,
    native_audio: true,
    wake_ms: wakeMs,
    voice,
    unready_feedback_without_command: true,
    forged_channel_cannot_play: true,
    no_replay: true,
    close_releases: true,
    page_errors: errors,
  };
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
