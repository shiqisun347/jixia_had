'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useOptionalToast } from '@/components/ui/toast-provider';
import { useAppLocale } from '@/i18n';
import { AdminButton, AdminConfirmDialog } from './admin-controls';
import { adminApi, readableAdminError } from './admin-api';
import type { MatchControlRequest, MatchControlTarget } from './admin-types';

type RecoveryAction = 'reset_stage' | 'reset_speech' | 'skip_speech' | 'retry_asr';
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
  const asrRetry = operation?.kind === 'asr.retry';
  const label = (action: RecoveryAction, value = data) =>
    ({
      reset_stage: say(
        `重置当前阶段（${value?.stage_name ?? '…'}）`,
        `Reset stage (${value?.stage_name ?? '…'})`,
      ),
      reset_speech: say('重置当前发言', 'Reset current speech'),
      skip_speech: say('跳过当前发言 · AI占位', 'Skip speech · AI placeholder'),
      retry_asr: say('重试上一段语音识别', 'Retry previous speech recognition'),
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
          'Recovery accepted. Check the current state and result.',
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
            '将安全停止本次发言，由AI生成明确标记的占位文字，再进入下一回合。原本运行中的比赛通过恢复检查后自动继续；原本已暂停的比赛仍保持暂停。占位不播报、不作为辩手实绩评分。',
            'Safely stop this attempt and generate labelled placeholder text before moving to the next turn. A running match continues automatically after recovery checks; an already paused match stays paused. The placeholder is neither spoken nor scored.',
          )
        : selection?.action === 'retry_asr'
          ? say(
              '保留原发言和已确认文字，使用原录音重新识别，麦克风保持关闭。成功后由系统检查并继续；失败时可重新尝试、重置或安全跳过。',
              'Keep the original speech and confirmed text, and recognize its saved recording again with the microphone off. The system checks and continues after success; on failure, retry, reset or safely skip.',
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
          '重置后保持暂停。跳过或重试识别成功后，由系统检查是否可以继续。',
          'Reset leaves the match paused. After a successful skip or recognition retry, the system checks whether it can continue.',
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        <AdminButton
          tone="secondary"
          disabled={busy || pending || !data?.can_retry_asr}
          onClick={() => open('retry_asr')}
        >
          {label('retry_asr')}
        </AdminButton>
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
              asrRetry ? '正在重试原录音识别，麦克风保持关闭。' : 'AI占位正在生成，比赛已暂停。',
              asrRetry
                ? 'Retrying the saved recording. The microphone remains off.'
                : 'Generating the AI placeholder. The match is paused.',
            )}
          </span>
          {!asrRetry ? (
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
          ) : null}
        </div>
      ) : null}
      {operation?.status === 'FAILED' || operation?.status === 'CANCELLED' ? (
        <p role="status" className="text-xs text-amber-800">
          {say(
            asrRetry
              ? '语音识别未完成，原记录仍保留。可以重试识别、重置或安全跳过。'
              : '占位未生成，比赛保持暂停。可以重新尝试或重置当前发言。',
            asrRetry
              ? 'Recognition did not finish. Existing records remain; retry recognition, reset or safely skip.'
              : 'No placeholder was generated. The match is paused; retry or reset the current speech.',
          )}
        </p>
      ) : null}
      {operation?.status === 'SUCCEEDED' ? (
        <p role="status" className="text-xs text-emerald-800">
          {say(
            data?.status === 'RUNNING'
              ? '应急操作已完成，比赛已继续。'
              : '应急操作已完成，请查看当前状态和恢复条件。',
            data?.status === 'RUNNING'
              ? 'Recovery completed. The match has continued.'
              : 'Recovery completed. Check the current state and recovery requirements.',
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
