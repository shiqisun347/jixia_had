'use client';

import { NextIntlClientProvider } from 'next-intl';
import { usePathname } from 'next/navigation';
import {
  createContext,
  startTransition,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { messages, type AppLocale } from './messages';

const STORAGE_KEY = 'jx.locale.v1';
const LocaleContext = createContext<{ locale: AppLocale; setLocale: (locale: AppLocale) => void }>({
  locale: 'zh-CN',
  setLocale: () => undefined,
});

function readInitialLocale(): AppLocale {
  if (typeof window === 'undefined') return 'zh-CN';
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === 'en' || stored === 'zh-CN') return stored;
  return navigator.language.toLowerCase().startsWith('en') ? 'en' : 'zh-CN';
}

function applyDocumentLocale(locale: AppLocale) {
  document.documentElement.lang = locale;
  document.documentElement.dataset.locale = locale;
  document.title = locale === 'en' ? 'Jixia · Live Human–AI Debate' : '稷下 · 人机实时辩论实验平台';
  const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
  if (description) {
    description.content =
      locale === 'en'
        ? 'A live debate platform for human participants and AI agents.'
        : '让人类与 Agent 在声音中交锋与共创的实时辩论实验平台。';
  }
  const social = {
    'og:title': document.title,
    'og:description': description?.content ?? '',
    'twitter:title': document.title,
    'twitter:description': description?.content ?? '',
  };
  Object.entries(social).forEach(([property, content]) => {
    const selector = property.startsWith('og:')
      ? `meta[property="${property}"]`
      : `meta[name="${property}"]`;
    const tag =
      document.querySelector<HTMLMetaElement>(selector) ??
      document.head.appendChild(document.createElement('meta'));
    if (property.startsWith('og:')) tag.setAttribute('property', property);
    else tag.setAttribute('name', property);
    tag.content = content;
  });
  document.cookie = `jx_locale=${locale}; path=/; max-age=31536000; samesite=lax`;
}

export function LocaleProvider({
  children,
  initialLocale,
}: Readonly<{ children: ReactNode; initialLocale?: AppLocale }>) {
  const pathname = usePathname();
  const [locale, setLocaleState] = useState<AppLocale>(() => initialLocale ?? readInitialLocale());
  const setLocale = useCallback((nextLocale: AppLocale) => {
    window.localStorage.setItem(STORAGE_KEY, nextLocale);
    applyDocumentLocale(nextLocale);
    startTransition(() => setLocaleState(nextLocale));
  }, []);

  useEffect(() => applyDocumentLocale(locale), [locale, pathname]);
  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  return (
    <LocaleContext.Provider value={value}>
      <NextIntlClientProvider locale={locale} messages={messages[locale]} timeZone="Asia/Shanghai">
        {children}
      </NextIntlClientProvider>
    </LocaleContext.Provider>
  );
}

export function useAppLocale() {
  return useContext(LocaleContext);
}

type TranslationValues = Record<string, string | number | Date> | undefined;

function lookup(locale: AppLocale, namespace: string, key: string): string {
  const value = key.split('.').reduce<unknown>(
    (current, segment) => {
      if (current && typeof current === 'object' && segment in current) {
        return (current as Record<string, unknown>)[segment];
      }
      return undefined;
    },
    (messages[locale] as unknown as Record<string, unknown>)[namespace],
  );
  return typeof value === 'string' ? value : `${namespace}.${key}`;
}

export function useAppTranslations(namespace: keyof typeof messages.en) {
  const { locale } = useAppLocale();
  return useCallback(
    (key: string, values?: TranslationValues) =>
      lookup(locale, namespace, key).replace(/\{(\w+)\}/g, (_, name: string) =>
        String(values?.[name] ?? `{${name}}`),
      ),
    [locale, namespace],
  );
}

export function getLocalizedApiError(code: string, fallback: string, locale: AppLocale): string {
  if (code === 'match_command_failed') return messages[locale].Match.connectionUnavailable;
  if (code === 'invalid_api_response') return messages[locale].Match.saveFailed;
  return fallback;
}
