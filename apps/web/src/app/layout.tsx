import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { ToastProvider } from '@/components/ui/toast-provider';
import { AppChrome } from '@/components/layout/app-chrome';
import { AuthProvider } from '@/features/auth/auth-provider';
import { ExpiredSessionBoundary } from '@/features/auth/expired-session-boundary';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: '稷下 · 人机实时辩论实验平台',
    template: '%s · 稷下',
  },
  description: '让人类与 Agent 在声音中交锋与共创的实时辩论实验平台。',
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN" data-scroll-behavior="smooth">
      <body>
        <ToastProvider>
          <AuthProvider>
            <ExpiredSessionBoundary>
              <AppChrome />
              {children}
            </ExpiredSessionBoundary>
          </AuthProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
