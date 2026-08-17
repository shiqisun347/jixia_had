import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { loadRootEnvironment, main, resolveWebPort } from './web-dev.mjs';

test('WEB_PORT defaults to 3000 and rejects invalid ports', () => {
  assert.equal(resolveWebPort(undefined), 3000);
  assert.equal(resolveWebPort('4312'), 4312);
  assert.throws(() => resolveWebPort('0'), /between 1 and 65535/);
  assert.throws(() => resolveWebPort('65536'), /between 1 and 65535/);
  assert.throws(() => resolveWebPort('3.5'), /between 1 and 65535/);
});

test('the native Node dotenv loader reads WEB_PORT from the root env file', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-env-'));
  const envFile = join(directory, '.env');
  const previousPort = process.env.WEB_PORT;
  writeFileSync(envFile, 'WEB_PORT=4313\n');
  delete process.env.WEB_PORT;

  try {
    assert.equal(loadRootEnvironment(envFile), true);
    assert.equal(resolveWebPort(), 4313);
  } finally {
    if (previousPort === undefined) delete process.env.WEB_PORT;
    else process.env.WEB_PORT = previousPort;
    rmSync(directory, { force: true, recursive: true });
  }
});

test('web dev passes the loaded WEB_PORT to Next.js', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-command-'));
  const envFile = join(directory, '.env');
  const previousPort = process.env.WEB_PORT;
  const calls = [];
  writeFileSync(envFile, 'WEB_PORT=4314\n');
  delete process.env.WEB_PORT;

  try {
    const exitCode = await main({
      envFile,
      spawnProcess(command, args, options) {
        calls.push({ command, args, options });
        const child = new EventEmitter();
        queueMicrotask(() => child.emit('exit', 0, null));
        return child;
      },
    });

    assert.equal(exitCode, 0);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].command, 'next');
    assert.deepEqual(calls[0].args, ['dev', '--port', '4314']);
    assert.equal(calls[0].options.env.WEB_PORT, '4314');
    assert.equal(calls[0].options.env.NEXT_DIST_DIR, '.next-dev');
  } finally {
    if (previousPort === undefined) delete process.env.WEB_PORT;
    else process.env.WEB_PORT = previousPort;
    rmSync(directory, { force: true, recursive: true });
  }
});
