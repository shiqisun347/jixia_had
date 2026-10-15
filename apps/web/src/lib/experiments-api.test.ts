import { afterEach, describe, expect, it, vi } from 'vitest';

import { experimentsApi } from './experiments-api';

describe('experiment index pagination', () => {
  afterEach(() => vi.restoreAllMocks());

  it('follows the batch cursor while keeping each request bounded', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    fetchMock
      .mockResolvedValueOnce(
        new Response('[{"id":"first"}]', {
          status: 200,
          headers: { 'X-Next-Cursor': 'cursor-1' },
        }),
      )
      .mockResolvedValueOnce(new Response('[{"id":"second"}]', { status: 200 }));

    await expect(experimentsApi.adminBatches()).resolves.toEqual([
      { id: 'first' },
      { id: 'second' },
    ]);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/admin/experiments/batches?limit=50',
      expect.objectContaining({ credentials: 'include' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/admin/experiments/batches?limit=50&cursor=cursor-1',
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('rejects a repeated cursor instead of looping forever', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () => new Response('[]', { status: 200, headers: { 'X-Next-Cursor': 'same' } }),
    );
    await expect(experimentsApi.appointments()).rejects.toThrow('实验分页游标重复');
  });

  it('fails closed when an index page is not an array', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"items":[]}', { status: 200 }));

    await expect(experimentsApi.adminBatches()).rejects.toThrow('实验历史响应格式异常');
  });

  it('fails closed when an annotation task page is not an array', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"items":[]}', { status: 200 }));

    await expect(experimentsApi.participantTasks()).rejects.toThrow('实验标注任务响应格式异常');
  });

  it('bounds the cumulative number of annotation tasks across pages', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    const firstPage = Array.from({ length: 100 }, (_, index) => ({ id: String(index) }));
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify(firstPage), {
          status: 200,
          headers: { 'X-Next-Cursor': 'task-cursor' },
        }),
      )
      .mockResolvedValueOnce(new Response('[{"id":"overflow"}]', { status: 200 }));

    await expect(experimentsApi.participantTasks()).rejects.toThrow('实验标注任务超过单次加载上限');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('bounds cumulative UTF-8 annotation task payload bytes', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    fetchMock
      .mockResolvedValueOnce(
        new Response('[]', {
          status: 200,
          headers: { 'X-Next-Cursor': 'task-cursor' },
        }),
      )
      .mockResolvedValueOnce(new Response('[]', { status: 200 }));
    let encodeCalls = 0;
    vi.spyOn(TextEncoder.prototype, 'encode').mockImplementation(() => {
      encodeCalls += 1;
      return {
        byteLength: encodeCalls === 1 ? 40 * 1024 * 1024 : 30 * 1024 * 1024,
      } as unknown as Uint8Array<ArrayBuffer>;
    });

    await expect(experimentsApi.expertTasks()).rejects.toThrow('实验标注任务超过单次加载上限');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('fails closed after the bounded number of index pages', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    let calls = 0;
    fetchMock.mockImplementation(async () => {
      calls += 1;
      return new Response('[]', {
        status: 200,
        headers: { 'X-Next-Cursor': `index-cursor-${calls}` },
      });
    });

    await expect(experimentsApi.appointments()).rejects.toThrow('实验历史超过单次加载上限');
    expect(calls).toBe(200);
  });

  it('bounds annotation pages at 25 and rejects a repeated task cursor', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    fetchMock
      .mockResolvedValueOnce(
        new Response('[]', {
          status: 200,
          headers: { 'X-Next-Cursor': 'task-cursor' },
        }),
      )
      .mockResolvedValueOnce(
        new Response('[]', {
          status: 200,
          headers: { 'X-Next-Cursor': 'task-cursor' },
        }),
      );

    await expect(experimentsApi.participantTasks()).rejects.toThrow('实验分页游标重复');
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/experiments/annotation-tasks?limit=25',
      expect.objectContaining({ credentials: 'include' }),
    );
  });

  it('fails closed after the bounded number of annotation pages', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      const cursor = new URL(url, 'https://example.test').searchParams.get('cursor') ?? 'first';
      return new Response('[]', {
        status: 200,
        headers: { 'X-Next-Cursor': `${cursor}-next` },
      });
    });

    await expect(experimentsApi.expertTasks()).rejects.toThrow('实验标注任务超过单次加载上限');
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });
});
