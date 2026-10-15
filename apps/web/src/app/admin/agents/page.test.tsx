import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { requestJson, showToast } = vi.hoisted(() => ({ requestJson: vi.fn(), showToast: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));
vi.mock('@/components/ui/toast-provider', () => ({ useOptionalToast: () => ({ showToast }) }));

import AdminAgentsPage from './page';

const rule = {
  id: 'rule-1',
  name: '论文实验规则',
  description: '',
  side_size: 4,
  estimated_seconds: 900,
  status: 'DISABLED',
  config_revision: 1,
  default_agent_model_profile_id: 'model-1',
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
    showToast.mockReset();
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
  it('changes only the selected rule switch and sends a status-only request', async () => {
    const rules = [rule, { ...rule, id: 'rule-2', name: '赛制 B' }];
    const statuses: Record<string, string> = { 'rule-1': 'ENABLED', 'rule-2': 'ENABLED' };
    requestJson.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/api/admin/catalog') return Promise.resolve(catalog);
      if (path === '/api/admin/rules') return Promise.resolve(rules);
      const id = path.includes('rule-2') ? 'rule-2' : 'rule-1';
      if (path.endsWith('/status')) {
        const body = JSON.parse(String(init?.body)) as { status: string };
        statuses[id] = body.status;
        return Promise.resolve({ ...agent, rule_id: id, status: body.status });
      }
      if (path.endsWith('/agents'))
        return Promise.resolve([{ ...agent, rule_id: id, status: statuses[id] }]);
      return Promise.resolve(workspace);
    });
    render(<AdminAgentsPage />);
    fireEvent.change(await screen.findByLabelText('赛制规则'), { target: { value: 'rule-1' } });
    fireEvent.click(await screen.findByRole('switch', { name: '龙安灵希 本赛制启用' }));
    await waitFor(() => expect(screen.getByRole('switch')).not.toBeChecked());
    expect(requestJson).toHaveBeenCalledWith('/api/admin/rules/rule-1/agents/agent-1/status', {
      method: 'PATCH',
      body: JSON.stringify({ status: 'DISABLED' }),
    });
    fireEvent.change(screen.getByLabelText('赛制规则'), { target: { value: 'rule-2' } });
    expect(await screen.findByRole('switch')).toBeChecked();
    expect(statuses['rule-2']).toBe('ENABLED');
  });

  it('keeps the switch unchanged when saving fails', async () => {
    requestJson.mockImplementation((path: string) => {
      if (path.endsWith('/status')) return Promise.reject(new Error('save failed'));
      if (path === '/api/admin/catalog') return Promise.resolve(catalog);
      if (path === '/api/admin/rules') return Promise.resolve([rule]);
      if (path.endsWith('/agents')) return Promise.resolve([agent]);
      return Promise.resolve(workspace);
    });
    render(<AdminAgentsPage />);
    fireEvent.change(await screen.findByLabelText('赛制规则'), { target: { value: 'rule-1' } });
    fireEvent.click(await screen.findByRole('switch'));
    await waitFor(() => expect(screen.getByRole('switch')).toBeEnabled());
    expect(screen.getByRole('switch')).toBeChecked();
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith(
        '/api/admin/rules/rule-1/agents/agent-1/status',
        expect.any(Object),
      ),
    );
    await waitFor(() =>
      expect(showToast).toHaveBeenCalledWith({ message: 'save failed', tone: 'error' }),
    );
  });

  it('shows global voice unavailability without clearing the rule preference', async () => {
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/admin/catalog') return Promise.resolve(catalog);
      if (path === '/api/admin/rules') return Promise.resolve([rule]);
      if (path.endsWith('/agents'))
        return Promise.resolve([{ ...agent, voice_status: 'DISABLED', available: false }]);
      return Promise.resolve(workspace);
    });
    render(<AdminAgentsPage />);
    fireEvent.change(await screen.findByLabelText('赛制规则'), { target: { value: 'rule-1' } });
    expect(await screen.findByRole('switch')).toBeChecked();
    expect(screen.getByText('音色已停用')).toBeVisible();
  });

  it('ignores a late Agent list from the previously selected rule', async () => {
    let resolveOld!: (value: unknown) => void;
    const oldResponse = new Promise((resolve) => {
      resolveOld = resolve;
    });
    requestJson.mockImplementation((path: string) => {
      if (path === '/api/admin/catalog') return Promise.resolve(catalog);
      if (path === '/api/admin/rules')
        return Promise.resolve([rule, { ...rule, id: 'rule-2', name: '赛制 B' }]);
      if (path === '/api/admin/rules/rule-1/agents') return oldResponse;
      if (path === '/api/admin/rules/rule-2/agents')
        return Promise.resolve([{ ...agent, name: '赛制 B Agent' }]);
      return Promise.resolve(workspace);
    });
    render(<AdminAgentsPage />);
    fireEvent.change(await screen.findByLabelText('赛制规则'), { target: { value: 'rule-1' } });
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith('/api/admin/rules/rule-1/agents'));
    fireEvent.change(screen.getByLabelText('赛制规则'), { target: { value: 'rule-2' } });
    expect(await screen.findByText('赛制 B Agent')).toBeVisible();
    resolveOld([agent]);
    await waitFor(() =>
      expect(screen.queryByRole('switch', { name: '龙安灵希 本赛制启用' })).not.toBeInTheDocument(),
    );
    expect(screen.getByText('赛制 B Agent')).toBeVisible();
  });
});
