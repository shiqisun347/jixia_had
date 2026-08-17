import { describe, expect, it } from 'vitest';

import { judgeFailureText, judgeStatusText, replayStatusText } from './terminal-copy';

describe('postmatch terminal copy', () => {
  it('marks judging as not applicable for terminated matches', () => {
    expect(judgeStatusText('TERMINATED', false)).toBe('比赛已终止，不进行 AI 评分。');
    expect(judgeStatusText('FINISHED', false)).toBe('评分任务尚未创建');
    expect(judgeStatusText('TERMINATED', true)).toBeNull();
  });

  it('turns judge diagnostics into actionable user copy', () => {
    expect(judgeFailureText('llm_first_token_timeout')).toBe('评分服务响应超时，请重新评分。');
    expect(judgeFailureText('private_vendor_detail')).toBe('AI 评分未完成，请稍后重新评分。');
  });

  it('does not report an empty terminated match as an archive failure', () => {
    expect(replayStatusText({ status: 'FAILED', error_code: 'audio_sources_missing' }, 0)).toEqual({
      tone: 'neutral',
      text: '本场没有可生成的回放。',
    });
  });

  it('keeps real archive failures visible', () => {
    expect(replayStatusText({ status: 'FAILED', error_code: 'encoder_failed' }, 0)).toEqual({
      tone: 'warning',
      text: '音频处理失败，请联系房主或管理员重试。',
    });
    expect(replayStatusText({ status: 'FAILED', error_code: 'audio_sources_missing' }, 1)).toEqual({
      tone: 'warning',
      text: '音频处理失败，请联系房主或管理员重试。',
    });
  });
});
