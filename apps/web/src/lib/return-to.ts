const CONTROL_OR_BACKSLASH = /[\\\u0000-\u001f\u007f]/;

export function sanitizeReturnTo(value: string | null | undefined, fallback = '/'): string {
  if (
    !value ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    CONTROL_OR_BACKSLASH.test(value)
  ) {
    return fallback;
  }
  try {
    const parsed = new URL(value, 'https://jixia.invalid');
    if (parsed.origin !== 'https://jixia.invalid') return fallback;
  } catch {
    return fallback;
  }
  return value;
}

export function sanitizeAuthReturnTo(value: string | null | undefined, fallback = '/'): string {
  const returnTo = sanitizeReturnTo(value, fallback);
  try {
    const pathname = new URL(returnTo, 'https://jixia.invalid').pathname;
    if (['/login', '/register', '/change-password'].includes(pathname)) return fallback;
  } catch {
    return fallback;
  }
  return returnTo;
}

export function authHrefWithReturnTo(path: string, returnTo: string): string {
  return returnTo === '/' ? path : `${path}?return_to=${encodeURIComponent(returnTo)}`;
}

export function buildReturnTo(pathname: string, search: string, hash = ''): string {
  const query = search ? (search.startsWith('?') ? search : `?${search}`) : '';
  const fragment = hash ? (hash.startsWith('#') ? hash : `#${hash}`) : '';
  return sanitizeReturnTo(`${pathname || '/'}${query}${fragment}`);
}
