import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useAdminSubmit } from './use-admin-submit';

describe('useAdminSubmit', () => {
  it('coalesces repeated submissions until the request settles', async () => {
    let resolveRequest: (() => void) | undefined;
    const request = vi.fn(() => new Promise<void>((resolve) => (resolveRequest = resolve)));
    const { result } = renderHook(() => useAdminSubmit());

    let first: Promise<boolean> | undefined;
    let second: Promise<boolean> | undefined;
    act(() => {
      first = result.current.submit(request);
      second = result.current.submit(request);
    });
    expect(request).toHaveBeenCalledTimes(1);
    await expect(second).resolves.toBe(false);

    await act(async () => {
      resolveRequest?.();
      await first;
    });
    expect(result.current.isSubmitting).toBe(false);
  });

  it('restores the control after a rejection', async () => {
    const { result } = renderHook(() => useAdminSubmit());
    await expect(
      act(() => result.current.submit(() => Promise.reject(new Error('failed')))),
    ).rejects.toThrow('failed');
    expect(result.current.isSubmitting).toBe(false);
  });
});
