import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

test('LLM runtime keeps separate normal and fast-decision timeout policies', async () => {
  const llm = await readFile('apps/core/src/jx_core/agent/llm.py', 'utf8');
  const runtime = await readFile('apps/core/src/jx_core/agent/runtime.py', 'utf8');
  assert.match(llm, /LLM_FIRST_TOKEN_TIMEOUT_SECONDS\s*=\s*10\.0/);
  assert.match(llm, /LLM_IDLE_TIMEOUT_SECONDS\s*=\s*10\.0/);
  assert.match(llm, /LLM_CONNECTION_TIMEOUT_SECONDS\s*=\s*10\.0/);
  assert.match(llm, /LLM_FAST_DECISION_TIMEOUT_SECONDS\s*=\s*3\.0/);
  assert.match(llm, /first_token_timeout_seconds:\s*float\s*=\s*LLM_FIRST_TOKEN_TIMEOUT_SECONDS/);
  assert.match(llm, /idle_timeout_seconds:\s*float\s*=\s*LLM_IDLE_TIMEOUT_SECONDS/);
  assert.match(runtime, /connection_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS/);
  assert.match(runtime, /first_token_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS/);
  assert.match(runtime, /idle_timeout_seconds=LLM_FAST_DECISION_TIMEOUT_SECONDS/);
  assert.match(llm, /timeout=httpx\.Timeout\(None, connect=self\._connection_timeout_seconds\)/);
});
