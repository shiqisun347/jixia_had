'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { type ComponentType, type ReactNode } from 'react';
import {
  AudioLines,
  BrainCircuit,
  ChevronRight,
  Database,
  FileText,
  Gavel,
  Home,
  LayoutDashboard,
  ListChecks,
  MessagesSquare,
  ScrollText,
  Settings,
  ShieldAlert,
  SquareActivity,
  Users,
} from 'lucide-react';

import type { ApiUser } from '@/lib/auth-api';
import { JixiaLogo } from '@/components/brand/jixia-logo';
import { LocaleSwitcher } from '@/components/layout/locale-switcher';
import { useAppTranslations } from '@/i18n';
import type { zhCN } from '@/i18n/messages';
import { cn } from '@/lib/cn';

type AdminMessageKey = keyof typeof zhCN.Admin;
type NavItem = { href: string; label: AdminMessageKey; icon: ComponentType<{ className?: string }> };
type NavGroup = { label: AdminMessageKey; items: NavItem[] };

const navGroups: NavGroup[] = [
  { label: 'overview', items: [{ href: '/admin', label: 'overview', icon: LayoutDashboard }] },
  {
    label: 'resources',
    items: [
      { href: '/admin/users', label: 'users', icon: Users },
      { href: '/admin/models', label: 'models', icon: BrainCircuit },
      { href: '/admin/voices', label: 'voices', icon: AudioLines },
      { href: '/admin/topics', label: 'topics', icon: Database },
    ],
  },
  {
    label: 'rules',
    items: [
      { href: '/admin/rules', label: 'ruleWorkspace', icon: ListChecks },
      { href: '/admin/agents', label: 'agents', icon: BrainCircuit },
    ],
  },
  {
    label: 'matches',
    items: [
      { href: '/admin/matches', label: 'matchData', icon: MessagesSquare },
      { href: '/admin/judge-results', label: 'judgeResults', icon: Gavel },
      { href: '/admin/surveys', label: 'surveys', icon: FileText },
    ],
  },
  {
    label: 'diagnostics',
    items: [
      { href: '/admin/logs', label: 'apiLogs', icon: SquareActivity },
      { href: '/admin/incidents', label: 'incidents', icon: ShieldAlert },
      { href: '/admin/tasks', label: 'tasks', icon: FileText },
    ],
  },
  {
    label: 'system',
    items: [
      { href: '/admin/audit', label: 'audit', icon: ScrollText },
      { href: '/admin/settings', label: 'settings', icon: Settings },
    ],
  },
];

function currentNavLabel(pathname: string, t: (key: string) => string): string {
  return (
    navGroups
      .flatMap((group) => group.items)
      .find((item) =>
        item.href === '/admin' ? pathname === '/admin' : pathname.startsWith(item.href),
      )?.label ?? t('title')
  );
}

function AdminNavigation({ pathname }: { pathname: string }) {
  const t = useAppTranslations('Admin');
  return (
    <nav className="flex-1 overflow-y-auto px-3 pb-5" aria-label="后台导航">
      {navGroups.map((group) => (
        <div className="mt-5" key={group.label}>
          <p className="px-3 text-[0.62rem] font-black tracking-[0.16em] text-slate-600">
            {t(group.label)}
          </p>
          <div className="mt-2 space-y-1">
            {group.items.map((item) => {
              const active =
                item.href === '/admin' ? pathname === '/admin' : pathname.startsWith(item.href);
              const Icon = item.icon;
              return (
                <Link
                  className={cn(
                    'group relative flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-bold transition focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100',
                    active
                      ? 'bg-blue-50 text-blue-700'
                      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-950',
                  )}
                  href={item.href}
                  key={item.href}
                  aria-current={active ? 'page' : undefined}
                >
                  {active ? (
                    <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-blue-600" />
                  ) : null}
                  <Icon className="size-4" aria-hidden="true" />
                  <span className="flex-1">{t(item.label)}</span>
                  {active ? (
                    <ChevronRight className="size-3.5 text-blue-500" aria-hidden="true" />
                  ) : null}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

export function AdminShell({ children, user }: { children: ReactNode; user: ApiUser }) {
  const pathname = usePathname();
  const t = useAppTranslations('Admin');
  const currentLabel = currentNavLabel(pathname, t);

  return (
    <div className="admin-shell">
      <aside className="fixed inset-y-0 left-0 z-30 flex w-64 flex-col border-r border-slate-200 bg-white">
        <Link className="flex h-20 items-center border-b border-slate-100 px-4" href="/admin">
          <JixiaLogo className="admin-brand" compact />
        </Link>
        <AdminNavigation pathname={pathname} />
        <div className="m-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-green-500 ring-4 ring-green-100" />
            <p className="truncate text-sm font-black">{user.real_name}</p>
          </div>
            <p className="mt-1.5 pl-4 text-[0.68rem] text-slate-500">{t('soleAdmin')} · @{user.username}</p>
        </div>
      </aside>

      <div className="pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200/80 bg-white/92 px-4 backdrop-blur-xl sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div>
              <p className="text-[0.65rem] font-bold tracking-[0.08em] text-slate-600">{t('title')}</p>
              <p className="text-sm font-black">{currentLabel}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
          <LocaleSwitcher />
          <Link
            className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-600 shadow-sm transition hover:border-blue-300 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100"
            href="/"
          >
            <Home className="size-4" aria-hidden="true" />
            {t('returnToApp')}
          </Link>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1560px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
