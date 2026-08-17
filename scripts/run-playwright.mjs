import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { playwrightEnvironment } from './playwright-environment.mjs';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const webRoot = join(resolve(scriptDirectory, '..'), 'apps/web');
const requireFromWeb = createRequire(join(webRoot, 'package.json'));
const playwrightCli = requireFromWeb.resolve('@playwright/test/cli');
const result = spawnSync(process.execPath, [playwrightCli, ...process.argv.slice(2)], {
  cwd: webRoot,
  env: playwrightEnvironment(),
  stdio: 'inherit',
});

if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
