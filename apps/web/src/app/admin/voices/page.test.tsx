import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';
import type { VoiceRow } from '@/features/admin/admin-types';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminVoicesPage from './page';

const initialVoice: VoiceRow = {
  id: '10000000-0000-4000-8000-000000000001',
  name: '测试音色',
  kind: 'AGENT',
  provider_voice: 'test-voice',
  rate: 1,
  chars_per_second: 4.5,
  playback_gain: 1,
  avatar_key: 'agent-07',
  status: 'ENABLED',
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AdminVoicesPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('Agent voice avatar choices', () => {
  afterEach(cleanup);

  beforeEach(() => {
    let voice = { ...initialVoice };
    requestJson.mockReset();
    requestJson.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/admin/catalog') {
        return Promise.resolve({ models: [], voices: [voice], agents: [], topics: [], rules: [] });
      }
      if (path === `/api/admin/catalog/voices/${voice.id}` && options?.method === 'PATCH') {
        voice = { ...voice, ...JSON.parse(String(options.body)) };
        return Promise.resolve(voice);
      }
      if (path === '/api/admin/catalog/voices' && options?.method === 'POST') {
        return Promise.resolve({ ...voice, ...JSON.parse(String(options.body)) });
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
  });

  it('offers all 28 presets and preserves a human preset after saving and refreshing', async () => {
    const view = renderPage();
    await screen.findByText('测试音色');
    fireEvent.pointerDown(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(await screen.findByText('编辑音色'));

    expect(screen.getAllByRole('radio')).toHaveLength(28);
    expect(screen.getByRole('radio', { name: 'Agent 头像 agent-07' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Agent 头像 agent-12' })).toBeVisible();
    fireEvent.click(screen.getByRole('radio', { name: 'Agent 头像 human-16' }));
    fireEvent.click(screen.getByRole('button', { name: '保存音色' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(requestJson).toHaveBeenCalledWith(`/api/admin/catalog/voices/${initialVoice.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        name: initialVoice.name,
        kind: 'AGENT',
        provider_voice: initialVoice.provider_voice,
        rate: 1,
        chars_per_second: 4.5,
        playback_gain: 1,
        avatar_key: 'human-16',
      }),
    });
    expect(view.container.querySelector('img')?.getAttribute('src')).toContain('human-16.webp');
    fireEvent.pointerDown(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(await screen.findByText('编辑音色'));
    expect(screen.getByRole('radio', { name: 'Agent 头像 human-16' })).toBeChecked();
  });

  it('keeps host voices without an avatar choice or submitted avatar', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: '添加音色' }));
    fireEvent.change(screen.getByLabelText('类型'), { target: { value: 'HOST' } });
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '主持音色' } });
    fireEvent.change(screen.getByLabelText('供应商音色 ID'), { target: { value: 'host-test' } });
    fireEvent.click(screen.getByRole('button', { name: '保存音色' }));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith('/api/admin/catalog/voices', {
        method: 'POST',
        body: JSON.stringify({
          name: '主持音色',
          kind: 'HOST',
          provider_voice: 'host-test',
          rate: 1,
          chars_per_second: 4.5,
          playback_gain: 1,
          avatar_key: null,
        }),
      }),
    );
  });
});
