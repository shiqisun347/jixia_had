import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useSubmissionGate } from './use-submission-gate';

describe('useSubmissionGate', () => {
  it('rejects repeated starts synchronously until released', () => {
    const { result } = renderHook(() => useSubmissionGate());

    act(() => {
      expect(result.current.tryStart()).toBe(true);
      expect(result.current.tryStart()).toBe(false);
    });
    expect(result.current.isPending).toBe(true);

    act(() => result.current.release());
    expect(result.current.isPending).toBe(false);
    expect(result.current.tryStart()).toBe(true);
  });
});
