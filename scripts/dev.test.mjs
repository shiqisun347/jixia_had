import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import { developmentCommands, startSupervisor } from './dev.mjs';

const quietLogger = {
  error() {},
};

test('development commands use the validated console entry points', () => {
  assert.deepEqual(developmentCommands(), [
    {
      name: 'web',
      command: 'pnpm',
      args: ['--filter', '@jx/web', 'dev'],
    },
    {
      name: 'core',
      command: 'uv',
      args: ['run', '--package', 'jx-core', 'jx-core'],
    },
    {
      name: 'jobs',
      command: 'uv',
      args: ['run', '--package', 'jx-jobs', 'jx-jobs'],
    },
  ]);
});

test('an unexpected child failure stops its siblings and reaches the root exit code', async () => {
  const persistentProgram = `
    process.on('SIGTERM', () => process.exit(0));
    setInterval(() => {}, 1_000);
  `;
  const supervisor = startSupervisor(
    [
      { name: 'first', command: process.execPath, args: ['-e', persistentProgram] },
      {
        name: 'failing',
        command: process.execPath,
        args: ['-e', 'setTimeout(() => process.exit(7), 80)'],
      },
      { name: 'third', command: process.execPath, args: ['-e', persistentProgram] },
    ],
    {
      logger: quietLogger,
      shutdownTimeoutMs: 1_000,
      stdio: 'ignore',
    },
  );

  assert.equal(await supervisor.done, 7);
  assert.ok([...supervisor.children.values()].every(({ finished }) => finished));
});

test('a clean child exit is still unexpected for a long-running dev stack', async () => {
  const supervisor = startSupervisor(
    [{ name: 'short-lived', command: process.execPath, args: ['-e', 'process.exit(0)'] }],
    {
      logger: quietLogger,
      shutdownTimeoutMs: 1_000,
      stdio: 'ignore',
    },
  );

  assert.equal(await supervisor.done, 1);
});

test('a synchronous spawn failure resolves without leaving shutdown timers behind', async () => {
  const supervisor = startSupervisor([{ name: 'missing', command: 'missing-command' }], {
    logger: quietLogger,
    shutdownTimeoutMs: 10_000,
    spawnProcess() {
      throw new Error('spawn failed');
    },
    stdio: 'ignore',
  });

  assert.equal(await supervisor.done, 1);
});

test(
  'a leader that exits cannot leave a signal-resistant grandchild behind',
  { skip: process.platform === 'win32' },
  async () => {
    const directory = mkdtempSync(join(tmpdir(), 'jx-dev-supervisor-'));
    const pidFile = join(directory, 'grandchild.pid');
    const readyFile = join(directory, 'grandchild.ready');
    let grandchildPid;
    const grandchildProgram = `
      const { writeFileSync } = require('node:fs');
      process.on('SIGTERM', () => {});
      writeFileSync(process.argv[1], 'ready');
      setInterval(() => {}, 1_000);
    `;
    const parentProgram = `
      const { spawn } = require('node:child_process');
      const { existsSync, writeFileSync } = require('node:fs');
      const grandchild = spawn(
        process.execPath,
        ['-e', ${JSON.stringify(grandchildProgram)}, process.argv[2]],
        { stdio: 'ignore' },
      );
      grandchild.unref();
      writeFileSync(process.argv[1], String(grandchild.pid));
      const deadline = Date.now() + 2_000;
      const readinessPoll = setInterval(() => {
        if (existsSync(process.argv[2])) process.exit(0);
        if (Date.now() >= deadline) process.exit(2);
      }, 5);
    `;

    try {
      const supervisor = startSupervisor(
        [
          {
            name: 'leader',
            command: process.execPath,
            args: ['-e', parentProgram, pidFile, readyFile],
          },
        ],
        {
          logger: quietLogger,
          shutdownTimeoutMs: 100,
          stdio: 'ignore',
        },
      );

      assert.equal(await supervisor.done, 1);
      grandchildPid = Number.parseInt(readFileSync(pidFile, 'utf8'), 10);
      assert.throws(() => process.kill(grandchildPid, 0), { code: 'ESRCH' });
    } finally {
      if (Number.isInteger(grandchildPid)) {
        try {
          process.kill(grandchildPid, 'SIGKILL');
        } catch (error) {
          if (error?.code !== 'ESRCH') throw error;
        }
      }
      rmSync(directory, { force: true, recursive: true });
    }
  },
);
