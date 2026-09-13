import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AdminBulkActions } from './admin-bulk-actions';
import { adminApi } from './admin-api';

vi.mock('./admin-api', async (importOriginal) => {
  const original = await importOriginal<typeof import('./admin-api')>();
  return {
    ...original,
    adminApi: {
      ...original.adminApi,
      preflightExport: vi.fn(),
      createExport: vi.fn(),
      exportStatus: vi.fn(),
    },
  };
});

function renderActions(ids = ['match-1', 'match-2']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AdminBulkActions ids={ids} onClear={vi.fn()} onCompleted={vi.fn()} resource="match" />
    </QueryClientProvider>,
  );
}

describe('match bulk export', () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('preflights selected matches and exposes the completed ZIP download', async () => {
    vi.mocked(adminApi.preflightExport).mockResolvedValue({ total_items: 2 });
    vi.mocked(adminApi.createExport).mockResolvedValue({
      id: 'export-1',
      status: 'QUEUED',
      total_items: 2,
      processed_items: 0,
      byte_count: 0,
      sha256: null,
      error_code: null,
      expires_at: null,
      created_at: '2026-08-24T00:00:00Z',
      completed_at: null,
    });
    vi.mocked(adminApi.exportStatus).mockResolvedValue({
      id: 'export-1',
      status: 'SUCCEEDED',
      total_items: 2,
      processed_items: 2,
      byte_count: 1024,
      sha256: 'a'.repeat(64),
      error_code: null,
      expires_at: null,
      created_at: '2026-08-24T00:00:00Z',
      completed_at: null,
    });
    renderActions();

    fireEvent.click(screen.getByRole('checkbox', { name: '包含已授权音频' }));
    fireEvent.click(screen.getByRole('button', { name: '导出比赛数据' }));

    expect(adminApi.preflightExport).toHaveBeenCalledWith(['match-1', 'match-2'], true);
    const dialog = await screen.findByRole('alertdialog', { name: '确认创建研究导出' });
    expect(dialog).toHaveTextContent('将导出 2 场比赛，包含可用音频');
    fireEvent.click(screen.getByRole('button', { name: '创建导出' }));

    expect(adminApi.createExport).toHaveBeenCalledWith(['match-1', 'match-2'], true);
    await waitFor(() => expect(adminApi.exportStatus).toHaveBeenCalledWith('export-1'));
    expect(await screen.findByRole('link', { name: '下载 ZIP' })).toHaveAttribute(
      'href',
      '/api/admin/exports/export-1/download',
    );
  });

  it('submits large deletions in bounded batches', async () => {
    const ids = Array.from({ length: 502 }, (_, index) => `match-${index + 1}`);
    const preflight = vi
      .spyOn(adminApi, 'bulkPreflight')
      .mockImplementation(async (_resource, _operation, batch) => ({ available: batch.length }));
    const bulk = vi
      .spyOn(adminApi, 'bulk')
      .mockResolvedValue({ failed_items: 0, status: 'SUCCEEDED' });
    renderActions(ids);

    fireEvent.click(screen.getByRole('button', { name: '删除比赛' }));
    fireEvent.click(await screen.findByRole('button', { name: '永久删除' }));

    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(2));
    expect(preflight).toHaveBeenNthCalledWith(1, 'match', 'DELETE', ids.slice(0, 500));
    expect(preflight).toHaveBeenNthCalledWith(2, 'match', 'DELETE', ids.slice(500));
    expect(bulk).toHaveBeenNthCalledWith(1, 'match', 'DELETE', ids.slice(0, 500));
    expect(bulk).toHaveBeenNthCalledWith(2, 'match', 'DELETE', ids.slice(500));
  });
});
