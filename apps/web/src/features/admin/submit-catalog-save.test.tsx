import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { submitCatalogSave } from './submit-catalog-save';
import { useAdminSubmit } from './use-admin-submit';

describe('submitCatalogSave', () => {
  it('coalesces synchronous repeated saves', async () => {
    let resolveSave: (() => void) | undefined;
    const save = vi.fn(() => new Promise<void>((resolve) => (resolveSave = resolve)));
    const refresh = vi.fn().mockResolvedValue({ isError: false });
    const { result } = renderHook(() => useAdminSubmit());

    let first: Promise<string> | undefined;
    let second: Promise<string> | undefined;
    act(() => {
      first = submitCatalogSave(result.current.submit, save, refresh);
      second = submitCatalogSave(result.current.submit, save, refresh);
    });
    await expect(second).resolves.toBe('not_started');
    expect(save).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveSave?.();
      await first;
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('distinguishes a committed save from a failed refresh', async () => {
    const { result } = renderHook(() => useAdminSubmit());
    await expect(
      act(() =>
        submitCatalogSave(
          result.current.submit,
          vi.fn().mockResolvedValue(undefined),
          vi.fn().mockResolvedValue({ isError: true }),
        ),
      ),
    ).resolves.toBe('refresh_failed');
  });

  it('does not refresh after a failed save', async () => {
    const failure = new Error('save failed');
    const refresh = vi.fn();
    const { result } = renderHook(() => useAdminSubmit());

    await expect(
      act(() =>
        submitCatalogSave(result.current.submit, vi.fn().mockRejectedValue(failure), refresh),
      ),
    ).rejects.toBe(failure);
    expect(refresh).not.toHaveBeenCalled();
  });
});
