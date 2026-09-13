import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  assertNoCssOptimizerWarnings,
  assertOwnedNextOutput,
  assertHomeBundleBoundary,
  assertQuestionnaireSurface,
  assertStaticArtifactIntegrity,
  assertStandaloneCoreOrigin,
  cleanNextOutput,
  copyStandalonePublic,
  copyStandaloneStatic,
  resolveCoreApiOrigin,
} from './build-web.mjs';

test('production build derives the Core API origin without loading unrelated secrets', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-origin-'));
  const envPath = join(directory, '.env');
  writeFileSync(
    envPath,
    'CORE_HOST=127.0.0.1\nCORE_PORT=8100\nDATABASE_URL=do-not-read\nAPI_KEY=do-not-read\n',
  );
  try {
    assert.equal(resolveCoreApiOrigin({}, envPath), 'http://127.0.0.1:8100');
    assert.equal(
      resolveCoreApiOrigin({ CORE_API_ORIGIN: 'http://127.0.0.1:8200' }, envPath),
      'http://127.0.0.1:8200',
    );
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('invalid or missing Core port uses the safe local fallback', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-origin-invalid-'));
  const envPath = join(directory, '.env');
  writeFileSync(envPath, 'CORE_HOST=127.0.0.1\nCORE_PORT=99999\n');
  try {
    assert.equal(resolveCoreApiOrigin({}, envPath), 'http://127.0.0.1:8000');
    assert.equal(resolveCoreApiOrigin({}, join(directory, 'missing')), 'http://127.0.0.1:8000');
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('cleanNextOutput removes only the owned .next directory', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-build-'));
  const webRoot = join(directory, 'apps/web');
  const outputDirectory = join(webRoot, '.next');
  const siblingFile = join(webRoot, 'keep.txt');
  mkdirSync(outputDirectory, { recursive: true });
  writeFileSync(join(outputDirectory, 'stale.css'), 'stale');
  writeFileSync(siblingFile, 'keep');

  try {
    cleanNextOutput(webRoot);
    assert.equal(existsSync(outputDirectory), false);
    assert.equal(existsSync(siblingFile), true);
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('standalone packaging includes public logo and avatar assets', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-public-'));
  const webRoot = join(directory, 'apps/web');
  mkdirSync(join(webRoot, 'public/assets/avatars'), { recursive: true });
  writeFileSync(join(webRoot, 'public/assets/logo-ui.webp'), 'logo');
  writeFileSync(join(webRoot, 'public/assets/avatars/agent-01.webp'), 'avatar');
  try {
    copyStandalonePublic(webRoot);
    assert.equal(
      existsSync(join(webRoot, '.next/standalone/apps/web/public/assets/logo-ui.webp')),
      true,
    );
    assert.equal(
      existsSync(join(webRoot, '.next/standalone/apps/web/public/assets/avatars/agent-01.webp')),
      true,
    );
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('standalone packaging includes Next static CSS and JS chunks', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-static-'));
  const webRoot = join(directory, 'apps/web');
  mkdirSync(join(webRoot, '.next/static/chunks'), { recursive: true });
  writeFileSync(join(webRoot, '.next/static/chunks/app.js'), 'chunk');
  try {
    copyStandaloneStatic(webRoot);
    assert.equal(
      existsSync(join(webRoot, '.next/standalone/apps/web/.next/static/chunks/app.js')),
      true,
    );
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('standalone build guard rejects a package pointing at the wrong Core port', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-origin-manifest-'));
  const webRoot = join(directory, 'apps/web');
  mkdirSync(join(webRoot, '.next'), { recursive: true });
  writeFileSync(
    join(webRoot, '.next/routes-manifest.json'),
    JSON.stringify({ rewrites: [{ destination: 'http://127.0.0.1:8000/api/:path*' }] }),
  );
  try {
    assert.throws(
      () => assertStandaloneCoreOrigin(webRoot, 'http://127.0.0.1:8100'),
      /expected Core origin/,
    );
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('home bundle guard rejects debate runtime code in initial scripts', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-home-bundle-'));
  const webRoot = join(directory, 'apps/web');
  mkdirSync(join(webRoot, '.next/server/app'), { recursive: true });
  mkdirSync(join(webRoot, '.next/static/chunks'), { recursive: true });
  writeFileSync(
    join(webRoot, '.next/server/app/index.html'),
    '<script src="/_next/static/chunks/home.js"></script>',
  );
  const chunkPath = join(webRoot, '.next/static/chunks/home.js');
  try {
    writeFileSync(chunkPath, 'export const ProtectedDebate = true;');
    assert.throws(() => assertHomeBundleBoundary(webRoot), /debate runtime marker/);
    writeFileSync(chunkPath, 'export const HomePage = true;');
    assert.doesNotThrow(() => assertHomeBundleBoundary(webRoot));
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('production build contains both questionnaire routes and profile links', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-questionnaire-surface-'));
  const webRoot = join(directory, 'apps/web');
  mkdirSync(join(webRoot, '.next/server/chunks'), { recursive: true });
  writeFileSync(
    join(webRoot, '.next/app-path-routes-manifest.json'),
    JSON.stringify({
      '/me/ai-experience/page': '/me/ai-experience',
      '/me/postmatch-surveys/page': '/me/postmatch-surveys',
    }),
  );
  writeFileSync(
    join(webRoot, '.next/server/chunks/me.js'),
    'apps_web_src_features_auth_me-page AI 辩论感受 赛后问卷',
  );
  try {
    assert.doesNotThrow(() => assertQuestionnaireSurface(webRoot));
    writeFileSync(
      join(webRoot, '.next/server/chunks/me.js'),
      'apps_web_src_features_auth_me-page AI 辩论感受',
    );
    assert.throws(() => assertQuestionnaireSurface(webRoot), /post-match survey link/);
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('static artifact guard rejects HTML references to missing JS or CSS', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-static-integrity-'));
  const webRoot = join(directory, 'apps/web');
  mkdirSync(join(webRoot, '.next/server/app'), { recursive: true });
  mkdirSync(join(webRoot, '.next/static/chunks'), { recursive: true });
  writeFileSync(
    join(webRoot, '.next/server/app/debate.html'),
    '<script src="/_next/static/chunks/present.js"></script><link href="/_next/static/missing.css">',
  );
  writeFileSync(join(webRoot, '.next/static/chunks/present.js'), 'ok');
  try {
    assert.throws(() => assertStaticArtifactIntegrity(webRoot), /missing\.css/);
    writeFileSync(join(webRoot, '.next/static/missing.css'), 'ok');
    assert.doesNotThrow(() => assertStaticArtifactIntegrity(webRoot));
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('production origin guard rejects the development Core port', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-production-origin-'));
  const webRoot = join(directory, 'apps/web');
  mkdirSync(join(webRoot, '.next'), { recursive: true });
  writeFileSync(
    join(webRoot, '.next/routes-manifest.json'),
    JSON.stringify({ rewrites: [{ destination: 'http://127.0.0.1:8000/api/:path*' }] }),
  );
  try {
    assert.throws(
      () => assertStandaloneCoreOrigin(webRoot, 'http://127.0.0.1:8100'),
      /expected Core origin/,
    );
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('the cleanup guard rejects paths outside the exact web .next directory', () => {
  const webRoot = '/tmp/jx-web/apps/web';
  assert.throws(
    () => assertOwnedNextOutput(webRoot, '/tmp/jx-web/apps'),
    /unexpected build output/,
  );
  assert.throws(
    () => assertOwnedNextOutput(webRoot, join(webRoot, '.next-old')),
    /unexpected build output/,
  );
});

test('the cleanup guard rejects a symbolic-link .next directory', () => {
  const directory = mkdtempSync(join(tmpdir(), 'jx-web-build-link-'));
  const webRoot = join(directory, 'apps/web');
  const externalDirectory = join(directory, 'external');
  mkdirSync(webRoot, { recursive: true });
  mkdirSync(externalDirectory);
  symlinkSync(externalDirectory, join(webRoot, '.next'));

  try {
    assert.throws(() => cleanNextOutput(webRoot), /symbolic-link/);
    assert.equal(existsSync(externalDirectory), true);
  } finally {
    rmSync(directory, { force: true, recursive: true });
  }
});

test('CSS optimizer warnings fail while normal build output is accepted', () => {
  assert.doesNotThrow(() => assertNoCssOptimizerWarnings('Compiled successfully'));
  assert.throws(
    () => assertNoCssOptimizerWarnings('Warning while optimizing generated CSS\nUnexpected token'),
    /CSS optimizer warning/,
  );
});
