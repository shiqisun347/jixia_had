'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Download, KeyRound, LoaderCircle, Send, Upload, Users } from 'lucide-react';
import { useRef, useState } from 'react';

import { Button, buttonVariants } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/toast-provider';
import { adminApi } from '@/features/admin/admin-api';
import { ApiClientError } from '@/lib/auth-api';
import { experimentsApi } from '@/lib/experiments-api';

const TEAM_COUNT = 6;
const TEAM_MEMBER_COUNT = 3;
const EXPERT_COUNT = 3;
const TOPIC_COUNT = 6;
type TeamDraft = { agentId: string; memberIds: string[] };

function blankTeams(): TeamDraft[] {
  return Array.from({ length: TEAM_COUNT }, () => ({
    agentId: '',
    memberIds: Array.from({ length: TEAM_MEMBER_COUNT }, () => ''),
  }));
}

function errorMessage(error: unknown) {
  return error instanceof ApiClientError ? error.message : '操作失败，请稍后重试。';
}

function uniqueNonEmpty(values: readonly string[]) {
  const present = values.filter(Boolean);
  return present.length === new Set(present).size;
}

const selectClass =
  'mt-1 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-900 outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-100';

export function AdminExperimentSetup({
  batchId,
  ruleId,
}: Readonly<{ batchId: string; ruleId: string }>) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [step, setStep] = useState<2 | 3>(2);
  const [teams, setTeams] = useState<TeamDraft[]>(blankTeams);
  const [expertIds, setExpertIds] = useState<string[]>(
    Array.from({ length: EXPERT_COUNT }, () => ''),
  );
  const [topicIds, setTopicIds] = useState<string[]>(Array.from({ length: TOPIC_COUNT }, () => ''));
  const [trainingTopicId, setTrainingTopicId] = useState('');
  const [rosterDirty, setRosterDirty] = useState(false);
  const [scheduleDirty, setScheduleDirty] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const csvInput = useRef<HTMLInputElement>(null);

  const catalog = useQuery({ queryKey: ['admin', 'catalog'], queryFn: adminApi.catalog });
  const ruleAgents = useQuery({
    queryKey: ['admin', 'rules', ruleId, 'agents'],
    queryFn: () => adminApi.ruleAgents(ruleId),
    enabled: Boolean(ruleId),
  });
  const users = useQuery({
    queryKey: ['admin', 'users', 'experiment-roster'],
    queryFn: () => adminApi.users({ page: 1, page_size: 100, status: 'ACTIVE', sort: 'username' }),
  });
  const roster = useQuery({
    queryKey: ['admin', 'experiments', batchId, 'roster'],
    queryFn: () => experimentsApi.adminRoster(batchId),
  });
  const schedule = useQuery({
    queryKey: ['admin', 'experiments', batchId, 'schedule'],
    queryFn: () => experimentsApi.adminSchedule(batchId),
  });

  const enabledAgents = ruleAgents.data?.filter((agent) => agent.status === 'ENABLED') ?? [];
  const enabledTopics = catalog.data?.topics.filter((topic) => topic.status === 'ENABLED') ?? [];
  const savedTeams =
    roster.data?.teams.length === TEAM_COUNT
      ? roster.data.teams.map((team) => ({
          agentId: team.agent_profile_id,
          memberIds: team.members.map((member) => member.user_id),
        }))
      : blankTeams();
  const savedExpertIds =
    roster.data?.experts.length === EXPERT_COUNT
      ? roster.data.experts.map((expert) => expert.user_id)
      : Array.from({ length: EXPERT_COUNT }, () => '');
  const currentTeams = rosterDirty ? teams : savedTeams;
  const currentExpertIds = rosterDirty ? expertIds : savedExpertIds;
  const formalSchedule = schedule.data?.matches.filter((match) => match.kind === 'FORMAL') ?? [];
  const savedTopicIds = Array.from(
    { length: TOPIC_COUNT },
    (_, index) => formalSchedule.find((match) => match.round_no === index + 1)?.topic_id ?? '',
  );
  const savedTrainingTopicId =
    schedule.data?.matches.find((match) => match.kind === 'TRAINING')?.topic_id ?? '';
  const currentTopicIds = scheduleDirty ? topicIds : savedTopicIds;
  const currentTrainingTopicId = scheduleDirty ? trainingTopicId : savedTrainingTopicId;
  const participantIds = currentTeams.flatMap((team) => team.memberIds);
  const agentIds = currentTeams.map((team) => team.agentId);
  const allHumanIds = [...participantIds, ...currentExpertIds];
  const rosterComplete =
    participantIds.every(Boolean) &&
    agentIds.every(Boolean) &&
    currentExpertIds.every(Boolean) &&
    uniqueNonEmpty(allHumanIds) &&
    uniqueNonEmpty(agentIds);
  const rosterSaved = roster.data?.teams.length === TEAM_COUNT && !rosterDirty;
  const topicsComplete =
    currentTopicIds.every(Boolean) &&
    Boolean(currentTrainingTopicId) &&
    uniqueNonEmpty([...currentTopicIds, currentTrainingTopicId]);
  const duplicateWarning = (() => {
    if (!uniqueNonEmpty(allHumanIds)) return '参与者与专家账号不能重复。';
    if (!uniqueNonEmpty(agentIds)) return '六支队伍必须使用不同 Agent。';
    if (!uniqueNonEmpty([...currentTopicIds, currentTrainingTopicId]))
      return '正式题与训练题不能重复。';
    return '';
  })();

  const generateAccounts = useMutation({
    mutationFn: experimentsApi.generateAccounts,
    onSuccess: async (result) => {
      const byCode = new Map(result.accounts.map((account) => [account.code, account.user_id]));
      setTeams(
        Array.from({ length: TEAM_COUNT }, (_, teamIndex) => ({
          agentId: currentTeams[teamIndex]?.agentId ?? '',
          memberIds: Array.from(
            { length: TEAM_MEMBER_COUNT },
            (_, memberIndex) =>
              byCode.get(
                `P${String(teamIndex * TEAM_MEMBER_COUNT + memberIndex + 1).padStart(2, '0')}`,
              ) ?? '',
          ),
        })),
      );
      setExpertIds(
        Array.from(
          { length: EXPERT_COUNT },
          (_, index) => byCode.get(`E${String(index + 1).padStart(2, '0')}`) ?? '',
        ),
      );
      setRosterDirty(true);
      await queryClient.invalidateQueries({ queryKey: ['admin', 'users', 'experiment-roster'] });
      showToast({
        message: `账号已就绪：P01-P18、E01-E03，统一密码 ${result.default_password}`,
        tone: 'success',
      });
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });
  const saveRoster = useMutation({
    mutationFn: () =>
      experimentsApi.replaceRoster(batchId, {
        teams: currentTeams.map((team, teamIndex) => ({
          team_code: `T${String(teamIndex + 1).padStart(2, '0')}`,
          agent_profile_id: team.agentId,
          members: team.memberIds.map((userId, memberIndex) => ({
            user_id: userId,
            participant_code: `P${String(teamIndex * TEAM_MEMBER_COUNT + memberIndex + 1).padStart(2, '0')}`,
          })),
        })),
        experts: currentExpertIds.map((userId, index) => ({
          user_id: userId,
          expert_code: `E${String(index + 1).padStart(2, '0')}`,
        })),
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', batchId, 'roster'] }),
        queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', batchId, 'schedule'] }),
      ]);
      setRosterDirty(false);
      showToast({ message: '人员与 Agent 已保存。', tone: 'success' });
      setStep(3);
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });
  const generate = useMutation({
    mutationFn: () =>
      experimentsApi.generateSchedule(batchId, currentTopicIds, currentTrainingTopicId),
    onSuccess: async () => {
      setScheduleDirty(false);
      await queryClient.invalidateQueries({
        queryKey: ['admin', 'experiments', batchId, 'schedule'],
      });
      showToast({ message: '排表已生成：18 场正式赛、3 场训练赛。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });
  const importSchedule = useMutation({
    mutationFn: (csvText: string) => experimentsApi.importSchedule(batchId, csvText),
    onSuccess: async (result) => {
      setScheduleDirty(false);
      await queryClient.invalidateQueries({
        queryKey: ['admin', 'experiments', batchId, 'schedule'],
      });
      showToast({ message: `已导入 ${result.imported_match_count} 场比赛。`, tone: 'success' });
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });
  const publish = useMutation({
    mutationFn: () => experimentsApi.publishBatch(batchId),
    onSuccess: async () => {
      setPublishOpen(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', 'batches'] }),
        queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', batchId, 'progress'] }),
      ]);
      showToast({ message: '批次已发布，参与者现在可以看到预约。', tone: 'success' });
    },
    onError: (error) => showToast({ message: errorMessage(error), tone: 'error' }),
  });

  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="grid border-b border-slate-200 sm:grid-cols-2">
        <button
          className={`flex min-h-14 items-center gap-3 px-5 text-left text-sm font-black ${step === 2 ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50'}`}
          onClick={() => setStep(2)}
          type="button"
        >
          <span className="grid size-7 place-items-center rounded-full bg-white text-xs shadow-sm">
            2
          </span>
          人员与 Agent
          {rosterSaved ? <Check className="ml-auto size-4 text-emerald-600" /> : null}
        </button>
        <button
          className={`flex min-h-14 items-center gap-3 border-t border-slate-200 px-5 text-left text-sm font-black sm:border-l sm:border-t-0 ${step === 3 ? 'bg-blue-50 text-blue-700' : 'text-slate-600 hover:bg-slate-50'}`}
          onClick={() => setStep(3)}
          type="button"
        >
          <span className="grid size-7 place-items-center rounded-full bg-white text-xs shadow-sm">
            3
          </span>
          辩题与排表
          {schedule.data?.matches.length === 21 ? (
            <Check className="ml-auto size-4 text-emerald-600" />
          ) : null}
        </button>
      </div>

      {step === 2 ? (
        <div className="space-y-6 p-5 lg:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-blue-100 bg-blue-50 p-4">
            <div>
              <h3 className="flex items-center gap-2 text-sm font-black">
                <KeyRound className="size-4 text-blue-700" /> 匿名账号
              </h3>
              <p className="mt-1 text-sm text-slate-600">
                P01-P18、E01-E03 · 统一默认密码 Jixia2026
              </p>
            </div>
            <Button
              disabled={generateAccounts.isPending}
              onClick={() => generateAccounts.mutate()}
              variant="secondary"
            >
              {generateAccounts.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <KeyRound className="size-4" />
              )}
              准备账号
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Users className="size-5 text-blue-600" />
            <h3 className="text-base font-black">六支固定队伍</h3>
          </div>
          <div className="space-y-4">
            {currentTeams.map((team, teamIndex) => (
              <div
                className="grid gap-3 border-t border-slate-100 pt-4 first:border-t-0 first:pt-0 xl:grid-cols-[4rem_repeat(4,minmax(0,1fr))]"
                key={`team-${teamIndex + 1}`}
              >
                <strong className="pt-3 text-sm">T{String(teamIndex + 1).padStart(2, '0')}</strong>
                {team.memberIds.map((userId, memberIndex) => (
                  <label className="text-xs font-bold text-slate-500" key={memberIndex}>
                    P{String(teamIndex * TEAM_MEMBER_COUNT + memberIndex + 1).padStart(2, '0')}
                    <select
                      className={selectClass}
                      onChange={(event) => {
                        setRosterDirty(true);
                        setTeams(
                          currentTeams.map((candidate, index) =>
                            index === teamIndex
                              ? {
                                  ...candidate,
                                  memberIds: candidate.memberIds.map((member, position) =>
                                    position === memberIndex ? event.target.value : member,
                                  ),
                                }
                              : candidate,
                          ),
                        );
                      }}
                      value={userId}
                    >
                      <option value="">选择账号</option>
                      {users.data?.items.map((user) => (
                        <option key={user.id} value={user.id}>
                          {user.real_name} · {user.username}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
                <label className="text-xs font-bold text-slate-500">
                  固定 Agent
                  <select
                    className={selectClass}
                    onChange={(event) => {
                      setRosterDirty(true);
                      setTeams(
                        currentTeams.map((candidate, index) =>
                          index === teamIndex
                            ? { ...candidate, agentId: event.target.value }
                            : candidate,
                        ),
                      );
                    }}
                    value={team.agentId}
                  >
                    <option value="">选择 Agent</option>
                    {enabledAgents.map((agent) => (
                      <option key={agent.id} value={agent.id}>
                        {agent.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            {currentExpertIds.map((userId, index) => (
              <label className="text-xs font-bold text-slate-500" key={index}>
                E{String(index + 1).padStart(2, '0')} 专家
                <select
                  className={selectClass}
                  onChange={(event) => {
                    setRosterDirty(true);
                    setExpertIds(
                      currentExpertIds.map((value, position) =>
                        position === index ? event.target.value : value,
                      ),
                    );
                  }}
                  value={userId}
                >
                  <option value="">选择账号</option>
                  {users.data?.items.map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.real_name} · {user.username}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          {duplicateWarning ? (
            <p className="text-sm font-bold text-red-700">{duplicateWarning}</p>
          ) : null}
          <div className="flex justify-end">
            <Button
              disabled={!rosterComplete || !rosterDirty || saveRoster.isPending}
              onClick={() => saveRoster.mutate()}
            >
              {saveRoster.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存并继续
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-6 p-5 lg:p-6">
          {!rosterSaved ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm font-bold text-amber-900">
              先完成人员与 Agent 配置，再生成排表。
            </div>
          ) : null}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {currentTopicIds.map((topicId, index) => (
              <label className="text-xs font-bold text-slate-500" key={index}>
                第 {index + 1} 轮正式题
                <select
                  className={selectClass}
                  onChange={(event) => {
                    setScheduleDirty(true);
                    setTopicIds(
                      currentTopicIds.map((value, position) =>
                        position === index ? event.target.value : value,
                      ),
                    );
                  }}
                  value={topicId}
                >
                  <option value="">选择辩题</option>
                  {enabledTopics.map((topic) => (
                    <option key={topic.id} value={topic.id}>
                      {topic.title}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <label className="block max-w-xl text-xs font-bold text-slate-500">
            训练赛独立辩题
            <select
              className={selectClass}
              onChange={(event) => {
                setScheduleDirty(true);
                setTrainingTopicId(event.target.value);
              }}
              value={currentTrainingTopicId}
            >
              <option value="">选择训练题</option>
              {enabledTopics.map((topic) => (
                <option key={topic.id} value={topic.id}>
                  {topic.title}
                </option>
              ))}
            </select>
          </label>
          {duplicateWarning ? (
            <p className="text-sm font-bold text-red-700">{duplicateWarning}</p>
          ) : null}
          <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-5">
            <Button
              disabled={!rosterSaved || !topicsComplete || generate.isPending}
              onClick={() => generate.mutate()}
              variant="secondary"
            >
              {generate.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}生成排表
            </Button>
            <input
              accept=".csv,text/csv"
              className="sr-only"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void file.text().then((text) => importSchedule.mutate(text));
                event.target.value = '';
              }}
              ref={csvInput}
              type="file"
            />
            <Button
              disabled={!rosterSaved || importSchedule.isPending}
              onClick={() => csvInput.current?.click()}
              variant="secondary"
            >
              <Upload className="size-4" /> 导入 CSV
            </Button>
            {schedule.data?.matches.length ? (
              <a
                className={buttonVariants({ variant: 'secondary' })}
                href={`/api/admin/experiments/batches/${batchId}/schedule.csv`}
              >
                <Download className="size-4" /> 导出机器 CSV
              </a>
            ) : null}
            <span className="ml-auto text-sm font-bold text-slate-600">
              {schedule.data?.matches.length
                ? `${schedule.data.matches.length} 场 · 版本 ${schedule.data.schedule_version}`
                : '尚未生成排表'}
            </span>
          </div>
          <div className="flex justify-end border-t border-slate-100 pt-5">
            <Button
              disabled={schedule.data?.matches.length !== 21}
              onClick={() => setPublishOpen(true)}
            >
              <Send className="size-4" /> 发布批次
            </Button>
          </div>
        </div>
      )}
      <ConfirmDialog
        confirmLabel="确认发布"
        description="发布后人员、Agent 和排表冻结，参与者将看到固定预约。"
        loading={publish.isPending}
        onConfirm={() => publish.mutate()}
        onOpenChange={setPublishOpen}
        open={publishOpen}
        title="发布这个实验批次？"
        tone="primary"
      />
    </section>
  );
}
