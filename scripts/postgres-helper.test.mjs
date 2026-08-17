import assert from 'node:assert/strict';
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('..', import.meta.url));
const helper = join(projectRoot, 'scripts/dev/postgres.sh');

function fakeDockerEnvironment(scenario) {
  const directory = mkdtempSync(join(tmpdir(), 'jx-fake-docker-'));
  const docker = join(directory, 'docker');
  const log = join(directory, 'docker.log');
  writeFileSync(
    docker,
    `#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"

if [[ "$1" == "info" ]]; then
  [[ "$FAKE_DOCKER_SCENARIO" != "daemon-unavailable" ]]
  exit
fi

if [[ "$1" == "container" && "$2" == "inspect" ]]; then
  [[ "$FAKE_DOCKER_SCENARIO" != "foreign-volume" ]]
  exit
fi

if [[ "$1" == "volume" && "$2" == "inspect" ]]; then
  if [[ "\${3:-}" == "--format" ]]; then
    if [[ "$FAKE_DOCKER_SCENARIO" == "foreign-volume" ]]; then
      printf '%s\\n' 'foreign-owner'
    else
      printf '%s\\n' 'jx-postgres-dev-v1'
    fi
  fi
  exit 0
fi

if [[ "$1" == "inspect" ]]; then
  case "$*" in
    *Config.Labels*)
      if [[ "$FAKE_DOCKER_SCENARIO" == "foreign-container" ]]; then
        printf '%s\\n' 'foreign-owner'
      else
        printf '%s\\n' 'jx-postgres-dev-v1'
      fi
      ;;
    *Config.Image*)
      if [[ "$FAKE_DOCKER_SCENARIO" == "wrong-image" ]]; then
        printf '%s\\n' 'foreign-image'
      else
        printf '%s\\n' 'postgres:16.14-alpine3.24@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777'
      fi
      ;;
    *Mounts*)
      if [[ "$FAKE_DOCKER_SCENARIO" == "wrong-mount" ]]; then
        printf '%s\\n' 'volume|some-other-volume|/var/lib/postgresql/data'
      else
        printf '%s\\n' 'volume|jx-postgres-dev-data|/var/lib/postgresql/data'
      fi
      ;;
    *) printf '%s\\n' 'running' ;;
  esac
  exit 0
fi

exit 0
`,
  );
  chmodSync(docker, 0o755);
  return {
    directory,
    log,
    env: {
      ...process.env,
      FAKE_DOCKER_LOG: log,
      FAKE_DOCKER_SCENARIO: scenario,
      PATH: `${directory}:${process.env.PATH ?? ''}`,
    },
  };
}

test('PostgreSQL helper is syntactically valid and pins its owned resources', () => {
  const syntax = spawnSync('bash', ['-n', helper], { encoding: 'utf8' });
  assert.equal(syntax.status, 0, syntax.stderr);

  const source = readFileSync(helper, 'utf8');
  assert.match(
    source,
    /postgres:16\.14-alpine3\.24@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777/,
  );
  assert.match(source, /io\.jixia-debate\.foundation\.postgres/);
});

test('PostgreSQL helper fails clearly when the Docker daemon is unavailable', () => {
  const fake = fakeDockerEnvironment('daemon-unavailable');
  try {
    const result = spawnSync('bash', [helper, 'stop'], {
      encoding: 'utf8',
      env: fake.env,
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Docker daemon is unavailable/);
    assert.doesNotMatch(readFileSync(fake.log, 'utf8'), /container inspect/);
  } finally {
    rmSync(fake.directory, { force: true, recursive: true });
  }
});

test('PostgreSQL helper refuses an unowned same-name container before stop', () => {
  const fake = fakeDockerEnvironment('foreign-container');
  try {
    const result = spawnSync('bash', [helper, 'stop'], {
      encoding: 'utf8',
      env: fake.env,
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Refusing to operate/);
    assert.doesNotMatch(readFileSync(fake.log, 'utf8'), /^stop jx-postgres-dev$/m);
  } finally {
    rmSync(fake.directory, { force: true, recursive: true });
  }
});

test('PostgreSQL helper refuses an unowned same-name volume before start', () => {
  const fake = fakeDockerEnvironment('foreign-volume');
  try {
    const result = spawnSync('bash', [helper, 'start'], {
      encoding: 'utf8',
      env: fake.env,
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Refusing to operate/);
    assert.doesNotMatch(readFileSync(fake.log, 'utf8'), /^run /m);
  } finally {
    rmSync(fake.directory, { force: true, recursive: true });
  }
});

for (const scenario of ['wrong-image', 'wrong-mount']) {
  test(`PostgreSQL helper refuses a same-name container with ${scenario}`, () => {
    const fake = fakeDockerEnvironment(scenario);
    try {
      const result = spawnSync('bash', [helper, 'logs'], {
        encoding: 'utf8',
        env: fake.env,
      });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /Refusing to operate/);
      assert.doesNotMatch(readFileSync(fake.log, 'utf8'), /^logs /m);
    } finally {
      rmSync(fake.directory, { force: true, recursive: true });
    }
  });
}
