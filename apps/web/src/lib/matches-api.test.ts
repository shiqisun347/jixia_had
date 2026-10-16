import { afterEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { matchesApi, newestMatchTranscript, type MatchTranscript } from './matches-api';

describe('match transcript response boundaries', () => {
  afterEach(() => vi.restoreAllMocks());

  it.each([
    null,
    {},
    { match_id: 'match', context_version: 1, speeches: null },
    { match_id: 'match', context_version: 1, speeches: [{ id: 'speech', action_key: null }] },
    {
      match_id: 'match',
      context_version: 1,
      speeches: [{ id: 'speech', action_key: '1:1', display_text: { raw: 'invalid' } }],
    },
  ])(
    'normalizes a malformed successful transcript instead of passing it to render: %j',
    async (body) => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
        new Response(JSON.stringify(body), { status: 200 }),
      );
      await expect(matchesApi.transcript('match')).rejects.toMatchObject({
        code: 'invalid_api_response',
      });
    },
  );

  it('retains the official empty transcript as a successful empty state', async () => {
    const body = { match_id: 'match', context_version: 0, speeches: [] };
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(body), { status: 200 }),
    );
    await expect(matchesApi.transcript('match')).resolves.toEqual(body);
  });

  it('keeps previously loaded records when a refresh returns malformed JSON data', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = ['matches', 'match', 'transcript'];
    const official = {
      match_id: 'match',
      context_version: 5,
      speeches: [{ id: 'official', display_text: '已保存的正式发言' }],
    };
    client.setQueryData(key, official);
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response('{"speeches":null}', { status: 200 }),
    );
    await expect(
      client.fetchQuery({ queryKey: key, queryFn: () => matchesApi.transcript('match') }),
    ).rejects.toMatchObject({ code: 'invalid_api_response' });
    expect(client.getQueryData(key)).toEqual(official);
    client.clear();
  });

  it('does not let a delayed read replace an edited or reset transcript at a newer context version', () => {
    const newer = { match_id: 'match', context_version: 6, speeches: [] } as MatchTranscript;
    const older = { ...newer, context_version: 5 };
    expect(newestMatchTranscript(newer, older)).toBe(newer);
    expect(newestMatchTranscript(older, newer)).toBe(newer);
  });
});
