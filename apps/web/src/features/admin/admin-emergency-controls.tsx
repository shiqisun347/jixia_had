'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useOptionalToast } from '@/components/ui/toast-provider';
import { useAppLocale } from '@/i18n';
import { AdminButton, AdminConfirmDialog } from './admin-controls';
import { adminApi, readableAdminError } from './admin-api';
import type { MatchControlRequest, MatchControlTarget } from './admin-types';

type RecoveryAction = 'reset_stage' | 'reset_speech' | 'skip_speech';
type Selection = { action: RecoveryAction; target: MatchControlTarget; operationId: string };

export function AdminEmergencyControls({ matchId }: Readonly<{ matchId: string }>) {
  const { locale } = useAppLocale();
  const say = (zh: string, en: string) => (locale === 'en' ? en : zh);
  const toast = useOptionalToast();
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<Selection | null>(null);
  const [busy, setBusy] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const synchronousGate = useRef(false);
  const target = useQuery({
    queryKey: ['admin', 'match-control-target', matchId],
    queryFn: () => adminApi.matchControlTarget(matchId),
    refetchInterval: (query) =>
      query.state.data && ['FINISHED', 'TERMINATED'].includes(query.state.data.status)
        ? false
        : 1500,
  });
  const data = target.data;
  const operation = data?.operation;
  const pending = operation?.status === 'PENDING';
  const label = (action: RecoveryAction, value = data) =>
    ({
      reset_stage: say(
        `重置当前阶段（${value?.stage_name ?? '…'}）`,
        `Reset stage (${value?.stage_name ?? '…'})`,
      ),
      reset_speech: say('重置当前发言', 'Reset current speech'),
      skip_speech: say('跳过当前发言 · AI占位', 'Skip speech · AI placeholder'),
    })[action];
  const open = (action: RecoveryAction) => {
    if (data) {
      setOperationError(null);
      setSelection({ action, target: data, operationId: crypto.randomUUID() });
    }
  };
  const refresh = async () => {
    await target.refetch();
    await queryClient.invalidateQueries({ predicate: (query) => query.queryKey.includes(matchId) });
  };
  const submit = async (payload: MatchControlRequest) => {
    if (synchronousGate.current) return;
    synchronousGate.current = true;
    setBusy(true);
    setOperationError(null);
    try {
      await adminApi.emergencyControlMatch(matchId, payload);
      setSelection(null);
      await refresh();
      toast?.showToast({
        message: say(
          '应急操作已受理，比赛保持受控状态，请查看处理结果。',
          'Recovery accepted. Check the result before resuming.',
        ),
        tone: 'success',
      });
    } catch (error) {
      const message = readableAdminError(error);
      setOperationError(message);
      toast?.showToast({ message, tone: 'error' });
      await target.refetch();
    } finally {
      synchronousGate.current = false;
      setBusy(false);
    }
  };
  const confirm = () => {
    if (!selection || selection.target.action_key === null) return;
    const value = selection.target;
    void submit({
      action: selection.action,
      operation_id: selection.operationId,
      expected_sequence: value.sequence,
      expected_action_key: value.action_key,
      expected_stage_generation: value.stage_generation,
      expected_speech_id: value.speech_id,
      expected_context_version: value.context_version,
    });
  };
  const description =
    selection?.action === 'reset_stage'
      ? say(
          '将作废当前阶段的发言与音频，恢复阶段起点并暂停。之前阶段记录保留；恢复后重新播放本阶段主持。',
          'Invalidate this stage’s speeches and audio, return to its beginning and pause. Previous stages remain. Resume to replay the stage introduction.',
        )
      : selection?.action === 'skip_speech'
        ? say(
            '将停止本次发言，由AI生成明确标记的占位文字，然后跳到下一回合并保持暂停。占位不播报、不作为该辩手实绩评分。',
            'Stop this attempt, generate labelled AI placeholder text and move to the next turn while paused. The placeholder is neither spoken nor scored as the debater’s performance.',
          )
        : say(
            '将清除本次发言的活动请求、文字和音频，恢复本次完整额度并保持暂停。其他发言记录保留。',
            'Retire this attempt’s requests, text and audio, restore its allowance and pause. Other speeches remain.',
          );
  return (
    <section
      className="space-y-3 rounded-xl border border-amber-200 bg-amber-50/60 p-3"
      aria-label={say('管理员应急恢复', 'Administrator recovery')}
    >
      <p className="text-sm font-bold">{say('管理员应急恢复', 'Administrator recovery')}</p>
      <p className="text-xs text-slate-600">
        {say(
          '重置或跳过后保持暂停，确认状态后再恢复比赛。',
          'Reset and skip leave the match paused. Check the state before resuming.',
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        <AdminButton
          tone="secondary"
          disabled={busy || pending || !data?.can_reset_stage}
          onClick={() => open('reset_stage')}
        >
          {label('reset_stage')}
        </AdminButton>
        <AdminButton
          tone="secondary"
          disabled={busy || pending || !data?.can_reset_speech}
          onClick={() => open('reset_speech')}
        >
          {label('reset_speech')}
        </AdminButton>
        <AdminButton
          tone="secondary"
          disabled={busy || pending || !data?.can_skip_speech}
          onClick={() => open('skip_speech')}
        >
          {label('skip_speech')}
        </AdminButton>
        <AdminButton tone="ghost" disabled={busy} onClick={() => void target.refetch()}>
          {say('刷新状态', 'Refresh state')}
        </AdminButton>
      </div>
      {target.isError ? (
        <p role="alert" className="text-xs text-red-700">
          {say(
            '无法读取当前恢复目标，请刷新后重试。',
            'Cannot load the recovery target. Refresh and try again.',
          )}
        </p>
      ) : null}
      {pending ? (
        <div className="flex flex-wrap items-center gap-2 text-xs" role="status">
          <span>
            {say(
              'AI占位正在生成，比赛已暂停。',
              'Generating the AI placeholder. The match is paused.',
            )}
          </span>
          <AdminButton
            tone="ghost"
            disabled={busy}
            onClick={() =>
              typeof operation?.id === 'string' &&
              void submit({ action: 'cancel_skip', operation_id: operation.id })
            }
          >
            {say('取消生成', 'Cancel generation')}
          </AdminButton>
        </div>
      ) : null}
      {operation?.status === 'FAILED' || operation?.status === 'CANCELLED' ? (
        <p role="status" className="text-xs text-amber-800">
          {say(
            '占位未生成，比赛保持暂停。可以重新尝试或重置当前发言。',
            'No placeholder was generated. The match is paused; retry or reset the current speech.',
          )}
        </p>
      ) : null}
      {operation?.status === 'SUCCEEDED' ? (
        <p role="status" className="text-xs text-emerald-800">
          {say(
            '应急操作已完成，检查当前阶段后可恢复比赛。',
            'Recovery completed. Check the stage before resuming.',
          )}
        </p>
      ) : null}
      <AdminConfirmDialog
        open={selection !== null}
        onOpenChange={(value) => {
          if (!value && !busy) setSelection(null);
        }}
        title={selection ? label(selection.action, selection.target) : ''}
        description={operationError ? `${description} ${operationError}` : description}
        confirmLabel={say('确认执行', 'Confirm')}
        loading={busy}
        onConfirm={confirm}
      />
    </section>
  );
}
