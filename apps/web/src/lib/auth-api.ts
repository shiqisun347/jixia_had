import type { components } from '@jx/contracts';

import { notifySessionExpired } from './session-events';

export type AuthResponse = components['schemas']['AuthResponse'];
export type ApiUser = components['schemas']['UserResponse'];
export type TermsResponse = components['schemas']['TermsResponse'];

interface ErrorEnvelope {
  readonly error?: {
    readonly code?: string;
    readonly message?: string;
    readonly field_errors?: Record<string, string>;
    readonly request_id?: string;
  };
}

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: Record<string, string>;
  readonly requestId?: string;

  constructor(status: number, payload: ErrorEnvelope) {
    const error = payload.error;
    super(error?.message ?? '请求失败，请稍后重试');
    this.name = 'ApiClientError';
    this.status = status;
    this.code = error?.code ?? 'unknown_error';
    this.fieldErrors = error?.field_errors ?? {};
    this.requestId = error?.request_id;
  }
}

const INVALID_API_RESPONSE_MESSAGE = '服务返回异常，请稍后重试。';

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let payload: ErrorEnvelope = {};
    try {
      payload = (await response.json()) as ErrorEnvelope;
    } catch {
      // The public error stays normalized even when an upstream proxy fails.
    }
    if (response.status === 401 && payload.error?.code === 'session_expired') {
      notifySessionExpired();
    }
    throw new ApiClientError(response.status, payload);
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiClientError(response.status, {
      error: { code: 'invalid_api_response', message: INVALID_API_RESPONSE_MESSAGE },
    });
  }
}

export const authApi = {
  currentUser: (signal?: AbortSignal) => requestJson<AuthResponse>('/api/auth/me', { signal }),
  currentTerms: () => requestJson<TermsResponse>('/api/legal/platform-terms/current'),
  register: (payload: {
    username: string;
    real_name: string;
    password: string;
    platform_terms_version: string;
    avatar_key: string;
  }) =>
    requestJson<AuthResponse>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  login: (payload: { username: string; password: string; return_to?: string }) =>
    requestJson<AuthResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify(payload) }),
  logout: () => requestJson<{ status: string }>('/api/auth/logout', { method: 'POST' }),
  changePassword: (payload: { current_password: string; new_password: string }) =>
    requestJson<AuthResponse>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateProfile: (realName: string) =>
    requestJson<AuthResponse>('/api/users/me', {
      method: 'PATCH',
      body: JSON.stringify({ real_name: realName }),
    }),
  uploadAvatar: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return requestJson<AuthResponse>('/api/users/me/avatar', { method: 'PUT', body });
  },
  deleteAvatar: () => requestJson<AuthResponse>('/api/users/me/avatar', { method: 'DELETE' }),
  selectAvatar: (avatarKey: string) =>
    requestJson<AuthResponse>('/api/users/me/avatar-preset', {
      method: 'PATCH',
      body: JSON.stringify({ avatar_key: avatarKey }),
    }),
};

export function avatarUrl(user: ApiUser): string {
  return `/api/users/${user.id}/avatar?v=${user.avatar_version ?? 0}`;
}
