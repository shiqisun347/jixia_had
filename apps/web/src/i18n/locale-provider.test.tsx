import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup } from '@testing-library/react';

import { LocaleProvider, useAppTranslations } from './locale-provider';
import { LocaleSwitcher } from '@/components/layout/locale-switcher';

function Probe() {
  const t = useAppTranslations('Navigation');
  return <p>{t('home')}</p>;
}

describe('LocaleProvider', () => {
  beforeEach(() => {
    cleanup();
    window.localStorage.clear();
    Object.defineProperty(window.navigator, 'language', { configurable: true, value: 'zh-CN' });
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    document.documentElement.lang = 'zh-CN';
    document.documentElement.removeAttribute('data-locale');
  });

  it('defaults to Chinese and persists an explicit English choice without navigation', () => {
    render(<LocaleProvider><LocaleSwitcher /><Probe /></LocaleProvider>);

    expect(screen.getByText('首页')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Switch to English' }));

    expect(screen.getByText('Home')).toBeVisible();
    expect(document.documentElement.lang).toBe('en');
    expect(window.localStorage.getItem('jx.locale.v1')).toBe('en');
    expect(screen.getByRole('button', { name: 'Switch to English' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('uses the persisted locale on a later mount', () => {
    window.localStorage.setItem('jx.locale.v1', 'en');
    render(<LocaleProvider><Probe /></LocaleProvider>);
    expect(screen.getByText('Home')).toBeVisible();
    expect(document.documentElement.lang).toBe('en');
  });

  it('uses English only when the browser language requests English', () => {
    Object.defineProperty(window.navigator, 'language', { configurable: true, value: 'en-GB' });
    render(<LocaleProvider><Probe /></LocaleProvider>);
    expect(screen.getByText('Home')).toBeVisible();
    expect(document.documentElement.lang).toBe('en');
  });
});
