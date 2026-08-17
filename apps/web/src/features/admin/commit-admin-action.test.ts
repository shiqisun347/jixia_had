import { describe, expect, it, vi } from 'vitest';

import { commitAdminAction } from './commit-admin-action';

describe('commitAdminAction', () => {
  it('refreshes after the action commits', async () => {
    const commit = vi.fn().mockResolvedValue(undefined);
    const refresh = vi.fn().mockResolvedValue({ isError: false });
    await expect(commitAdminAction(commit, refresh)).resolves.toBe('refreshed');
  });

  it('keeps a committed action distinct from a failed refresh', async () => {
    const commit = vi.fn().mockResolvedValue(undefined);
    const refresh = vi.fn().mockResolvedValue({ isError: true });
    await expect(commitAdminAction(commit, refresh)).resolves.toBe('refresh_failed');
  });

  it('does not refresh after a failed action', async () => {
    const error = new Error('action failed');
    const commit = vi.fn().mockRejectedValue(error);
    const refresh = vi.fn();
    await expect(commitAdminAction(commit, refresh)).rejects.toBe(error);
    expect(refresh).not.toHaveBeenCalled();
  });
});
