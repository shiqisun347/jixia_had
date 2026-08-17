'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState, type ComponentType, type ReactNode } from 'react';
import {
  AudioLines,
  Bot,
  BrainCircuit,
  ChevronRight,
  Database,
  FileText,
  Gavel,
  Home,
  LayoutDashboard,
  ListChecks,
  Menu,
  MessagesSquare,
  Settings,
  Users,
  X,
} from 'lucide-react';

import type { ApiUser } from '@/lib/auth-api';
import { JixiaLogo } from '@/components/brand/jixia-logo';
import { cn } from '@/lib/cn';

type NavItem = { href: string; label: string; icon: ComponentType<{ className?: string }> };
type NavGroup = { label: string; items: NavItem[] };

const navGroups: NavGroup[] = [
  { label: '总览', items: [{ href: '/admin', label: '运行总览', icon: LayoutDashboard }] },
  {
    label: '运营',
    items: [
      { href: '/admin/users', label: '用户管理', icon: Users },
      { href: '/admin/matches', label: '比赛与数据', icon: MessagesSquare },
      { href: '/admin/logs', label: '日志管理', icon: FileText },
    ],
  },
  {
    label: '智能体',
    items: [
      { href: '/admin/models', label: '模型设置', icon: BrainCircuit },
      { href: '/admin/agents', label: 'Agent 管理', icon: Bot },
      { href: '/admin/judge', label: 'AI 裁判', icon: Gavel },
    ],
  },
  {
    label: '内容与语音',
    items: [
      { href: '/admin/rules', label: '赛制规则', icon: ListChecks },
      { href: '/admin/topics', label: '辩题管理', icon: Database },
      { href: '/admin/voices', label: '语音方案', icon: AudioLines },
    ],
  },
  { label: '系统', items: [{ href: '/admin/settings', label: '系统设置', icon: Settings }] },
];

function currentNavLabel(pathname: string): string {
  return (
    navGroups
      .flatMap((group) => group.items)
      .find((item) =>
        item.href === '/admin' ? pathname === '/admin' : pathname.startsWith(item.href),
      )?.label ?? '管理后台'
  );
}

function AdminNavigation({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <nav className="flex-1 overflow-y-auto px-3 pb-5" aria-label="后台导航">
      {navGroups.map((group) => (
        <div className="mt-5" key={group.label}>
          <p className="px-3 text-[0.62rem] font-black tracking-[0.16em] text-slate-600">
            {group.label}
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
                  onClick={onNavigate}
                  aria-current={active ? 'page' : undefined}
                >
                  {active ? (
                    <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-blue-600" />
                  ) : null}
                  <Icon className="size-4" aria-hidden="true" />
                  <span className="flex-1">{item.label}</span>
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
  const [mobileOpen, setMobileOpen] = useState(false);
  const currentLabel = currentNavLabel(pathname);

  return (
    <div className="admin-shell">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white lg:flex lg:flex-col">
        <Link className="flex h-20 items-center border-b border-slate-100 px-4" href="/admin">
          <JixiaLogo compact />
        </Link>
        <AdminNavigation pathname={pathname} />
        <div className="m-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-green-500 ring-4 ring-green-100" />
            <p className="truncate text-sm font-black">{user.real_name}</p>
          </div>
          <p className="mt-1.5 pl-4 text-[0.68rem] text-slate-500">唯一管理员 · @{user.username}</p>
        </div>
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            className="absolute inset-0 bg-slate-950/30 backdrop-blur-sm"
            aria-label="关闭后台导航"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="relative flex h-full w-72 flex-col bg-white shadow-2xl">
            <div className="flex h-16 items-center justify-between border-b border-slate-100 px-5">
              <JixiaLogo compact />
              <button
                className="grid size-9 place-items-center rounded-lg bg-slate-100"
                onClick={() => setMobileOpen(false)}
              >
                <X className="size-4" aria-hidden="true" />
                <span className="sr-only">关闭导航</span>
              </button>
            </div>
            <AdminNavigation pathname={pathname} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200/80 bg-white/92 px-4 backdrop-blur-xl sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <button
              className="grid size-10 place-items-center rounded-xl border border-slate-200 bg-white lg:hidden"
              onClick={() => setMobileOpen(true)}
            >
              <Menu className="size-4" aria-hidden="true" />
              <span className="sr-only">打开后台导航</span>
            </button>
            <div>
              <p className="text-[0.65rem] font-bold tracking-[0.08em] text-slate-600">管理后台</p>
              <p className="text-sm font-black">{currentLabel}</p>
            </div>
          </div>
          <Link
            className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold text-slate-600 shadow-sm transition hover:border-blue-300 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100"
            href="/"
          >
            <Home className="size-4" aria-hidden="true" />
            返回用户端
          </Link>
        </header>
        <main className="mx-auto w-full max-w-[1560px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
