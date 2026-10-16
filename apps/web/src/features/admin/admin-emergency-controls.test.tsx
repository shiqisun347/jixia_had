import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/ui/toast-provider';
import { AdminEmergencyControls } from './admin-emergency-controls';
import { adminApi } from './admin-api';
import type { MatchControlTarget } from './admin-types';

vi.mock('./admin-api', () => ({
  adminApi: { matchControlTarget: vi.fn(), emergencyControlMatch: vi.fn() },
  readableAdminError: () => '操作目标已变化，比赛保持暂停，请重新确认。',
}));

const target = {
  match_id: 'match-304',
  status: 'PAUSED',
  sequence: 10,
  action_key: '1:1',
  stage_name: '立论',
  stage_generation: 2,
  speech_id: 'old-speech',
  context_version: 3,
  can_reset_stage: true,
  can_reset_speech: true,
  can_skip_speech: true,
  operation: null,
} as unknown as MatchControlTarget;
let client: QueryClient;

function mount(value = target) {
  vi.mocked(adminApi.matchControlTarget).mockResolvedValue(value);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AdminEmergencyControls matchId="match-304" />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());
afterEach(() => {
  cleanup();
  client?.clear();
});

describe('administrator emergency controls', () => {
  it('submits a frozen ASR retry target and never submits a resume command', async () => {
    mount({ ...target, status: 'ERROR', can_retry_asr: true });
    const retry = await screen.findByRole('button', { name: '重试上一段语音识别' });
    await waitFor(() => expect(retry).toBeEnabled());
    fireEvent.click(retry);
    expect(screen.getByRole('alertdialog')).toHaveTextContent('保留原发言和已确认文字');
    const next = { ...target, sequence: 11, speech_id: 'next-speech' };
    act(() => client.setQueryData(['admin', 'match-control-target', 'match-304'], next));
    vi.mocked(adminApi.matchControlTarget).mockResolvedValue(next);
    vi.mocked(adminApi.emergencyControlMatch).mockResolvedValue({ status: 'PENDING' } as never);
    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
    await waitFor(() => expect(adminApi.emergencyControlMatch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(adminApi.emergencyControlMatch).mock.calls[0][1]).toMatchObject({
      action: 'retry_asr',
      expected_sequence: 10,
      expected_action_key: '1:1',
      expected_stage_generation: 2,
      expected_speech_id: 'old-speech',
      expected_context_version: 3,
    });
  });

  it('uses the Core retry capability and distinguishes ASR work from an AI placeholder', async () => {
    mount({
      ...target,
      can_retry_asr: false,
      operation: { id: 'retry-owned', kind: 'asr.retry', status: 'PENDING' },
    } as MatchControlTarget);
    expect(await screen.findByRole('button', { name: '重试上一段语音识别' })).toBeDisabled();
    expect(await screen.findByText('正在重试原录音识别，麦克风保持关闭。')).toBeVisible();
    expect(screen.queryByRole('button', { name: '取消生成' })).not.toBeInTheDocument();
    expect(screen.queryByText('AI占位正在生成，比赛已暂停。')).not.toBeInTheDocument();
  });

  it('keeps the confirmed target frozen when a refresh selects another stage', async () => {
    mount();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '重置当前阶段（立论）' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: '重置当前阶段（立论）' }));
    const next = { ...target, stage_name: '自由辩论', action_key: '3:0', sequence: 20 };
    act(() => client.setQueryData(['admin', 'match-control-target', 'match-304'], next));
    vi.mocked(adminApi.matchControlTarget).mockResolvedValue(next);
    vi.mocked(adminApi.emergencyControlMatch).mockRejectedValue(new Error('owned stale rejection'));
    fireEvent.click(screen.getByRole('button', { name: '确认执行' }));
    await waitFor(() => expect(adminApi.emergencyControlMatch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(adminApi.emergencyControlMatch).mock.calls[0][1]).toMatchObject({
      expected_sequence: 10,
      expected_action_key: '1:1',
      expected_speech_id: 'old-speech',
    });
    expect(
      (await screen.findAllByText('操作目标已变化，比赛保持暂停，请重新确认。'))[0],
    ).toBeVisible();
    expect(screen.getByRole('alertdialog')).toHaveTextContent('重置当前阶段（立论）');
  });

  it('coalesces duplicate confirmation clicks using one stable operation id', async () => {
    let release: (() => void) | undefined;
    vi.mocked(adminApi.emergencyControlMatch).mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ status: 'SUCCEEDED' } as never);
        }),
    );
    mount();
    await waitFor(() => expect(screen.getByRole('button', { name: '重置当前发言' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '重置当前发言' }));
    const confirm = screen.getByRole('button', { name: '确认执行' });
    act(() => {
      fireEvent.click(confirm);
      fireEvent.click(confirm);
    });
    expect(adminApi.emergencyControlMatch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(adminApi.emergencyControlMatch).mock.calls[0][1].operation_id).toMatch(
      /^[0-9a-f-]{36}$/,
    );
    await act(async () => release?.());
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  });

  it('disables competing actions while pending and cancels only that operation', async () => {
    const pending = {
      ...target,
      operation: { id: 'operation-current', status: 'PENDING' },
    } as MatchControlTarget;
    mount(pending);
    const cancel = await screen.findByRole('button', { name: '取消生成' });
    expect(screen.getByRole('button', { name: '跳过当前发言 · AI占位' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重置当前发言' })).toBeDisabled();
    vi.mocked(adminApi.emergencyControlMatch).mockResolvedValue({ status: 'CANCELLED' } as never);
    vi.mocked(adminApi.matchControlTarget).mockResolvedValue({
      ...pending,
      operation: { ...pending.operation!, status: 'CANCELLED' },
    });
    fireEvent.click(cancel);
    await waitFor(() =>
      expect(adminApi.emergencyControlMatch).toHaveBeenCalledWith('match-304', {
        action: 'cancel_skip',
        operation_id: 'operation-current',
      }),
    );
    expect(
      await screen.findByText('占位未生成，比赛保持暂停。可以重新尝试或重置当前发言。'),
    ).toBeVisible();
  });

  it('retains a clear paused state after placeholder generation fails', async () => {
    mount({
      ...target,
      operation: { id: 'operation-failed', status: 'FAILED' },
    } as MatchControlTarget);
    expect(
      await screen.findByText('占位未生成，比赛保持暂停。可以重新尝试或重置当前发言。'),
    ).toBeVisible();
    expect(adminApi.emergencyControlMatch).not.toHaveBeenCalled();
  });
});
