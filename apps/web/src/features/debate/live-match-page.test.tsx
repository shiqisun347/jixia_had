import { describe, expect, it } from 'vitest';

import { ApiClientError } from '@/lib/auth-api';
import type { MatchSnapshot } from '@/lib/matches-api';
import type { RoomSnapshot } from '@/lib/rooms-api';

import {
  actionLabel,
  normalizedTranscriptDraft,
  terminalPresentation,
  transcriptSaveErrorText,
} from './live-match-page';
import { resolveCurrentSeat } from './match-presentation';
import { matchSocketErrorText, newestMatchSnapshot } from './use-match-runtime';

describe('live match completion presentation', () => {
  it('points users to the available post-match record instead of an unfinished placeholder', () => {
    const presentation = actionLabel('MATCH_FINISHED');

    expect(presentation.title).toBe('本场辩论已完成');
    expect(presentation.detail).toContain('完整文字记录已经归档');
    expect(presentation.detail).not.toContain('后续切片');
  });

  it('does not describe a terminated match as normally completed', () => {
    const presentation = terminalPresentation('TERMINATED');
    expect(presentation?.title).toBe('本场比赛已终止');
    expect(presentation?.detail).toContain('不能继续发言');
    expect(presentation?.detail).not.toContain('AI 裁判正在生成');
  });
});

describe('live transcript editing feedback', () => {
  it('normalizes a meaningful draft and rejects blank text', () => {
    expect(normalizedTranscriptDraft('  修正后的发言  ')).toBe('修正后的发言');
    expect(normalizedTranscriptDraft(' \n  ')).toBeNull();
  });

  it('uses a safe fallback while preserving normalized API messages', () => {
    expect(transcriptSaveErrorText(new Error('network details'))).toBe('保存失败，请稍后重试。');
    expect(
      transcriptSaveErrorText(
        new ApiClientError(409, {
          error: { code: 'speech_not_editable', message: '该发言暂不可修改' },
        }),
      ),
    ).toBe('该发言暂不可修改');
  });
});

describe('current speaker seat resolution', () => {
  const seats = [
    {
      id: 'affirmative-agent',
      side: 'AFFIRMATIVE',
      seat_no: 1,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-a1',
    },
    {
      id: 'negative-agent',
      side: 'NEGATIVE',
      seat_no: 2,
      occupant_type: 'AGENT',
      user_id: null,
      agent_profile_id: 'agent-n2',
    },
  ] as unknown as RoomSnapshot['seats'];
  const base = {
    status: 'RUNNING',
    action_state: 'AGENT_SPEAKING',
    current_speaker_user_id: null,
    current_agent_profile_id: null,
    current_speaker_side: null,
    current_speaker_seat_no: null,
  } as unknown as MatchSnapshot;

  it('does not match the first Agent through two null human ids', () => {
    expect(resolveCurrentSeat(seats, { ...base, current_agent_profile_id: 'agent-n2' })).toBe(
      seats[1],
    );
  });

  it('falls back to the authoritative side and seat number', () => {
    expect(
      resolveCurrentSeat(seats, {
        ...base,
        current_speaker_side: 'NEGATIVE',
        current_speaker_seat_no: 2,
      }),
    ).toBe(seats[1]);
  });

  it('clears the active speaker for terminal matches', () => {
    expect(
      resolveCurrentSeat(seats, {
        ...base,
        status: 'FINISHED',
        current_agent_profile_id: 'agent-n2',
      }),
    ).toBeUndefined();
  });

  it('does not expose stale speaker identity during a host announcement', () => {
    expect(
      resolveCurrentSeat(seats, {
        ...base,
        action_state: 'HOST_ANNOUNCING',
        current_agent_profile_id: 'agent-n2',
        current_speaker_side: 'NEGATIVE',
        current_speaker_seat_no: 2,
      }),
    ).toBeUndefined();
  });
});

describe('agent speaking copy', () => {
  it('does not show the removed central-subtitle claim', () => {
    const presentation = actionLabel('AGENT_SPEAKING');
    expect(presentation.detail).toContain('文字记录同步更新');
    expect(presentation.detail).not.toContain('字幕严格跟随');
  });
});

describe('live match socket feedback', () => {
  it('shows concrete recovery blockers returned by the server', () => {
    expect(
      matchSocketErrorText({
        type: 'match.resume_check_failed',
        payload: { reasons: ['辩手仍离线', '设备检测已失效'] },
      }),
    ).toBe('恢复条件未满足：辩手仍离线；设备检测已失效');
  });

  it('clears stale recovery feedback when the countdown starts', () => {
    expect(matchSocketErrorText({ type: 'match.resume_countdown' })).toBeNull();
  });
});

describe('match snapshot ordering', () => {
  it('does not let an older reconnect response overwrite newer state', () => {
    const newer = { sequence: 12 } as MatchSnapshot;
    const older = { sequence: 11 } as MatchSnapshot;
    expect(newestMatchSnapshot(newer, older)).toBe(newer);
    expect(newestMatchSnapshot(older, newer)).toBe(newer);
  });
});
