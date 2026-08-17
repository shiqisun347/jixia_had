'use client';

import { usePathname } from 'next/navigation';

import { SiteHeader } from './site-header';

export function AppChrome() {
  const pathname = usePathname() || '/';
  // The home prototype owns its header so isolated Storybook fixtures remain self-contained.
  if (pathname === '/') return null;
  return <SiteHeader />;
}
