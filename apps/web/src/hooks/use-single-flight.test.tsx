import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useSingleFlight } from './use-single-flight';

describe('useSingleFlight', () => {
  it('returns the request value and restores pending state', async () => {
    const { result } = renderHook(() => useSingleFlight());
    await expect(act(() => result.current.run(() => Promise.resolve('saved')))).resolves.toEqual({
      started: true,
      value: 'saved',
    });
    expect(result.current.isPending).toBe(false);
  });

  it('coalesces synchronous repeated calls', async () => {
    let resolveRequest: ((value: string) => void) | undefined;
    const request = vi.fn(() => new Promise<string>((resolve) => (resolveRequest = resolve)));
    const { result } = renderHook(() => useSingleFlight());

    let first: Promise<{ started: false } | { started: true; value: string }> | undefined;
    let second: Promise<{ started: false } | { started: true; value: string }> | undefined;
    act(() => {
      first = result.current.run(request);
      second = result.current.run(request);
    });
    expect(request).toHaveBeenCalledTimes(1);
    await expect(second).resolves.toEqual({ started: false });

    await act(async () => {
      resolveRequest?.('saved');
      await first;
    });
  });

  it('restores the gate after a rejection', async () => {
    const { result } = renderHook(() => useSingleFlight());
    await expect(
      act(() => result.current.run(() => Promise.reject(new Error('failed')))),
    ).rejects.toThrow('failed');
    expect(result.current.isPending).toBe(false);
  });
});
