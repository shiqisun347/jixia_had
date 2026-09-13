import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { requestJson } = vi.hoisted(() => ({ requestJson: vi.fn() }));

vi.mock('@/lib/auth-api', () => ({ requestJson }));

import { LeaderboardPage } from './leaderboard-page';

describe('LeaderboardPage', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('makes the wide table region keyboard-focusable', async () => {
    requestJson.mockResolvedValue({
      generated_at: '2026-08-20T00:00:00Z',
      human: [
        {
          rank: 1,
          participant_id: 'human-1',
          display_name: '测试辩手',
          points: 10,
          matches: 2,
          wins: 1,
          average_personal_score: 8.5,
        },
      ],
      agent: [],
    });

    render(<LeaderboardPage />);

    const region = await waitFor(() => screen.getByRole('region', { name: '排行榜数据表' }));
    expect(region).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('table')).toBeVisible();
  });
});
