import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { loadEnvFile } from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));

export function loadRootEnvironment(envFile = join(projectRoot, '.env')) {
  if (!existsSync(envFile)) return false;
  loadEnvFile(envFile);
  return true;
}

export function resolveWebPort(rawValue = process.env.WEB_PORT) {
  if (rawValue === undefined || rawValue === '') return 3000;
  if (!/^\d+$/.test(rawValue)) throw new Error('WEB_PORT must be an integer between 1 and 65535.');

  const port = Number(rawValue);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw new Error('WEB_PORT must be an integer between 1 and 65535.');
  }
  return port;
}

export async function main({ envFile = join(projectRoot, '.env'), spawnProcess = spawn } = {}) {
  try {
    loadRootEnvironment(envFile);
    const port = resolveWebPort();
    const child = spawnProcess('next', ['dev', '--port', String(port)], {
      cwd: join(projectRoot, 'apps/web'),
      env: { ...process.env, NEXT_DIST_DIR: '.next-dev' },
      stdio: 'inherit',
    });

    return await new Promise((resolve) => {
      child.once('error', () => {
        console.error('Unable to start the Next.js development server.');
        resolve(1);
      });
      child.once('exit', (code, signal) => {
        if (signal === 'SIGINT') resolve(130);
        else if (signal === 'SIGTERM') resolve(143);
        else resolve(code ?? 1);
      });
    });
  } catch (error) {
    console.error(
      error instanceof Error ? error.message : 'Unable to start the web development server.',
    );
    return 1;
  }
}

const isDirectRun =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isDirectRun) process.exitCode = await main();
