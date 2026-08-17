import { afterEach, describe, expect, it, vi } from 'vitest';

import { requestJson } from './auth-api';
import { SESSION_EXPIRED_EVENT } from './session-events';

describe('requestJson session expiry events', () => {
  afterEach(() => vi.restoreAllMocks());

  it('notifies the app only for an expired session response', async () => {
    const listener = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          error: { code: 'session_expired', message: '登录状态已失效，请重新登录' },
        }),
        { status: 401, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    await expect(requestJson('/api/lobby/rooms')).rejects.toMatchObject({
      status: 401,
      code: 'session_expired',
    });
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
  });

  it('normalizes a successful HTML response instead of leaking a JSON syntax error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response('<html>temporarily unavailable</html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html' },
      }),
    );

    await expect(requestJson('/api/legal/platform-terms/current')).rejects.toMatchObject({
      status: 200,
      code: 'invalid_api_response',
      message: '服务返回异常，请稍后重试。',
    });
  });

  it('accepts valid JSON even when the response omits content type', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response('{"ok":true}', { status: 200 }),
    );

    await expect(requestJson<{ ok: boolean }>('/api/health')).resolves.toEqual({ ok: true });
  });

  it.each([
    [401, 'not_authenticated'],
    [403, 'forbidden'],
  ])('does not notify for HTTP %s with %s', async (status, code) => {
    const listener = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, listener);
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code, message: '请求失败' } }), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(requestJson('/api/test')).rejects.toBeTruthy();
    expect(listener).not.toHaveBeenCalled();
    window.removeEventListener(SESSION_EXPIRED_EVENT, listener);
  });
});
