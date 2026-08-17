import { describe, expect, it } from 'vitest';

import {
  authHrefWithReturnTo,
  buildReturnTo,
  sanitizeAuthReturnTo,
  sanitizeReturnTo,
} from './return-to';

describe('safe return targets', () => {
  it('preserves query strings and hash fragments', () => {
    expect(buildReturnTo('/lobby', 'join=1', '#room')).toBe('/lobby?join=1#room');
  });

  it('rejects external and ambiguous paths', () => {
    expect(sanitizeReturnTo('https://attacker.test', '/me')).toBe('/me');
    expect(sanitizeReturnTo('//attacker.test', '/me')).toBe('/me');
    expect(sanitizeReturnTo('/safe\\attacker', '/me')).toBe('/me');
  });
});

describe('sanitizeAuthReturnTo', () => {
  it('preserves room entry paths with their complete query', () => {
    expect(sanitizeAuthReturnTo('/lobby?join=1')).toBe('/lobby?join=1');
    expect(sanitizeAuthReturnTo('/join/JX8K2M?source=invite')).toBe('/join/JX8K2M?source=invite');
  });

  it('rejects external and authentication-loop destinations', () => {
    expect(sanitizeAuthReturnTo('https://attacker.test')).toBe('/');
    expect(sanitizeAuthReturnTo('/login?return_to=%2Flogin')).toBe('/');
    expect(sanitizeAuthReturnTo('/register')).toBe('/');
    expect(sanitizeAuthReturnTo('/change-password')).toBe('/');
  });

  it('only adds return_to when there is a non-root destination', () => {
    expect(authHrefWithReturnTo('/terms', '/')).toBe('/terms');
    expect(authHrefWithReturnTo('/terms', '/join/JX8K2M?source=invite')).toBe(
      '/terms?return_to=%2Fjoin%2FJX8K2M%3Fsource%3Dinvite',
    );
  });
});
