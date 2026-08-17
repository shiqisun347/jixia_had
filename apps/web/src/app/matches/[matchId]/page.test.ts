import { describe, expect, it, vi } from 'vitest';

import { commitJudgeResult } from './page';

describe('judge result commit semantics', () => {
  it('returns the authoritative refreshed page after a successful commit', async () => {
    const patch = vi.fn().mockResolvedValue({ status: 'updated' });
    const refresh = vi.fn().mockResolvedValue({ judge: { status: 'SUCCEEDED' } });

    await expect(commitJudgeResult(patch, refresh)).resolves.toEqual({
      refreshed: true,
      data: { judge: { status: 'SUCCEEDED' } },
    });
  });

  it('keeps commit success distinct from a later refresh failure', async () => {
    const patch = vi.fn().mockResolvedValue({ status: 'updated' });
    const refresh = vi.fn().mockRejectedValue(new Error('read failed'));

    await expect(commitJudgeResult(patch, refresh)).resolves.toEqual({ refreshed: false });
  });

  it('propagates a commit failure without attempting the refresh', async () => {
    const patchError = new Error('invalid scores');
    const patch = vi.fn().mockRejectedValue(patchError);
    const refresh = vi.fn();

    await expect(commitJudgeResult(patch, refresh)).rejects.toBe(patchError);
    expect(refresh).not.toHaveBeenCalled();
  });
});
