import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const tempDir = mkdtempSync(join(tmpdir(), 'jx-openapi-'));
const openapiPath = join(tempDir, 'openapi.json');
const generatedPath = join(tempDir, 'openapi.d.ts');
const openapiTypescriptBin = join(
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'openapi-typescript.cmd' : 'openapi-typescript',
);

try {
  const openapi = execFileSync(
    'uv',
    ['run', '--package', 'jx-core', 'python', 'scripts/export-openapi.py'],
    { encoding: 'utf8' },
  );
  writeFileSync(openapiPath, openapi);
  execFileSync(openapiTypescriptBin, [openapiPath, '-o', generatedPath], {
    stdio: 'inherit',
  });

  const actual = readFileSync(generatedPath, 'utf8');
  const expectedPath = 'packages/contracts/src/generated/openapi.d.ts';
  if (process.argv.includes('--write')) {
    mkdirSync('packages/contracts/src/generated', { recursive: true });
    writeFileSync(expectedPath, actual);
    console.log(`Generated ${expectedPath}.`);
  } else if (!existsSync(expectedPath)) {
    console.error(`Missing generated contract: ${expectedPath}. Run pnpm contracts:generate.`);
    process.exitCode = 1;
  } else {
    const expected = readFileSync(expectedPath, 'utf8');
    if (expected !== actual) {
      console.error(`OpenAPI contract drift detected. Regenerate ${expectedPath}.`);
      process.exitCode = 1;
    } else {
      console.log('OpenAPI contract is up to date.');
    }
  }
} finally {
  rmSync(tempDir, { recursive: true, force: true });
}
