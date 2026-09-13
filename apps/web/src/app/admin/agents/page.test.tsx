import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminAgentsPage from './page';

const rule = {
  id: 'rule-1',
  name: '论文实验规则',
  description: '',
  side_size: 4,
  estimated_seconds: 900,
  status: 'DISABLED',
  config_revision: 1,
  historical_read_only: false,
};
const catalog = {
  models: [{ id: 'model-1', name: 'Qwen', status: 'ENABLED' }],
  voices: [
    {
      id: 'voice-1',
      name: '龙安灵希',
      kind: 'AGENT',
      provider_voice: 'voice',
      rate: 1,
      chars_per_second: 4,
      avatar_key: 'agent-07',
      status: 'ENABLED',
    },
  ],
  agents: [],
  topics: [],
  rules: [],
};
const agent = {
  id: 'agent-1',
  rule_id: 'rule-1',
  name: '龙安灵希',
  model_profile_id: 'model-1',
  voice_profile_id: 'voice-1',
  generation_params: {},
  status: 'ENABLED',
  prompt_override_count: 0,
};
const workspace = {
  stages: [{ id: 'stage-1', name: '正方立论', prompts: [{ purpose: 'SPEECH' }] }],
};

describe('rule-scoped Agent management', () => {
  afterEach(cleanup);
  beforeEach(() => {
    window.localStorage.clear();
    requestJson.mockReset();
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/admin/catalog') return Promise.resolve(catalog);
      if (path === '/api/admin/rules') return Promise.resolve([rule]);
      if (path === '/api/admin/rules/rule-1/agents') return Promise.resolve([agent]);
      if (path === '/api/admin/rules/rule-1/workspace') return Promise.resolve(workspace);
      if (path.includes('/prompts/SPEECH'))
        return Promise.resolve({
          uses_default: true,
          template_text: '默认',
          default_template_text: '默认',
        });
      return Promise.resolve({});
    });
  });

  it('does not mix Agents before a rule is selected', async () => {
    render(<AdminAgentsPage />);
    expect(await screen.findByText('先选择一套赛制规则，再管理它的 Agent 池。')).toBeVisible();
    expect(screen.queryByText('龙安灵希')).not.toBeInTheDocument();
    expect(requestJson).not.toHaveBeenCalledWith(expect.stringContaining('/agents'));
  });

  it('loads only the selected rule pool and keeps identity read-only', async () => {
    render(<AdminAgentsPage />);
    fireEvent.change(await screen.findByLabelText('赛制规则'), { target: { value: 'rule-1' } });
    expect(await screen.findByText('使用赛制默认')).toBeVisible();
    expect(requestJson).toHaveBeenCalledWith('/api/admin/rules/rule-1/agents');

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    expect(
      await screen.findByText('基础身份跟随全局音色；这里只编辑规则内运行配置。'),
    ).toBeVisible();
    expect(screen.queryByLabelText('名称')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('音色')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        expect.stringContaining('/agents/agent-1/stages/stage-1/prompts/SPEECH'),
      ),
    );
  });
});
