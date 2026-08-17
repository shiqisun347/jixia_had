import { describe, expect, it } from 'vitest';

import { formal4v4Stages } from './page';

describe('formal 4v4 rule template', () => {
  it('matches the approved stage order and free debate clocks', () => {
    const stages = formal4v4Stages();

    expect(stages).toHaveLength(9);
    expect(stages.map((stage) => stage.name)).toEqual([
      '正方一辩立论',
      '反方一辩立论',
      '正方二辩陈词',
      '反方二辩陈词',
      '正方三辩陈词',
      '反方三辩陈词',
      '自由辩论',
      '反方四辩总结',
      '正方四辩总结',
    ]);
    expect(stages[6]).toMatchObject({
      stage_kind: 'FREE_DEBATE',
      duration_seconds: 180,
      max_speech_seconds: 30,
      starting_side: 'AFFIRMATIVE',
    });
    expect(stages[8]?.end_host_text).toBe('本场辩论到此结束，感谢各位辩手。');
  });
});
