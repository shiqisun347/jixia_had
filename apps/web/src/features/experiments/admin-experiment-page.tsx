'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import {
  Download,
  Eye,
  FileSearch,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCcw,
  Search,
  ShieldOff,
  Trash2,
  X,
} from 'lucide-react';
import { Dialog } from 'radix-ui';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/toast-provider';
import { ApiClientError } from '@/lib/auth-api';
import { experimentsApi, type ExperimentJob } from '@/lib/experiments-api';
import { adminApi } from '@/features/admin/admin-api';
import { AdminI18nBoundary } from '@/features/admin/admin-ui';

import { AdminExperimentSetup } from './admin-experiment-setup';

function message(error: unknown) {
  return error instanceof ApiClientError ? error.message : '操作失败，请稍后重试。';
}

function matchStatusLabel(status: string) {
  return (
    {
      DRAFT: '草稿',
      SCHEDULED: '待开赛',
      WAITING: '等待中',
      RUNNING: '进行中',
      PAUSED: '已暂停',
      COMPLETED: '已完成',
      TERMINATED: '已终止',
    }[status] ?? status
  );
}

function attemptStatusLabel(status: string | null) {
  if (!status) return '未创建';
  return (
    {
      CREATED: '已创建',
      WAITING: '等待中',
      RUNNING: '进行中',
      PAUSED: '已暂停',
      COMPLETED: '已完成',
      TERMINATED: '已终止',
    }[status] ?? status
  );
}

function batchStatusLabel(status: string) {
  return { DRAFT: '草稿', PUBLISHED: '已发布', DISABLED: '已停用' }[status] ?? status;
}

function jobStatusLabel(status: string) {
  return (
    {
      PENDING: '等待执行',
      RUNNING: '执行中',
      SUCCEEDED: '已完成',
      FAILED: '失败',
    }[status] ?? status
  );
}

function TaskStatus({ task }: Readonly<{ task: ExperimentJob }>) {
  const query = useQuery({
    queryKey: ['admin', 'experiments', 'jobs', task.id],
    queryFn: () => experimentsApi.job(task.id),
    initialData: task,
    refetchInterval: (state) =>
      ['PENDING', 'RUNNING'].includes(state.state.data?.status ?? '') ? 1500 : false,
  });
  const value = query.data;
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4 text-sm">
      {['PENDING', 'RUNNING'].includes(value.status) ? (
        <LoaderCircle className="size-4 animate-spin text-blue-600" />
      ) : null}
      <strong>{value.task_type === 'EXPERIMENT_BATCH_EXPORT' ? '研究导出' : '保留期检查'}</strong>
      <span className="text-slate-500">{jobStatusLabel(value.status)}</span>
      {value.error_code ? <span className="text-red-700">{value.error_code}</span> : null}
      {value.artifact_ready ? (
        <a
          className="ml-auto inline-flex items-center gap-1 font-black text-blue-700 hover:underline"
          href={`/api/admin/experiments/exports/${value.id}/download`}
        >
          <Download className="size-4" /> 下载 ZIP
        </a>
      ) : null}
      {value.report ? (
        <p className="w-full rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-700">
          {JSON.stringify(value.report, null, 2)}
        </p>
      ) : null}
    </div>
  );
}

