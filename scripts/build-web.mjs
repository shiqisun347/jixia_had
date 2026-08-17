import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
export const projectRoot = resolve(scriptDirectory, '..');
export const defaultWebRoot = join(projectRoot, 'apps/web');

function envValue(source, key) {
  const match = source.match(new RegExp(`^${key}=([^\\r\\n#]*)`, 'm'));
  return match?.[1]?.trim();
}

export function resolveCoreApiOrigin(
  environment = process.env,
  envPath = join(projectRoot, '.env'),
) {
  if (environment.CORE_API_ORIGIN?.trim()) return environment.CORE_API_ORIGIN.trim();
  let source = '';
  try {
    source = readFileSync(envPath, 'utf8');
  } catch {
    return 'http://127.0.0.1:8000';
  }
  const host = envValue(source, 'CORE_HOST') || '127.0.0.1';
  const port = envValue(source, 'CORE_PORT');
  if (!port || !/^\d{1,5}$/.test(port) || Number(port) > 65535 || Number(port) < 1) {
    return 'http://127.0.0.1:8000';
  }
  return `http://${host}:${port}`;
}

export function assertOwnedNextOutput(webRoot, outputDirectory) {
  const resolvedWebRoot = resolve(webRoot);
  const resolvedOutput = resolve(outputDirectory);
  const expectedOutput = join(resolvedWebRoot, '.next');

  if (resolvedOutput !== expectedOutput) {
    throw new Error(`Refusing to clean unexpected build output: ${resolvedOutput}`);
  }
  if (existsSync(resolvedOutput) && lstatSync(resolvedOutput).isSymbolicLink()) {
    throw new Error('Refusing to clean a symbolic-link .next directory.');
  }
}

export function cleanNextOutput(webRoot, outputDirectory = join(webRoot, '.next')) {
  assertOwnedNextOutput(webRoot, outputDirectory);
  rmSync(outputDirectory, { force: true, recursive: true });
}

export function assertNoCssOptimizerWarnings(output) {
  if (/warning while optimizing generated css/i.test(output)) {
    throw new Error('Next.js emitted a CSS optimizer warning; the production build is rejected.');
  }
}

export function copyStandalonePublic(webRoot) {
  const standalonePublic = join(webRoot, '.next', 'standalone', 'apps', 'web', 'public');
  mkdirSync(standalonePublic, { recursive: true });
  cpSync(join(webRoot, 'public'), standalonePublic, { recursive: true });
}

export function copyStandaloneStatic(webRoot) {
  const standaloneStatic = join(webRoot, '.next', 'standalone', 'apps', 'web', '.next', 'static');
  mkdirSync(standaloneStatic, { recursive: true });
  cpSync(join(webRoot, '.next', 'static'), standaloneStatic, { recursive: true });
}

export function assertStandaloneCoreOrigin(webRoot, expectedOrigin) {
  const manifestPath = join(webRoot, '.next', 'routes-manifest.json');
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const rewrites = [
    ...(manifest.rewrites?.beforeFiles ?? []),
    ...(manifest.rewrites?.afterFiles ?? []),
    ...(manifest.rewrites?.fallback ?? []),
    ...(Array.isArray(manifest.rewrites) ? manifest.rewrites : []),
  ];
  if (!rewrites.some((rewrite) => rewrite.destination === `${expectedOrigin}/api/:path*`)) {
    throw new Error(
      `Standalone Web build does not target the expected Core origin: ${expectedOrigin}`,
    );
  }
}

export function assertHomeBundleBoundary(webRoot) {
  const homeHtml = readFileSync(join(webRoot, '.next', 'server', 'app', 'index.html'), 'utf8');
  const chunkUrls = [
    ...homeHtml.matchAll(/(?:src|href)="\/_next\/static\/([^"?]+\.js)(?:\?[^\"]*)?"/g),
  ].map((match) => match[1]);
  const debateMarkers = ['ProtectedDebate', 'free-debate-clock'];
  for (const chunkUrl of new Set(chunkUrls)) {
    const source = readFileSync(join(webRoot, '.next', 'static', chunkUrl), 'utf8');
    const marker = debateMarkers.find((candidate) => source.includes(candidate));
    if (marker) {
      throw new Error(`Home bundle unexpectedly includes debate runtime marker: ${marker}`);
    }
  }
}

export function buildWeb({ webRoot = defaultWebRoot, spawnBuild = spawnSync } = {}) {
  cleanNextOutput(webRoot);
  const requireFromWeb = createRequire(join(webRoot, 'package.json'));
  const nextCli = requireFromWeb.resolve('next/dist/bin/next');
  const result = spawnBuild(process.execPath, [nextCli, 'build'], {
    cwd: webRoot,
    encoding: 'utf8',
    env: {
      ...process.env,
      CORE_API_ORIGIN: resolveCoreApiOrigin(),
      NEXT_DIST_DIR: '.next',
    },
    maxBuffer: 100 * 1024 * 1024,
  });

  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `Next.js production build failed with exit code ${result.status ?? 'unknown'}.`,
    );
  }

  assertNoCssOptimizerWarnings(`${result.stdout ?? ''}\n${result.stderr ?? ''}`);
  assertStandaloneCoreOrigin(
    webRoot,
    resolveCoreApiOrigin({
      ...process.env,
      CORE_API_ORIGIN: resolveCoreApiOrigin(),
    }),
  );
  assertHomeBundleBoundary(webRoot);

  // Next standalone intentionally omits the public directory. The deployed
  // server must still carry the brand logo and identity avatars referenced by
  // absolute /assets URLs, otherwise every runtime page loses them.
  copyStandalonePublic(webRoot);
  // Next standalone also keeps build CSS/JS outside the runtime directory.
  // Copy it in so starting server.js directly cannot serve HTML with 404 chunks.
  copyStandaloneStatic(webRoot);
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : undefined;
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    buildWeb();
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Unable to build the Web application.');
    process.exitCode = 1;
  }
}
