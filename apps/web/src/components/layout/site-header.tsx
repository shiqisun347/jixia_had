'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

import { AuthNavigation } from '@/features/auth/auth-navigation';

import { JixiaLogo } from '../brand/jixia-logo';

const navigation = [
  { label: '首页', href: '/' },
  { label: '比赛大厅', href: '/lobby' },
  { label: '排行榜', href: '/leaderboard' },
  { label: '使用指南', href: '/guide' },
] as const;

function isCurrent(pathname: string, href: string) {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader({ authNavigation }: Readonly<{ authNavigation?: ReactNode }>) {
  const pathname = usePathname() || '/';
  if (pathname.startsWith('/admin')) return null;
  const compact = pathname === '/debate' || pathname.startsWith('/matches/');

  return (
    <header className={`site-header${compact ? ' site-header--compact' : ''}`}>
      <div className="site-header__inner">
        <Link className="site-header__brand" href="/" aria-label="返回首页" prefetch={false}>
          <JixiaLogo compact={compact} />
        </Link>
        <nav className="site-header__nav" aria-label="主导航">
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
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="site-header__account">{authNavigation ?? <AuthNavigation />}</div>
      </div>
    </header>
  );
}
