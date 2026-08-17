'use client';

import { useCallback, useRef, useState } from 'react';

type SingleFlightResult<T> = { started: false } | { started: true; value: T };

export function useSingleFlight() {
  const pendingRef = useRef(false);
  const [isPending, setIsPending] = useState(false);

  const run = useCallback(async <T>(request: () => Promise<T>): Promise<SingleFlightResult<T>> => {
    if (pendingRef.current) return { started: false };
    pendingRef.current = true;
    setIsPending(true);
    try {
      return { started: true, value: await request() };
    } finally {
      pendingRef.current = false;
      setIsPending(false);
    }
  }, []);

  return { isPending, run };
}
