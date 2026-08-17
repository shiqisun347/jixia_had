import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));

vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminAgentsPage from './page';

const catalog = {
  models: [
    {
      id: '11111111-1111-4111-8111-111111111111',
      name: 'Qwen',
      status: 'ENABLED',
      model_id: 'qwen',
    },
  ],
  voices: [
    {
      id: '22222222-2222-4222-8222-222222222222',
      name: '龙安灵希',
      kind: 'AGENT',
      provider_voice: 'voice-1',
      rate: 1,
      chars_per_second: 4,
      avatar_key: 'agent-07',
      status: 'ENABLED',
    },
  ],
  agents: [
    {
      id: '33333333-3333-4333-8333-333333333333',
      name: '乾元',
      model_profile_id: '11111111-1111-4111-8111-111111111111',
      voice_profile_id: '22222222-2222-4222-8222-222222222222',
      system_prompt: '系统提示',
      debater_prompt: '辩手提示',
      generation_params: { temperature: 0.8 },
      avatar_key: 'agent-08',
      status: 'ENABLED',
    },
  ],
  topics: [],
  rules: [],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AdminAgentsPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('admin Agent catalog', () => {
  afterEach(cleanup);

  beforeEach(() => {
    requestJson.mockReset();
    requestJson.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/admin/catalog') return Promise.resolve(catalog);
      if (path === '/api/admin/catalog/agents' && options?.method === 'POST') {
        return Promise.reject(new Error('Agent 名称已存在，请换一个名称'));
      }
      if (
        path === '/api/admin/catalog/agents/33333333-3333-4333-8333-333333333333' &&
        options?.method === 'PATCH'
      ) {
        return Promise.resolve({ ...catalog.agents[0], name: '乾元·改' });
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
  });

  it('shows duplicate create failures as a floating toast', async () => {
    renderPage();
    await screen.findByText('乾元');
    fireEvent.click(screen.getByRole('button', { name: '创建 Agent' }));
    fireEvent.change(screen.getByLabelText('Agent 名称'), { target: { value: '乾元' } });
    fireEvent.change(screen.getByLabelText('LLM 模型'), {
      target: { value: '11111111-1111-4111-8111-111111111111' },
    });
    fireEvent.change(screen.getByLabelText('TTS 音色'), {
      target: { value: '22222222-2222-4222-8222-222222222222' },
    });
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
    expect(screen.getByText('头像由 TTS 音色决定')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Agent 名称已存在');
    expect(requestJson).toHaveBeenCalledWith(
      '/api/admin/catalog/agents',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('prefills an Agent and persists edits through PATCH', async () => {
    renderPage();
    fireEvent.pointerDown(await screen.findByRole('button', { name: '更多操作' }));
    fireEvent.click(await screen.findByText('编辑配置'));

    expect(screen.getByLabelText('Agent 名称')).toHaveValue('乾元');
    expect(screen.getByLabelText('生成温度')).toHaveValue(0.8);
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Agent 名称'), { target: { value: '乾元·改' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        '/api/admin/catalog/agents/33333333-3333-4333-8333-333333333333',
        expect.objectContaining({
          method: 'PATCH',
          body: expect.stringContaining('乾元·改'),
        }),
      ),
    );
    const requestBody = JSON.parse(
      requestJson.mock.calls.find(
        ([path, options]) =>
          path === '/api/admin/catalog/agents/33333333-3333-4333-8333-333333333333' &&
          options?.method === 'PATCH',
      )?.[1]?.body as string,
    ) as Record<string, unknown>;
    expect(requestBody).not.toHaveProperty('avatar_key');
    expect(await screen.findByRole('status')).toHaveTextContent('Agent 配置已更新');
  });
});
