import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminModelsPage from './page';

const model = {
  id: '10000000-0000-4000-8000-000000000001',
  name: 'Qwen',
  config_ref: 'qwen-main',
  model_id: 'qwen-plus',
  base_url: 'https://example.test/v1',
  max_concurrency: 20,
  token_per_char: 1,
  generation_params: {},
  capability_schema: {
    temperature: { minimum: 0, maximum: 1.5 },
    top_p: null,
    max_tokens: { minimum: 128, maximum: 8192 },
  },
  status: 'ENABLED',
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AdminModelsPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('admin model capabilities', () => {
  afterEach(cleanup);

  beforeEach(() => {
    requestJson.mockReset();
    requestJson.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/admin/catalog') {
        return Promise.resolve({ models: [model], voices: [], agents: [], topics: [], rules: [] });
      }
      if (path === `/api/admin/catalog/models/${model.id}` && options?.method === 'PATCH') {
        return Promise.resolve(model);
      }
      if (path === `/api/admin/catalog/models/${model.id}/api-key` && options?.method === 'POST') {
        return Promise.resolve({ status: 'rotated', api_key_last4: '7890' });
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
  });

  it('edits a structured capability schema without a raw JSON field', async () => {
    renderPage();
    await screen.findByText('Qwen');
    fireEvent.pointerDown(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(await screen.findByText('编辑配置'));

    expect(screen.getByLabelText('Temperature 最大值')).toHaveValue(1.5);
    expect(screen.getByLabelText('Top P')).not.toBeChecked();
    expect(screen.getByLabelText('Top P 最小值')).toBeDisabled();
    expect(screen.queryByLabelText(/JSON/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Top P'));
    fireEvent.change(screen.getByLabelText('Top P 最小值'), { target: { value: '0.2' } });
    fireEvent.change(screen.getByLabelText('Top P 最大值'), { target: { value: '0.9' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        `/api/admin/catalog/models/${model.id}`,
        expect.objectContaining({
          method: 'PATCH',
          body: expect.stringContaining('"top_p":{"minimum":0.2,"maximum":0.9}'),
        }),
      ),
    );
  });

  it('rotates an API key through a separate action without exposing the old key', async () => {
    renderPage();
    await screen.findByText('Qwen');
    fireEvent.pointerDown(screen.getByRole('button', { name: '更多操作' }));
    fireEvent.click(await screen.findByText('轮换 API Key'));
    expect(screen.getByLabelText('新 API Key')).toHaveValue('');
    fireEvent.change(screen.getByLabelText('新 API Key'), { target: { value: 'secret-7890' } });
    fireEvent.click(screen.getByRole('button', { name: '确认轮换' }));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(`/api/admin/catalog/models/${model.id}/api-key`, {
        method: 'POST',
        body: JSON.stringify({ api_key: 'secret-7890' }),
      }),
    );
  });
});
