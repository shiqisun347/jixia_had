import { describe, expect, it } from 'vitest';

import {
  derivePreparationFlow,
  deriveRoomStartBlockers,
  selectDefaultRuleId,
} from './room-experience';

describe('selectDefaultRuleId', () => {
  const rules = [
    { id: 'one', name: '1v1 快速赛', side_size: 1 },
    { id: 'four-generic', name: '自定义四人赛', side_size: 4 },
    { id: 'four-formal', name: '4v4 正式辩论赛', side_size: 4 },
  ];

  it('prefers the formal 4v4 rule by exact name', () => {
    expect(selectDefaultRuleId(rules)).toBe('four-formal');
  });

  it('falls back to any 4v4 rule and then the first enabled rule', () => {
    expect(selectDefaultRuleId(rules.slice(0, 2))).toBe('four-generic');
    expect(selectDefaultRuleId(rules.slice(0, 1))).toBe('one');
    expect(selectDefaultRuleId([])).toBe('');
  });
});

describe('deriveRoomStartBlockers', () => {
  const member = {
    user_id: 'human-1',
    member_role: 'DEBATER',
    online: true,
    ready: true,
    real_name: '林知行',
  };
  const humanSeat = {
    occupant_type: 'HUMAN',
    user_id: 'human-1',
    occupant_name: '林知行',
  };
  const agentSeat = { occupant_type: 'AGENT', agent_profile_id: 'agent-1' };

  it('explains empty, offline and unready human seats', () => {
    expect(
      deriveRoomStartBlockers({ members: [member], seats: [{ occupant_type: 'EMPTY' }] }),
    ).toEqual(['仍有空席，请先安排人类或 Agent']);
    expect(
      deriveRoomStartBlockers({ members: [{ ...member, online: false }], seats: [humanSeat] }),
    ).toEqual(['林知行当前离线，需重新进入房间']);
    expect(
      deriveRoomStartBlockers({ members: [{ ...member, ready: false }], seats: [humanSeat] }),
    ).toEqual(['林知行尚未完成设备检测与准备']);
  });

  it('allows a complete unique lineup and rejects duplicate Agents', () => {
    expect(deriveRoomStartBlockers({ members: [member], seats: [humanSeat, agentSeat] })).toEqual(
      [],
    );
    expect(deriveRoomStartBlockers({ members: [], seats: [agentSeat, { ...agentSeat }] })).toEqual([
      '同一个 Agent 不能占用多个席位，请重新安排',
    ]);
  });
});

describe('derivePreparationFlow', () => {
  const base = {
    hasActiveMembership: true,
    memberRole: 'DEBATER',
    hasOwnSeat: false,
    hasValidDeviceCheck: false,
    ready: false,
    recheck: false,
  };

  it('derives identity, seat, device and ready without duplicate local state', () => {
    expect(derivePreparationFlow({ ...base, hasActiveMembership: false }).activeStep).toBe(1);
    expect(derivePreparationFlow(base).activeStep).toBe(2);
    expect(derivePreparationFlow({ ...base, hasOwnSeat: true }).activeStep).toBe(3);
    expect(derivePreparationFlow({ ...base, hasOwnSeat: true, ready: true }).activeStep).toBe(4);
  });

  it('treats an organizer with a seat as a human participant', () => {
    const flow = derivePreparationFlow({ ...base, memberRole: 'ORGANIZER', hasOwnSeat: true });
    expect(flow.isHumanParticipant).toBe(true);
    expect(flow.activeStep).toBe(3);
  });

  it('marks spectators ready without requiring a seat or device check', () => {
    const flow = derivePreparationFlow({ ...base, memberRole: 'SPECTATOR' });
    expect(flow.isSpectator).toBe(true);
    expect(flow.activeStep).toBe(4);
  });

  it('keeps recheck on the device step even when the member was ready', () => {
    const flow = derivePreparationFlow({
      ...base,
      memberRole: 'ORGANIZER',
      hasOwnSeat: true,
      hasValidDeviceCheck: true,
      ready: true,
      recheck: true,
    });
    expect(flow.activeStep).toBe(3);
    expect(flow.completedSteps).toEqual([1, 2]);
  });

  it('uses the probe outcome as the authoritative next action', () => {
    expect(
      derivePreparationFlow({ ...base, hasOwnSeat: true, deviceResultStatus: 'WARN' }).nextAction,
    ).toBe('确认网络提示并完成准备');
    expect(
      derivePreparationFlow({ ...base, hasOwnSeat: true, deviceResultStatus: 'FAIL' }).nextAction,
    ).toBe('根据提示处理后重新检测');
  });
});
