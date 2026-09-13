import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));
vi.mock('next/navigation', () => ({ useParams: () => ({ ruleId: 'rule-1' }) }));

import RuleWorkspacePage from './page';

const workspace = {
  rule: {
    id: 'rule-1',
    name: '论文实验规则',
    description: '',
    side_size: 4,
    estimated_seconds: 900,
    status: 'DISABLED',
    config_revision: 1,
    historical_read_only: false,
    topic_policy: 'BOTH',
  },
  agent_count: 8,
  stages: [
    {
      id: 'stage-1',
      position: 1,
      name: '正方立论',
      stage_kind: 'FIXED_SPEECH',
      duration_seconds: 0,
      start_host_text: '',
      end_host_text: '',
      parameters: {},
      actions: [{ id: 'action-1', side: 'AFFIRMATIVE', seat_no: 1, duration_seconds: 90 }],
      prompts: [{ purpose: 'SPEECH', template_text: '原 Prompt', variables: ['TOPIC'] }],
    },
    {
      id: 'stage-2',
      position: 2,
      name: '自由辩论',
      stage_kind: 'FREE_DEBATE',
      duration_seconds: 360,
      start_host_text: '',
      end_host_text: '',
      parameters: { max_speech_seconds: 30, starting_side: 'AFFIRMATIVE' },
      actions: [],
      prompts: [
        {
          purpose: 'SPEECH',
          template_text: '自由发言 {{DEBATE_HISTORY}}',
          variables: ['DEBATE_HISTORY'],
        },
        {
          purpose: 'DECISION',
          template_text: '自由决策 {{DEBATE_HISTORY}}',
          variables: ['DEBATE_HISTORY'],
        },
      ],
    },
  ],
  judge: {
    enabled: false,
    model_profile_id: null,
    judge_prompt: '',
    include_in_leaderboard: false,
  },
};
const catalog = {
  models: [{ id: 'model-1', name: 'Qwen', status: 'ENABLED' }],
  voices: [],
  agents: [],
  topics: [],
  rules: [],
};

describe('rule workspace', () => {
  afterEach(cleanup);
  beforeEach(() => {
    requestJson.mockReset();
    requestJson.mockImplementation((path: string) =>
      path.endsWith('/workspace')
        ? Promise.resolve(workspace)
        : path === '/api/admin/catalog'
          ? Promise.resolve(catalog)
          : Promise.resolve({}),
    );
  });

  it('edits a stage Prompt in a drawer', async () => {
    render(<RuleWorkspacePage />);
    fireEvent.click(await screen.findByRole('button', { name: /正方立论/ }));
    fireEvent.change(screen.getByDisplayValue('原 Prompt'), {
      target: { value: '新 Prompt {{TOPIC}}' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存 Prompt' }));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        '/api/admin/rules/rule-1/stages/stage-1/prompts/SPEECH',
        expect.objectContaining({ method: 'PUT' }),
      ),
    );
  });

  it('saves each free-debate Prompt to its selected slot', async () => {
    render(<RuleWorkspacePage />);
    fireEvent.click(await screen.findByRole('button', { name: /自由辩论/ }));
    fireEvent.change(screen.getByDisplayValue('自由发言 {{DEBATE_HISTORY}}'), {
      target: { value: '新版自由发言 {{DEBATE_HISTORY}}' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存 Prompt' }));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        '/api/admin/rules/rule-1/stages/stage-2/prompts/SPEECH',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ template_text: '新版自由发言 {{DEBATE_HISTORY}}' }),
        }),
      ),
    );

    fireEvent.click(await screen.findByRole('button', { name: /自由辩论/ }));
    fireEvent.change(screen.getByLabelText('Prompt 槽位'), { target: { value: 'DECISION' } });
    fireEvent.change(screen.getByDisplayValue('自由决策 {{DEBATE_HISTORY}}'), {
      target: { value: '新版自由决策 {{DEBATE_HISTORY}}' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存 Prompt' }));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        '/api/admin/rules/rule-1/stages/stage-2/prompts/DECISION',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ template_text: '新版自由决策 {{DEBATE_HISTORY}}' }),
        }),
      ),
    );
  });

  it('saves minimal rule-owned judge configuration', async () => {
    render(<RuleWorkspacePage />);
    fireEvent.click(await screen.findByRole('button', { name: /AI 裁判/ }));
    fireEvent.click(screen.getByLabelText('启用 AI 裁判'));
    fireEvent.change(screen.getByLabelText('全局模型'), { target: { value: 'model-1' } });
    fireEvent.change(screen.getByLabelText('裁判 Prompt'), {
      target: { value: '请裁决 {{DEBATE_HISTORY}}' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存裁判配置' }));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        '/api/admin/rules/rule-1/judge',
        expect.objectContaining({ method: 'PUT' }),
      ),
    );
  });
});
