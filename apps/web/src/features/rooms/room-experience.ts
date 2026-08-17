type RuleOption = {
  id: string;
  name: string;
  side_size: number;
};

export function selectDefaultRuleId(rules: readonly RuleOption[]): string {
  return (
    rules.find((rule) => rule.name === '4v4 正式辩论赛')?.id ??
    rules.find((rule) => rule.side_size === 4)?.id ??
    rules[0]?.id ??
    ''
  );
}

export type PreparationStep = 1 | 2 | 3 | 4;

export type PreparationFlow = {
  activeStep: PreparationStep;
  completedSteps: readonly PreparationStep[];
  isSpectator: boolean;
  isHumanParticipant: boolean;
  nextAction: string;
};

type StartReadinessMember = {
  user_id: string;
  member_role: string;
  online: boolean;
  ready: boolean;
  real_name: string;
};

type StartReadinessSeat = {
  occupant_type: string;
  user_id?: string | null;
  agent_profile_id?: string | null;
  occupant_name?: string | null;
};

export function deriveRoomStartBlockers({
  members,
  seats,
}: Readonly<{
  members: readonly StartReadinessMember[];
  seats: readonly StartReadinessSeat[];
}>): string[] {
  if (!seats.length || seats.some((seat) => seat.occupant_type === 'EMPTY')) {
    return ['仍有空席，请先安排人类或 Agent'];
  }

  const seatedUserIds = new Set(
    seats.flatMap((seat) => (seat.occupant_type === 'HUMAN' && seat.user_id ? [seat.user_id] : [])),
  );
  const unseated = members.find(
    (member) => member.member_role === 'DEBATER' && !seatedUserIds.has(member.user_id),
  );
  if (unseated) return [`${unseated.real_name}尚未选择席位`];

  for (const seat of seats) {
    if (seat.occupant_type !== 'HUMAN' || !seat.user_id) continue;
    const member = members.find((candidate) => candidate.user_id === seat.user_id);
    const name = seat.occupant_name || member?.real_name || '真人辩手';
    if (!member?.online) return [`${name}当前离线，需重新进入房间`];
    if (!member.ready) return [`${name}尚未完成设备检测与准备`];
  }

  const agentIds = seats.flatMap((seat) =>
    seat.occupant_type === 'AGENT' && seat.agent_profile_id ? [seat.agent_profile_id] : [],
  );
  if (new Set(agentIds).size !== agentIds.length) {
    return ['同一个 Agent 不能占用多个席位，请重新安排'];
  }
  return [];
}

export function derivePreparationFlow({
  hasActiveMembership,
  memberRole,
  hasOwnSeat,
  hasValidDeviceCheck,
  ready,
  recheck,
  deviceResultStatus,
}: Readonly<{
  hasActiveMembership: boolean;
  memberRole?: string | null;
  hasOwnSeat: boolean;
  hasValidDeviceCheck: boolean;
  ready: boolean;
  recheck: boolean;
  deviceResultStatus?: 'PASS' | 'WARN' | 'FAIL' | null;
}>): PreparationFlow {
  if (!hasActiveMembership) {
    return {
      activeStep: 1,
      completedSteps: [],
      isSpectator: false,
      isHumanParticipant: false,
      nextAction: '先选择作为辩手或观众加入',
    };
  }

  if (memberRole === 'SPECTATOR') {
    return {
      activeStep: 4,
      completedSteps: [1, 2, 3, 4],
      isSpectator: true,
      isHumanParticipant: false,
      nextAction: '观众席已就绪，等待比赛开始',
    };
  }

  if (!hasOwnSeat) {
    return {
      activeStep: 2,
      completedSteps: [1],
      isSpectator: false,
      isHumanParticipant: false,
      nextAction: '选择一个正方或反方席位',
    };
  }

  if (ready && !recheck) {
    return {
      activeStep: 4,
      completedSteps: [1, 2, 3, 4],
      isSpectator: false,
      isHumanParticipant: true,
      nextAction: '准备完成，等待房主开始比赛',
    };
  }

  return {
    activeStep: 3,
    completedSteps: hasValidDeviceCheck && !recheck ? [1, 2, 3] : [1, 2],
    isSpectator: false,
    isHumanParticipant: true,
    nextAction:
      deviceResultStatus === 'WARN'
        ? '确认网络提示并完成准备'
        : deviceResultStatus === 'FAIL'
          ? '根据提示处理后重新检测'
          : hasValidDeviceCheck && !recheck
            ? '使用有效检测并完成准备'
            : '开始约 3 秒的设备检测',
  };
}
