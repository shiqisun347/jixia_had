import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminSettingsPage from './page';

const settings = {
  log_retention_days: 30,
  debug_enabled: false,
  debug_expires_at: null,
  max_upload_bytes: 2 * 1024 * 1024,
};

describe('admin system settings', () => {
  afterEach(cleanup);

  beforeEach(() => {
    requestJson.mockReset();
    requestJson.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/api/admin/storage') {
        return Promise.resolve({
          total_bytes: 100,
          used_bytes: 20,
          free_bytes: 80,
          used_ratio: 0.2,
          estimated_days_remaining: null,
          automatic_backup: false,
        });
      }
      if (path === '/api/admin/settings' && !options) return Promise.resolve(settings);
      if (path === '/api/admin/settings' && options?.method === 'PATCH') {
        return Promise.resolve(settings);
      }
      return Promise.reject(new Error(`unexpected request: ${path}`));
    });
  });

  it('saves whitelisted runtime controls and gives DEBUG an expiry', async () => {
    render(
      <ToastProvider>
        <AdminSettingsPage />
      </ToastProvider>,
    );
    await screen.findByDisplayValue('30');
    fireEvent.change(screen.getByLabelText('运行日志保留天数'), { target: { value: '45' } });
    fireEvent.click(screen.getByLabelText('临时记录 DEBUG 日志'));
    expect(screen.getByLabelText('DEBUG 自动关闭时间')).toHaveValue();
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        '/api/admin/settings',
        expect.objectContaining({
          method: 'PATCH',
          body: expect.stringContaining('"log_retention_days":45'),
        }),
      ),
    );
    const patchCall = requestJson.mock.calls.find(
      ([path, options]) => path === '/api/admin/settings' && options?.method === 'PATCH',
    );
    expect(JSON.parse(patchCall?.[1]?.body as string)).toEqual(
      expect.objectContaining({
        debug_enabled: true,
        max_upload_bytes: 2 * 1024 * 1024,
      }),
    );
  });
});
