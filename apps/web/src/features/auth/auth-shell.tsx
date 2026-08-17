import Link from 'next/link';
import type { ReactNode } from 'react';

import { JixiaLogo } from '@/components/brand/jixia-logo';

export function AuthShell({
  eyebrow,
  title,
  description,
  children,
  footer,
}: Readonly<{
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
}>) {
  return (
    <main className="jx-page-grid relative grid min-h-[calc(100dvh-var(--jx-header-height)-1px)] place-items-center overflow-hidden px-6 py-4">
      <section className="relative z-10 w-full max-w-md rounded-[2rem] border border-blue-100/90 bg-white/90 p-7 shadow-[0_30px_90px_rgba(36,72,122,0.16)] backdrop-blur-xl sm:p-8">
        <Link
          className="inline-flex rounded-xl focus-visible:ring-2 focus-visible:ring-blue-600"
          href="/"
          prefetch={false}
        >
          <JixiaLogo />
        </Link>
        <p className="jx-kicker mt-7">{eyebrow}</p>
        <h1 className="mt-3 text-3xl font-black tracking-[-0.045em] text-slate-950">{title}</h1>
        <p className="mt-3 text-sm leading-6 text-slate-600">{description}</p>
        <div className="mt-6">{children}</div>
        {footer ? <div className="mt-5 border-t border-slate-100 pt-5">{footer}</div> : null}
      </section>
    </main>
  );
}
