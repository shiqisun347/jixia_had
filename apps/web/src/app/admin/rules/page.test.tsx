import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));
vi.mock('@/lib/auth-api', () => ({ requestJson }));

import AdminRulesPage from './page';

const rule = {
  id: 'rule-1',
  name: '论文实验规则',
  description: '三阶段',
  side_size: 4,
  estimated_seconds: 900,
  status: 'DISABLED',
  config_revision: 2,
  historical_read_only: false,
};
const catalog = {
  models: [{ id: 'model-1', name: 'Qwen', status: 'ENABLED' }],
  voices: [
    {
      id: 'host-1',
      name: '主持人',
      kind: 'HOST',
      provider_voice: 'host',
      rate: 1,
      chars_per_second: 4,
      status: 'ENABLED',
    },
  ],
  agents: [],
  topics: [],
  rules: [],
};

describe('rule directory', () => {
  afterEach(cleanup);
  beforeEach(() => {
    requestJson.mockReset();
    requestJson.mockImplementation((path: string) =>
      path === '/api/admin/rules' ? Promise.resolve([rule]) : Promise.resolve(catalog),
    );
  });

  it('shows a directory instead of a flat stage form', async () => {
    render(<AdminRulesPage />);
    expect(await screen.findByText('论文实验规则')).toBeVisible();
    expect(screen.getByRole('link', { name: /打开工作区/ })).toHaveAttribute(
      'href',
      '/admin/rules/rule-1',
    );
    expect(screen.queryByLabelText('阶段名称')).not.toBeInTheDocument();
  });

  it('creates from a focused drawer with the approved stage template', async () => {
    render(<AdminRulesPage />);
    fireEvent.click(await screen.findByRole('button', { name: '新建规则' }));
    fireEvent.change(screen.getByLabelText('规则名称'), { target: { value: '新规则' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    expect(requestJson).toHaveBeenCalledWith(
      '/api/admin/rules',
      expect.objectContaining({ method: 'POST' }),
    );
    const call = requestJson.mock.calls.find(
      ([path, options]) => path === '/api/admin/rules' && options?.method === 'POST',
    );
    const body = JSON.parse(call?.[1]?.body as string);
    expect(body.draft.stages.map((stage: { name: string }) => stage.name)).toEqual([
      '正方立论',
      '反方立论',
      '自由辩论',
      '比赛结束',
    ]);
    expect(body.draft.stages[2].parameters.starting_side).toBe('AFFIRMATIVE');
  });
});
