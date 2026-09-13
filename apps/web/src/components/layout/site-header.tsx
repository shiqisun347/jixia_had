'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { CSSProperties, ReactNode } from 'react';

import { AuthNavigation } from '@/features/auth/auth-navigation';
import { useAppTranslations } from '@/i18n';

import { JixiaLogo } from '../brand/jixia-logo';
import { LocaleSwitcher } from './locale-switcher';

const navigation = [
  { label: 'home', href: '/' },
  { label: 'lobby', href: '/lobby' },
  { label: 'leaderboard', href: '/leaderboard' },
  { label: 'guide', href: '/guide' },
] as const;

function isCurrent(pathname: string, href: string) {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader({ authNavigation }: Readonly<{ authNavigation?: ReactNode }>) {
  const pathname = usePathname() || '/';
  const t = useAppTranslations('Navigation');
  if (pathname.startsWith('/admin')) return null;
  const compact = pathname === '/debate' || pathname.startsWith('/matches/');

  return (
    <header className={`site-header${compact ? ' site-header--compact' : ''}`}>
      <div className="site-header__inner">
        <Link className="site-header__brand" href="/" aria-label={t('returnHome')} prefetch={false}>
          <JixiaLogo compact={compact} />
        </Link>
        <nav
          className="site-header__nav"
          aria-label={t('main')}
          style={{ '--site-nav-count': navigation.length } as CSSProperties}
        >
          {navigation.map((item) => {
            const current = isCurrent(pathname, item.href);
            return (
              <Link
                key={item.href}
                className={`site-header__link${current ? ' is-current' : ''}`}
                href={item.href}
                prefetch={false}
                aria-current={current ? 'page' : undefined}
              >
                {t(item.label)}
              </Link>
            );
          })}
        </nav>
        <div className="site-header__account"><LocaleSwitcher />{authNavigation ?? <AuthNavigation />}</div>
      </div>
    </header>
  );
}
