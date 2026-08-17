import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';

import {
  MATCH_COMMAND_FAILURE_MESSAGE,
  shouldRestoreMicrophone,
  useMatchCommand,
} from './use-match-command';

function Wrapper({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <ToastProvider>
      <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
    </ToastProvider>
  );
}

describe('useMatchCommand', () => {
  afterEach(cleanup);

  it('coalesces repeated commands while one is in flight', async () => {
    let resolveCommand: ((value: boolean) => void) | undefined;
    const send = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveCommand = resolve;
        }),
    );
    const { result } = renderHook(() => useMatchCommand(send), { wrapper: Wrapper });

    let first: Promise<boolean> | undefined;
    act(() => {
      first = result.current.command('match.pause');
      void result.current.command('match.pause');
    });
    expect(send).toHaveBeenCalledTimes(1);
    expect(result.current.isPending).toBe(true);

    await act(async () => {
      resolveCommand?.(true);
      await first;
    });
    expect(result.current.isPending).toBe(false);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a transient error when the command is not acknowledged', async () => {
    const { result } = renderHook(() => useMatchCommand(vi.fn().mockResolvedValue(false)), {
      wrapper: Wrapper,
    });

    await act(() => result.current.command('speech.finish'));

    expect(screen.getByRole('alert')).toHaveTextContent(MATCH_COMMAND_FAILURE_MESSAGE);
  });

  it('normalizes an unexpected transport rejection into the same visible failure', async () => {
    const { result } = renderHook(
      () => useMatchCommand(vi.fn().mockRejectedValue(new Error('socket send failed'))),
      { wrapper: Wrapper },
    );

    await expect(act(() => result.current.command('match.resume'))).resolves.toBe(false);

    expect(screen.getByRole('alert')).toHaveTextContent(MATCH_COMMAND_FAILURE_MESSAGE);
  });
});

describe('shouldRestoreMicrophone', () => {
  it('restores only after a failed command while the user is still the speaker', () => {
    expect(shouldRestoreMicrophone(false, true)).toBe(true);
    expect(shouldRestoreMicrophone(true, true)).toBe(false);
    expect(shouldRestoreMicrophone(false, false)).toBe(false);
  });
});
