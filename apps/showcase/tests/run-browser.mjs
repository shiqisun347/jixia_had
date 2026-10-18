import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const python =
  process.env.SHOWCASE_PYTHON || fileURLToPath(new URL('../.venv/bin/python', import.meta.url));
const server = spawn(python, ['tests/serve.py'], {
  cwd: root,
  env: { ...process.env, PYTHONPATH: root },
  stdio: ['ignore', 'pipe', 'inherit'],
});
const output = createInterface({ input: server.stdout });

try {
  const line = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Synthetic server did not start')), 15000);
    const finish = (callback, value) => {
      clearTimeout(timer);
      callback(value);
    };
    output.once('line', (value) => finish(resolve, value));
    server.once('error', (error) => finish(reject, error));
    server.once('exit', (code) => finish(reject, new Error(`Synthetic server exited: ${code}`)));
  });
  const { origin } = JSON.parse(line);
  assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(origin + '/health');
      assert.equal(response.status, 200);
      break;
    } catch (error) {
      if (attempt >= 49 || server.exitCode !== null) throw error;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  for (const name of ['single-vote-wake.mjs', 'single-vote-public-smoke.mjs', 'vote-browser.mjs']) {
    const child = spawn(process.execPath, [fileURLToPath(new URL(name, import.meta.url))], {
      cwd: fileURLToPath(new URL('../../../', import.meta.url)),
      env: { ...process.env, SHOWCASE_QA_ORIGIN: origin },
      stdio: 'inherit',
    });
    const [code] = await once(child, 'exit');
    assert.equal(code, 0, `${name} failed`);
  }
} finally {
  output.close();
  if (server.exitCode === null) {
    server.kill('SIGTERM');
    const timer = setTimeout(() => server.kill('SIGKILL'), 5000);
    await once(server, 'exit');
    clearTimeout(timer);
  }
}
