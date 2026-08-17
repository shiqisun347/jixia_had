'use client';

import { useCallback, useRef, useState } from 'react';

export function useAdminSubmit() {
  const pendingRef = useRef(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const submit = useCallback(async (request: () => Promise<void>) => {
    if (pendingRef.current) return false;
    pendingRef.current = true;
    setIsSubmitting(true);
    try {
      await request();
      return true;
    } finally {
      pendingRef.current = false;
      setIsSubmitting(false);
    }
  }, []);

  return { isSubmitting, submit };
}
