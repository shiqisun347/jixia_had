'use client';

import { Languages } from 'lucide-react';

import { useAppLocale, useAppTranslations } from '@/i18n';

export function LocaleSwitcher() {
  const { locale, setLocale } = useAppLocale();
  const t = useAppTranslations('Locale');
  return (
    <div className="locale-switcher" aria-label="Language selector" role="group">
      <Languages aria-hidden="true" className="size-3.5" />
      <button aria-label={t('switchToChinese')} aria-pressed={locale === 'zh-CN'} className={locale === 'zh-CN' ? 'is-active' : ''} onClick={() => setLocale('zh-CN')} type="button">中文</button>
      <span aria-hidden="true">/</span>
      <button aria-label={t('switchToEnglish')} aria-pressed={locale === 'en'} className={locale === 'en' ? 'is-active' : ''} onClick={() => setLocale('en')} type="button">EN</button>
    </div>
  );
}
