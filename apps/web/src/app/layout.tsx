import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { ToastProvider } from '@/components/ui/toast-provider';
import { AppChrome } from '@/components/layout/app-chrome';
import { AuthProvider } from '@/features/auth/auth-provider';
import { ExpiredSessionBoundary } from '@/features/auth/expired-session-boundary';
import { LocaleProvider } from '@/i18n';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: '稷下 · 人机实时辩论赛',
    template: '%s · 稷下',
  },
  description: '四名真人辩手与四名 Agent 的实时语音辩论赛。',
  openGraph: {
    title: '稷下 · 人机实时辩论赛',
    description: '四名真人辩手与四名 Agent 的实时语音辩论赛。',
  },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN" data-scroll-behavior="smooth" suppressHydrationWarning>
      <body>
        <LocaleProvider>
          <ToastProvider>
            <AuthProvider>
              <ExpiredSessionBoundary>
                <AppChrome />
                {children}
              </ExpiredSessionBoundary>
            </AuthProvider>
          </ToastProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
