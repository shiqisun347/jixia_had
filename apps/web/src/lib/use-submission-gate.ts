'use client';

import { useCallback, useRef, useState } from 'react';

export function useSubmissionGate() {
  const pendingRef = useRef(false);
  const [isPending, setIsPending] = useState(false);

  const tryStart = useCallback(() => {
    if (pendingRef.current) return false;
    pendingRef.current = true;
    setIsPending(true);
    return true;
  }, []);

  const release = useCallback(() => {
    pendingRef.current = false;
    setIsPending(false);
  }, []);

  return { isPending, release, tryStart };
}
