import { execFileSync, spawn } from 'node:child_process';
import { cpSync, mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { projectRoot } from './web-dev.mjs';

const webRoot = join(projectRoot, 'apps/web');
const runtimeRoot = mkdtempSync(join(tmpdir(), 'jx-browser-web-'));
let child;

function cleanup() {
  rmSync(runtimeRoot, { recursive: true, force: true });
}

try {
  execFileSync('corepack', ['pnpm', '--filter', '@jx/web', 'build'], {
    cwd: projectRoot,
    stdio: 'inherit',
  });
  cpSync(join(webRoot, '.next/standalone'), runtimeRoot, { recursive: true });
  const runtimeWebRoot = join(runtimeRoot, 'apps/web');
  mkdirSync(join(runtimeWebRoot, '.next'), { recursive: true });
  cpSync(join(webRoot, '.next/static'), join(runtimeWebRoot, '.next/static'), { recursive: true });
  cpSync(join(webRoot, 'public'), join(runtimeWebRoot, 'public'), { recursive: true });

  child = spawn(process.execPath, [join(runtimeWebRoot, 'server.js')], {
    cwd: runtimeWebRoot,
    env: { ...process.env, HOSTNAME: '127.0.0.1', PORT: '3100' },
    stdio: 'inherit',
  });
  process.on('SIGINT', () => child?.kill('SIGINT'));
  process.on('SIGTERM', () => child?.kill('SIGTERM'));
  child.once('error', () => {
    process.exitCode = 1;
  });
  child.once('exit', (code, signal) => {
    process.exitCode = signal === 'SIGINT' ? 130 : signal === 'SIGTERM' ? 143 : (code ?? 1);
    cleanup();
  });
} catch (error) {
  cleanup();
  console.error(error instanceof Error ? error.message : 'Unable to start browser test server.');
  process.exitCode = 1;
}
