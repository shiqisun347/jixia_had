import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { loadRootEnvironment, projectRoot } from './web-dev.mjs';

const signalExitCodes = {
  SIGINT: 130,
  SIGTERM: 143,
};

export function developmentCommands() {
  return [
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
  ];
}

function processTreeIsAlive(child) {
  if (child.pid === undefined) return false;

  if (process.platform === 'win32') {
    return child.exitCode === null && child.signalCode === null;
  }

  try {
    process.kill(-child.pid, 0);
    return true;
  } catch (error) {
    if (error?.code === 'ESRCH') return false;
    if (error?.code === 'EPERM') return true;
    throw error;
  }
}

function terminateProcessTree(child, signal) {
  if (child.pid === undefined) return;

  try {
    if (process.platform === 'win32') {
      if (processTreeIsAlive(child)) child.kill(signal);
    } else process.kill(-child.pid, signal);
  } catch (error) {
    if (error?.code !== 'ESRCH') throw error;
  }
}

/**
 * Start and supervise a set of long-running development commands.
 *
 * Each child owns a process group on POSIX so stopping pnpm/uv also stops its
 * Next/Uvicorn descendants. The returned controller is intentionally small so
 * the shutdown behavior can be exercised with node:test without starting the
 * real services.
 */
export function startSupervisor(
  commands,
  {
    cwd = projectRoot,
    env = process.env,
    logger = console,
    shutdownTimeoutMs = 5_000,
    stdio = 'inherit',
    spawnProcess = spawn,
  } = {},
) {
  const children = new Map();
  let stopping = false;
  let requestedExitCode = 0;
  let forceKillTimer;
  let shutdownPollTimer;
  let resolved = false;
  let resolveDone;

  const done = new Promise((resolve) => {
    resolveDone = resolve;
  });

  const finishIfStopped = () => {
    if (!stopping || resolved) return;
    if ([...children.values()].some(({ finished }) => !finished)) return;
    if ([...children.values()].some(({ child }) => processTreeIsAlive(child))) return;
    if (forceKillTimer !== undefined) clearTimeout(forceKillTimer);
    if (shutdownPollTimer !== undefined) clearInterval(shutdownPollTimer);
    resolved = true;
    resolveDone(requestedExitCode);
  };

  const stopRemaining = (signal, exitCode) => {
    if (stopping) return;
    stopping = true;
    requestedExitCode = exitCode;

    for (const { child } of children.values()) {
      terminateProcessTree(child, signal);
    }

    forceKillTimer = setTimeout(() => {
      for (const { child } of children.values()) {
        if (processTreeIsAlive(child)) terminateProcessTree(child, 'SIGKILL');
      }
      finishIfStopped();
    }, shutdownTimeoutMs);
    shutdownPollTimer = setInterval(finishIfStopped, 25);
    finishIfStopped();
  };

  const childFinished = (state, { code, signal, error } = {}) => {
    if (state.finished) return;
    state.finished = true;

    if (!stopping) {
      const exitCode = error ? 1 : code && code > 0 ? code : (signalExitCodes[signal] ?? 1);
      if (error) logger.error(`[${state.name}] failed to start.`);
      else if (signal) logger.error(`[${state.name}] exited with ${signal}.`);
      else logger.error(`[${state.name}] exited with code ${code ?? 'unknown'}.`);
      stopRemaining('SIGTERM', exitCode);
    }

    finishIfStopped();
  };

  for (const { name, command, args = [] } of commands) {
    let child;
    try {
      child = spawnProcess(command, args, {
        cwd,
        detached: process.platform !== 'win32',
        env,
        stdio,
      });
    } catch {
      logger.error(`[${name}] failed to start.`);
      stopRemaining('SIGTERM', 1);
      break;
    }

    const state = { name, child, finished: false };
    children.set(name, state);
    child.once('error', (error) => childFinished(state, { error }));
    child.once('exit', (code, signal) => childFinished(state, { code, signal }));
  }

  if (children.size === 0 && !resolved) {
    resolved = true;
    resolveDone(requestedExitCode);
  }

  return {
    children,
    done,
    shutdown(signal = 'SIGTERM') {
      stopRemaining(signal, signalExitCodes[signal] ?? 0);
    },
  };
}

export async function main() {
  try {
    loadRootEnvironment();
  } catch {
    console.error('Unable to load the root .env file.');
    return 1;
  }

  try {
    execFileSync('bash', ['scripts/dev/postgres.sh', 'status'], {
      cwd: projectRoot,
      stdio: 'ignore',
    });
  } catch {
    console.error('PostgreSQL is not ready. Run `pnpm db:start` first.');
    return 1;
  }

  const supervisor = startSupervisor(developmentCommands(), {
    cwd: projectRoot,
    env: process.env,
  });
  const handleSigint = () => supervisor.shutdown('SIGINT');
  const handleSigterm = () => supervisor.shutdown('SIGTERM');
  process.once('SIGINT', handleSigint);
  process.once('SIGTERM', handleSigterm);

  try {
    return await supervisor.done;
  } finally {
    process.removeListener('SIGINT', handleSigint);
    process.removeListener('SIGTERM', handleSigterm);
  }
}

const isDirectRun =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isDirectRun) process.exitCode = await main();