export function AdminExperimentPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const batches = useQuery({
    queryKey: ['admin', 'experiments', 'batches'],
    queryFn: experimentsApi.adminBatches,
  });
  const catalog = useQuery({ queryKey: ['admin', 'catalog'], queryFn: adminApi.catalog });
  const [batchId, setBatchId] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [code, setCode] = useState('');
  const [title, setTitle] = useState('');
  const [ruleId, setRuleId] = useState('');
  const [trainingRoomQuota, setTrainingRoomQuota] = useState(3);
  const [jobs, setJobs] = useState<ExperimentJob[]>([]);
  const [publishAttemptId, setPublishAttemptId] = useState<string | null>(null);
  const [publishReason, setPublishReason] = useState('');
  const [disableOpen, setDisableOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const selectedBatchId = batchId || batches.data?.[0]?.id || '';
  const selected = batches.data?.find((batch) => batch.id === selectedBatchId);
  const roster = useQuery({
    queryKey: ['admin', 'experiments', selectedBatchId, 'roster'],
    queryFn: () => experimentsApi.adminRoster(selectedBatchId),
    enabled: settingsOpen && Boolean(selectedBatchId),
  });
  const promptTemplates = useQuery({
    queryKey: ['admin', 'experiments', 'prompt-templates'],
    queryFn: experimentsApi.promptTemplates,
    enabled: settingsOpen,
  });
  const progress = useQuery({
    queryKey: ['admin', 'experiments', selectedBatchId, 'progress'],
    queryFn: () => experimentsApi.adminProgress(selectedBatchId),
    enabled: Boolean(selectedBatchId && selected?.status !== 'DRAFT'),
  });
  const publish = useMutation({
    mutationFn: ({ attemptId, reason }: { attemptId: string; reason: string }) =>
      experimentsApi.publishResult(attemptId, reason),
    onSuccess: async () => {
      setPublishAttemptId(null);
      setPublishReason('');
      await queryClient.invalidateQueries({
        queryKey: ['admin', 'experiments', selectedBatchId, 'progress'],
      });
      showToast({ message: '结果已公开并已请求重算排行榜。', tone: 'success' });
    },
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });
  const createExport = useMutation({
    mutationFn: () => experimentsApi.createExport(selectedBatchId),
    onSuccess: (task) => setJobs((current) => [task, ...current]),
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });
  const retention = useMutation({
    mutationFn: () => experimentsApi.retentionDryRun(selectedBatchId),
    onSuccess: (task) => setJobs((current) => [task, ...current]),
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });
  const disable = useMutation({
    mutationFn: () => experimentsApi.disableBatch(selectedBatchId),
    onSuccess: async () => {
      setDisableOpen(false);
      await queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', 'batches'] });
      showToast({ message: '实验批次已停用，历史数据保持只读。', tone: 'success' });
    },
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });
  const availableRules =
    catalog.data?.rules.filter(
      (rule) =>
        rule.status === 'ENABLED' && rule.side_size === 4 && Boolean(rule.audio_reviewed_at),
    ) ?? [];
  const filteredBatches = (batches.data ?? []).filter((batch) => {
    const needle = search.trim().toLowerCase();
    return (
      (statusFilter === 'ALL' || batch.status === statusFilter) &&
      (!needle || `${batch.code} ${batch.title}`.toLowerCase().includes(needle))
    );
  });
  const createBatch = useMutation({
    mutationFn: () =>
      experimentsApi.createBatch({
        code: code.trim().toUpperCase(),
        title: title.trim(),
        rule_id: ruleId,
        training_room_quota: trainingRoomQuota,
      }),
    onSuccess: async (batch) => {
      setCreateOpen(false);
      setCode('');
      setTitle('');
      setRuleId('');
      setTrainingRoomQuota(3);
      await queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', 'batches'] });
      setBatchId(batch.id);
      showToast({ message: '草稿已创建，继续完成下面两步。', tone: 'success' });
    },
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });
  const updateBatch = useMutation({
    mutationFn: () =>
      experimentsApi.updateBatch(selectedBatchId, {
        code: code.trim().toUpperCase(),
        title: title.trim(),
        rule_id: ruleId,
        training_room_quota: trainingRoomQuota,
      }),
    onSuccess: async () => {
      setEditing(false);
      await queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', 'batches'] });
      showToast({ message: '批次基本信息已保存。', tone: 'success' });
    },
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });
  const deleteBatch = useMutation({
    mutationFn: () => experimentsApi.deleteBatch(selectedBatchId),
    onSuccess: async () => {
      setDeleteOpen(false);
      setBatchId('');
      await queryClient.invalidateQueries({ queryKey: ['admin', 'experiments', 'batches'] });
      showToast({ message: '草稿已删除。', tone: 'success' });
    },
    onError: (error) => showToast({ message: message(error), tone: 'error' }),
  });

  const openCreate = () => {
    setCode('');
    setTitle('');
    setRuleId(availableRules[0]?.id ?? '');
    setTrainingRoomQuota(3);
    setCreateOpen(true);
  };
  const openEdit = () => {
    if (!selected) return;
    setCode(selected.code);
    setTitle(selected.title);
    setRuleId(selected.rule_id);
    setTrainingRoomQuota(selected.training_room_quota);
    setEditing(true);
  };

  return <AdminI18nBoundary>{(
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-black tracking-[0.14em] text-blue-700">PAPER EXPERIMENT</p>
          <h1 className="mt-2 text-2xl font-black">论文实验管理</h1>
          <p className="mt-2 text-sm text-slate-600">固定排表、标注完成度、结果门禁与研究导出。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={openCreate}>
            <Plus className="size-4" /> 新建批次
          </Button>
          <Button onClick={() => void progress.refetch()} variant="secondary">
            <RefreshCcw className="size-4" /> 刷新进度
          </Button>
        </div>
        {selected?.status === 'PUBLISHED' ? (
          <Button
            disabled={disable.isPending}
            onClick={() => setDisableOpen(true)}
            variant="danger"
          >
            <ShieldOff className="size-4" /> 停用批次
          </Button>
        ) : null}
      </header>

      <section className="grid gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <aside className="rounded-lg border border-slate-200 bg-white p-3">
          <div className="flex items-center gap-2 border-b border-slate-100 px-2 pb-3">
            <Search className="size-4 text-slate-400" />
            <input
              aria-label="搜索实验批次"
              className="min-w-0 flex-1 text-sm outline-none"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索批次"
              value={search}
            />
          </div>
          <select
            aria-label="筛选批次状态"
            className="mt-2 min-h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs font-bold text-slate-600"
            onChange={(event) => setStatusFilter(event.target.value)}
            value={statusFilter}
          >
            <option value="ALL">全部状态</option>
            <option value="DRAFT">草稿</option>
            <option value="PUBLISHED">已发布</option>
            <option value="DISABLED">已停用</option>
          </select>
          <div className="mt-2 space-y-1">
            {filteredBatches.map((batch) => (
              <button
                className={`w-full rounded-lg p-3 text-left ${batch.id === selectedBatchId ? 'bg-blue-50 text-blue-800' : 'hover:bg-slate-50'}`}
                key={batch.id}
                onClick={() => setBatchId(batch.id)}
                type="button"
              >
                <span className="block truncate text-sm font-black">{batch.code}</span>
                <span className="mt-1 block truncate text-xs text-slate-600">{batch.title}</span>
                <span className="mt-2 inline-flex rounded-md bg-white px-2 py-1 text-[0.68rem] font-bold text-slate-600">
                  {batch.status === 'DRAFT'
                    ? '草稿'
                    : batch.status === 'PUBLISHED'
                      ? '已发布'
                      : '已停用'}
                </span>
              </button>
            ))}
            {!filteredBatches.length ? (
              <p className="px-2 py-8 text-center text-xs text-slate-500">暂无批次</p>
            ) : null}
          </div>
        </aside>
        <div className="min-w-0 space-y-4">
          {selected ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4">
              <div>
                <p className="text-xs font-bold text-slate-500">当前批次</p>
                <h2 className="mt-1 text-xl font-black">
                  {selected.code} · {selected.title}
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  {selected.status === 'DRAFT'
                    ? '草稿：可以继续修改'
                    : selected.status === 'PUBLISHED'
                      ? '已发布：配置已冻结'
                      : '已停用：历史数据只读'}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => setSettingsOpen(true)} size="sm" variant="secondary">
                  <FileSearch className="size-3.5" /> Agent 与 Prompt
                </Button>
                <a
                  className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  download
                  href={`/api/admin/experiments/batches/${selected.id}/schedule-readable.csv`}
                >
                  <Download className="size-3.5" /> 清晰排期表
                </a>
                <a
                  className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  download
                  href="/api/admin/experiments/accounts.csv"
                >
                  <Download className="size-3.5" /> 账号与密码
                </a>
                {selected.status === 'DRAFT' ? (
                  <>
                    <Button onClick={openEdit} size="sm" variant="secondary">
                      <Pencil className="size-3.5" /> 编辑基本信息
                    </Button>
                    <Button onClick={() => setDeleteOpen(true)} size="sm" variant="danger">
                      <Trash2 className="size-3.5" /> 删除草稿
                    </Button>
                  </>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-300 bg-white p-12 text-center">
              <h2 className="text-lg font-black">先选择一个批次</h2>
              <p className="mt-2 text-sm text-slate-500">或者新建一个实验批次开始配置。</p>
            </div>
          )}
        </div>
      </section>

      {selected?.status === 'DRAFT' ? (
        <AdminExperimentSetup
          batchId={selectedBatchId}
          key={selectedBatchId}
          ruleId={selected.rule_id}
        />
      ) : null}

      {selected && progress.data ? (
        <>
          <section className="grid gap-4 sm:grid-cols-3">
            <div className="border border-slate-200 bg-white p-5">
              <p className="text-xs font-bold text-slate-500">正式赛完成</p>
              <p className="mt-2 text-3xl font-black">
                {progress.data.formal_completed}/{progress.data.formal_total}
              </p>
            </div>
            <div className="border border-slate-200 bg-white p-5">
              <p className="text-xs font-bold text-slate-500">排表版本</p>
              <p className="mt-2 text-3xl font-black">v{selected.schedule_version}</p>
            </div>
            <div className="border border-slate-200 bg-white p-5">
              <p className="text-xs font-bold text-slate-500">批次状态</p>
              <p className="mt-2 text-3xl font-black">{batchStatusLabel(selected.status)}</p>
            </div>
          </section>
          <section className="overflow-hidden border border-slate-200 bg-white">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[58rem] border-collapse text-sm">
                <thead className="bg-slate-50 text-left text-xs text-slate-500">
                  <tr>
                    <th className="p-3">场次</th>
                    <th className="p-3">辩题</th>
                    <th className="p-3">双方</th>
                    <th className="p-3">状态</th>
                    <th className="p-3">参与者标注</th>
                    <th className="p-3">结果</th>
                    <th className="p-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {progress.data.matches.map((match) => (
                    <tr className="border-t border-slate-100" key={match.scheduled_match_id}>
                      <td className="p-3 font-black">
                        第 {match.round_no} 轮 · 第 {match.match_no} 场
                        <span className="mt-1 block text-xs font-semibold text-slate-500">
                          {match.kind === 'FORMAL' ? '正式赛' : '训练赛'}
                        </span>
                      </td>
                      <td className="max-w-[20rem] p-3 font-semibold">{match.topic_title}</td>
                      <td className="whitespace-nowrap p-3">
                        <span className="font-bold text-blue-700">
                          {match.affirmative_team_code}
                        </span>
                        <span className="px-1 text-slate-600">vs</span>
                        <span className="font-bold text-rose-700">{match.negative_team_code}</span>
                      </td>
                      <td className="whitespace-nowrap p-3">
                        <span className="font-bold">{matchStatusLabel(match.match_status)}</span>
                        <span className="block text-xs text-slate-500">
                          {match.attempt_no ? `第 ${match.attempt_no} 次尝试 · ` : ''}
                          {attemptStatusLabel(match.attempt_status)}
                        </span>
                      </td>
                      <td className="p-3 font-black">
                        {match.completed_annotations}/{match.total_annotations || 6}
                      </td>
                      <td className="p-3">{match.public_at ? '已公开' : '门禁中'}</td>
                      <td className="p-3 text-right">
                        {match.match_id ? (
                          <Link
                            className="mr-3 text-xs font-bold text-blue-700 hover:underline"
                            href={`/admin/matches/${match.match_id}`}
                          >
                            数据与请求日志
                          </Link>
                        ) : null}
                        {match.attempt_id &&
                        match.kind === 'FORMAL' &&
                        match.attempt_status === 'COMPLETED' &&
                        !match.public_at ? (
                          <Button
                            disabled={publish.isPending}
                            onClick={() => setPublishAttemptId(match.attempt_id)}
                            size="sm"
                            variant="secondary"
                          >
                            <Eye className="size-3.5" /> 公开
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="border border-slate-200 bg-white p-5">
            <h2 className="text-lg font-black">研究数据与保留期</h2>
            <div className="mt-4 flex flex-wrap gap-3">
              <Button disabled={createExport.isPending} onClick={() => createExport.mutate()}>
                <Download className="size-4" /> 生成匿名研究包
              </Button>
              <Button
                disabled={retention.isPending}
                onClick={() => retention.mutate()}
                variant="secondary"
              >
                <FileSearch className="size-4" /> 预览到期数据
              </Button>
            </div>
            {jobs.map((task) => (
              <TaskStatus key={task.id} task={task} />
            ))}
          </section>
        </>
      ) : progress.isPending && selectedBatchId && selected?.status !== 'DRAFT' ? (
        <div className="grid place-items-center py-20">
          <LoaderCircle className="size-8 animate-spin text-blue-600" />
        </div>
      ) : null}
      <Dialog.Root
        open={createOpen || editing}
        onOpenChange={(open) => {
          if (!open && !createBatch.isPending && !updateBatch.isPending) {
            setCreateOpen(false);
            setEditing(false);
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[100] bg-slate-950/35 backdrop-blur-[2px]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[101] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-lg border border-slate-200 bg-white p-6 shadow-2xl outline-none">
            <Dialog.Title className="text-lg font-black">
              {editing ? '编辑批次基本信息' : '新建实验批次'}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-slate-600">
              {editing
                ? '草稿状态下可以修改，发布后配置冻结。'
                : '先创建一个草稿，随后配置人员、Agent 和排表。'}
            </Dialog.Description>
            <div className="mt-5 space-y-4">
              <label className="block text-sm font-bold">
                批次编号
                <input
                  className="mt-1 min-h-11 w-full rounded-lg border border-slate-200 px-3 font-normal"
                  onChange={(event) => setCode(event.target.value.replace(/[^A-Za-z0-9_-]/g, ''))}
                  value={code}
                />
              </label>
              <label className="block text-sm font-bold">
                批次名称
                <input
                  className="mt-1 min-h-11 w-full rounded-lg border border-slate-200 px-3 font-normal"
                  onChange={(event) => setTitle(event.target.value)}
                  value={title}
                />
              </label>
              <label className="block text-sm font-bold">
                实验赛制
                <select
                  className="mt-1 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-3 font-normal"
                  onChange={(event) => setRuleId(event.target.value)}
                  value={ruleId}
                >
                  <option value="">选择赛制规则</option>
                  {availableRules.map((rule) => (
                    <option key={rule.id} value={rule.id}>
                      {rule.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm font-bold">
                每位参与者训练房间上限
                <input
                  className="mt-1 min-h-11 w-full rounded-lg border border-slate-200 px-3 font-normal"
                  max={20}
                  min={1}
                  onChange={(event) => setTrainingRoomQuota(Number(event.target.value))}
                  type="number"
                  value={trainingRoomQuota}
                />
              </label>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                onClick={() => {
                  setCreateOpen(false);
                  setEditing(false);
                }}
                variant="secondary"
              >
                取消
              </Button>
              <Button
                disabled={
                  !code.trim() ||
                  !title.trim() ||
                  !ruleId ||
                  trainingRoomQuota < 1 ||
                  trainingRoomQuota > 20 ||
                  createBatch.isPending ||
                  updateBatch.isPending
                }
                onClick={() => (editing ? updateBatch.mutate() : createBatch.mutate())}
              >
                {createBatch.isPending || updateBatch.isPending ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : null}
                {editing ? '保存修改' : '创建草稿'}
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root
        modal
        onOpenChange={(open) => {
          if (!open && !publish.isPending) {
            setPublishAttemptId(null);
            setPublishReason('');
          }
        }}
        open={Boolean(publishAttemptId)}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[100] bg-slate-950/35 backdrop-blur-[2px]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[101] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-lg border border-slate-200 bg-white p-6 shadow-2xl outline-none">
            <div className="flex items-start justify-between gap-4">
              <div>
                <Dialog.Title className="text-lg font-black text-slate-950">
                  提前公开比赛结果
                </Dialog.Title>
                <Dialog.Description className="mt-2 text-sm leading-6 text-slate-600">
                  此操作会立即向参与者公开评分并请求重算排行榜。原因将写入审计记录。
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button aria-label="关闭" disabled={publish.isPending} size="icon" variant="ghost">
                  <X className="size-4" />
                </Button>
              </Dialog.Close>
            </div>
            <label className="mt-5 block text-sm font-black text-slate-800">
              公开原因
              <textarea
                autoFocus
                className="mt-2 min-h-28 w-full resize-y rounded-lg border border-slate-200 p-3 font-normal leading-6 outline-none focus:border-blue-500"
                maxLength={500}
                onChange={(event) => setPublishReason(event.target.value)}
                placeholder="请说明为何绕过参与者标注完成门禁"
                value={publishReason}
              />
            </label>
            <div className="mt-5 flex justify-end gap-3">
              <Dialog.Close asChild>
                <Button disabled={publish.isPending} variant="secondary">
                  取消
                </Button>
              </Dialog.Close>
              <Button
                disabled={!publishReason.trim() || publish.isPending}
                onClick={() => {
                  if (!publishAttemptId || !publishReason.trim()) return;
                  publish.mutate({
                    attemptId: publishAttemptId,
                    reason: publishReason.trim(),
                  });
                }}
              >
                {publish.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
                确认公开
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root modal onOpenChange={setSettingsOpen} open={settingsOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[100] bg-slate-950/35 backdrop-blur-[2px]" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[101] max-h-[calc(100vh-2rem)] w-[calc(100%-2rem)] max-w-4xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-slate-200 bg-white p-6 shadow-2xl outline-none">
            <div className="flex items-start justify-between gap-4">
              <div>
                <Dialog.Title className="text-lg font-black">Agent 与 Prompt 设置</Dialog.Title>
                <Dialog.Description className="mt-2 text-sm text-slate-600">
                  当前批次固定映射和实际模板。单场原始输入输出从“数据与请求日志”进入。
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button aria-label="关闭" size="icon" variant="ghost">
                  <X className="size-4" />
                </Button>
              </Dialog.Close>
            </div>
            {roster.isPending || promptTemplates.isPending ? (
              <div className="grid place-items-center py-12">
                <LoaderCircle className="size-6 animate-spin text-blue-600" />
              </div>
            ) : (
              <div className="mt-6 space-y-6">
                <section>
                  <h3 className="text-sm font-black">固定队伍与 Agent</h3>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {(roster.data?.teams ?? []).map((team) => (
                      <div className="rounded-lg border border-slate-200 p-3" key={team.team_code}>
                        <p className="text-sm font-black">{team.team_code}</p>
                        <p className="mt-1 text-sm text-slate-600">
                          {catalog.data?.agents.find((agent) => agent.id === team.agent_profile_id)
                            ?.name ?? team.agent_profile_id}
                        </p>
                        <p className="mt-2 text-xs text-slate-500">
                          {team.members.map((member) => member.participant_code).join('、')}
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
                <section>
                  <h3 className="text-sm font-black">
                    Prompt 模板 · {promptTemplates.data?.version ?? '加载失败'}
                  </h3>
                  <div className="mt-3 grid gap-4 lg:grid-cols-2">
                    {(
                      [
                        ['决策 Prompt', promptTemplates.data?.decision_prompt],
                        ['发言 Prompt', promptTemplates.data?.speech_prompt],
                      ] as const
                    ).map(([label, value]) => (
                      <div className="min-w-0" key={label}>
                        <p className="text-xs font-bold text-slate-500">{label}</p>
                        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-4 text-xs leading-5 text-slate-100">
                          {value ?? '暂无模板'}
                        </pre>
                      </div>
                    ))}
                  </div>
                </section>
                <div className="flex flex-wrap gap-4 border-t border-slate-100 pt-4">
                  <Link
                    className="text-sm font-bold text-blue-700 hover:underline"
                    href="/admin/agents"
                  >
                    打开 Agent 管理
                  </Link>
                  <Link
                    className="text-sm font-bold text-blue-700 hover:underline"
                    href="/admin/logs"
                  >
                    打开系统日志
                  </Link>
                </div>
              </div>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <ConfirmDialog
        confirmLabel="删除草稿"
        description="将删除这个草稿中的阵容和排表。匿名账号、Agent、辩题和赛制不会删除；已有比赛记录时系统会拒绝。"
        loading={deleteBatch.isPending}
        onConfirm={() => deleteBatch.mutate()}
        onOpenChange={setDeleteOpen}
        open={deleteOpen}
        title="删除这个实验草稿？"
      />
      <ConfirmDialog
        confirmLabel="确认停用"
        description="停用后不能再创建或进入比赛，历史数据和研究导出仍保留。进行中或暂停的比赛必须先终止。"
        loading={disable.isPending}
        onConfirm={() => disable.mutate()}
        onOpenChange={setDisableOpen}
        open={disableOpen}
        title="停用实验批次？"
      />
    </div>
  )}</AdminI18nBoundary>;
}
