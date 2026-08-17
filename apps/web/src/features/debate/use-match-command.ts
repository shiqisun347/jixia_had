'use client';

import { useCallback, useRef, useState } from 'react';

import { useToast } from '@/components/ui/toast-provider';

export const MATCH_COMMAND_FAILURE_MESSAGE = '比赛指令未执行，请检查实时连接后重试。';

export function shouldRestoreMicrophone(commandSucceeded: boolean, isCurrentSpeaker: boolean) {
  return !commandSucceeded && isCurrentSpeaker;
}

export function useMatchCommand<Command extends string>(
  sendCommand: (type: Command) => Promise<boolean>,
) {
  const { showToast } = useToast();
  const [isPending, setIsPending] = useState(false);
  const inFlight = useRef(false);

  const command = useCallback(
    async (type: Command) => {
      if (inFlight.current) return false;
      inFlight.current = true;
      setIsPending(true);
      try {
        let success = false;
        try {
          success = await sendCommand(type);
        } catch {
          success = false;
        }
        if (!success) showToast({ message: MATCH_COMMAND_FAILURE_MESSAGE, tone: 'error' });
        return success;
      } finally {
        inFlight.current = false;
        setIsPending(false);
      }
    },
    [sendCommand, showToast],
  );

  return { command, isPending } as const;
}
