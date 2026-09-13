import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminApiRequestLogsPage from './page';

const call = {
  id: '11111111-1111-4111-8111-111111111111', kind: 'LLM_SPEECH', kind_label: 'Agent 发言稿',
  provider: 'OPENAI_COMPATIBLE', operation: 'chat.completions.stream', model: 'qwen', voice: null,
  attempt_no: 1, status: 'SUCCEEDED', status_label: '成功', match_id: null, capture_version: 1,
  captured_at: '2026-08-23T12:00:01Z', source_kind: 'MATCH', source_resource_id: null,
  logical_call_id: '22222222-2222-4222-8222-222222222222', provider_request_id: 'provider-1',
  request_capture_status: 'COMPLETE', response_capture_status: 'COMPLETE', request_original_bytes: 10,
  response_original_bytes: 20, request_stored_bytes: 30, response_stored_bytes: 40,
  request_sha256: 'a'.repeat(64), response_sha256: 'b'.repeat(64), capture_error_code: null,
  speech_id: null, generation_id: null, decision_round_id: null, context_version: 1,
  started_at: '2026-08-23T12:00:00Z', first_result_latency_ms: 100, completed_latency_ms: 200,
  prompt_tokens: 10, completion_tokens: 12, audio_bytes: null, audio_duration_ms: null, error_code: null,
  has_request: true, has_response: true, explanation: { what: '成功', why: '成功', impact: '已处理' },
};

describe('API request logs', () => {
  afterEach(cleanup);
  beforeEach(() => {
    requestJson.mockReset();
    requestJson.mockImplementation((path: string) => path.endsWith(call.id)
      ? Promise.resolve({ ...call, request: { body: { prompt: '输入' } }, response: { body: { text: '输出' } }, content_errors: [], technical: {} })
      : Promise.resolve({ items: [call], page: 1, page_size: 50, total: 1, total_pages: 1 }));
  });

  it('loads metadata first and request response only after opening detail', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AdminApiRequestLogsPage /></QueryClientProvider>);
    const row = await screen.findByRole('button', { name: /Agent 发言稿/ });
    expect(requestJson.mock.calls.some(([path]) => String(path).endsWith(call.id))).toBe(false);
    fireEvent.click(row);
    expect(await screen.findByText('请求 JSON')).toBeVisible();
    expect(await screen.findByText(/输入/)).toBeVisible();
    expect(requestJson.mock.calls.filter(([path]) => String(path).endsWith(call.id))).toHaveLength(1);
  });

  it('keeps refresh controls out of URL and has no export action', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><AdminApiRequestLogsPage /></QueryClientProvider>);
    expect(await screen.findByText('API 请求日志')).toBeVisible();
    fireEvent.change(screen.getByLabelText('自动刷新间隔'), { target: { value: '2000' } });
    expect(screen.getByText('实时观察')).toBeVisible();
    expect(screen.queryByText(/导出/)).not.toBeInTheDocument();
    await waitFor(() => expect(window.location.search).not.toContain('refresh'));
  });
});
