import { describe, expect, it } from 'vitest';

import { localizeText } from './localized-text';

describe('localizeText', () => {
  it('translates fixed questionnaire and annotation UI text in English', () => {
    expect(localizeText('赛后辩手体验问卷', 'en')).toBe('Post-match debater experience questionnaire');
    expect(localizeText('提交本场问卷', 'en')).toBe('Submit this questionnaire');
  });

  it('keeps Chinese UI text unchanged for the Chinese locale', () => {
    expect(localizeText('提交本场问卷', 'zh-CN')).toBe('提交本场问卷');
  });

  it('preserves unknown business content', () => {
    expect(localizeText('自定义辩题：技术会缩小教育差距吗？', 'en')).toBe(
      '自定义辩题：技术会缩小教育差距吗？',
    );
  });
});
