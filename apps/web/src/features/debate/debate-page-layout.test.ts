import { describe, expect, it } from 'vitest';

import {
  canPauseMatch,
  experimentAgentSeatStatus,
  freeDebateProgress,
  localizedStageName,
  runtimeErrorLabel,
  shouldShowRuntimeFault,
} from './debate-page-layout';
import type { MatchSnapshot } from '@/lib/matches-api';

describe('freeDebateProgress', () => {
  it.each([
    [300_000, 300_000, 100],
    [150_000, 300_000, 50],
    [0, 300_000, 0],
    [450_000, 300_000, 100],
    [-1_000, 300_000, 0],
  ])('maps %i of %i to %i percent', (remaining, total, expected) => {
    expect(freeDebateProgress(remaining, total)).toBe(expected);
  });

  it.each([null, 0, -1])('returns a neutral result for invalid total %s', (total) => {
    expect(freeDebateProgress(100_000, total)).toBeNull();
  });
});

describe('standard stage localization', () => {
  it('uses canonical English debate terminology for a standard stage', () => {
    expect(localizedStageName('正方一辩立论', 'en')).toBe('First Affirmative Constructive');
  });

  it('does not alter a custom stage name without an approved translation', () => {
    expect(localizedStageName('自定义质询环节', 'en')).toBe('自定义质询环节');
    expect(localizedStageName('正方一辩立论', 'zh-CN')).toBe('正方一辩立论');
  });
});

describe('match pause controls', () => {
  it('allows a debater or experiment controller while the match is running', () => {
    expect(canPauseMatch('RUNNING', true, false)).toBe(true);
    expect(canPauseMatch('RUNNING', false, true)).toBe(true);
    expect(canPauseMatch('RUNNING', false, false)).toBe(false);
    expect(canPauseMatch('PAUSED', true, true)).toBe(false);
  });
});

describe('experimentAgentSeatStatus', () => {
  const snapshot = {
    experiment_mode: true,
    action_state: 'FREE_SELECTING',
    free_holder_side: 'NEGATIVE',
    current_speaker_side: null,
    experiment_team_state: { agent_status: 'DECIDING' },
  } as MatchSnapshot;

  it('projects the authoritative status only onto the candidate-side Agent', () => {
    expect(
      experimentAgentSeatStatus(snapshot, {
        occupant_type: 'AGENT',
        side: 'NEGATIVE',
      }),
    ).toBe('DECIDING');
    expect(
      experimentAgentSeatStatus(snapshot, {
        occupant_type: 'AGENT',
        side: 'AFFIRMATIVE',
      }),
    ).toBeNull();
  });

  it('renders a technical miss as an effective skip', () => {
    expect(
      experimentAgentSeatStatus(
        {
          ...snapshot,
          experiment_team_state: { agent_status: 'TECHNICAL_MISSING' },
        } as MatchSnapshot,
        { occupant_type: 'AGENT', side: 'NEGATIVE' },
      ),
    ).toBe('SKIP');
  });

  it('projects each formal 4v4 Agent decision independently', () => {
    const formal = {
      ...snapshot,
      experiment_mode: false,
      formal_4v4: true,
      agent_decisions: [
        { agent_profile_id: 'agent-1', status: 'HAND' },
        { agent_profile_id: 'agent-2', status: 'SKIP' },
      ],
    } as MatchSnapshot;
    expect(
      experimentAgentSeatStatus(
        formal,
        { occupant_type: 'AGENT', side: 'NEGATIVE' },
        'agent-1',
      ),
    ).toBe('RAISE');
    expect(
      experimentAgentSeatStatus(
        formal,
        { occupant_type: 'AGENT', side: 'NEGATIVE' },
        'agent-2',
      ),
    ).toBe('SKIP');
  });
});

describe('runtimeErrorLabel', () => {
  it('explains a human start timeout with a recovery action', () => {
    expect(runtimeErrorLabel('HUMAN_START_TIMEOUT')).toBe(
      '当前辩手 60 秒内未开始发言，比赛已暂停；确认设备后可申请恢复。',
    );
  });

  it('explains a human-only wait timeout as a rule pause', () => {
    expect(runtimeErrorLabel('HUMAN_WAIT_TIMEOUT')).toBe(
      '60 秒内无人举手，比赛已暂停；恢复后将重新开始完整等待。',
    );
  });

  it.each([
    ['PAUSED', 'HUMAN_START_TIMEOUT', true],
    ['PAUSED', null, false],
    ['ERROR', null, true],
    ['SYSTEM_RECOVERY', null, true],
    ['RUNNING', 'HUMAN_START_TIMEOUT', false],
  ])('selects fault details for %s with error %s', (status, errorCode, expected) => {
    expect(shouldShowRuntimeFault(status, errorCode)).toBe(expected);
  });
});
