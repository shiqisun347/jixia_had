import { ArrowRight, Compass, Home, Map } from 'lucide-react';
import Link from 'next/link';

const recoveryLinks = [
  { href: '/', label: '返回首页', icon: Home, primary: true },
  { href: '/lobby', label: '进入比赛大厅', icon: Compass, primary: false },
  { href: '/guide', label: '查看使用指南', icon: Map, primary: false },
] as const;

export default function NotFound() {
  return (
    <main className="jx-page-grid grid min-h-[calc(100dvh-var(--jx-header-height))] place-items-center bg-[#f7faff] px-6 py-6 text-slate-950">
      <section className="w-full max-w-2xl rounded-[2rem] border border-blue-100 bg-white p-8 text-center shadow-[0_26px_80px_rgba(36,72,122,0.13)] sm:p-12">
        <p className="jx-kicker">404 · LOST LINK</p>
        <h1 className="mt-4 text-4xl font-black sm:text-5xl">页面不存在</h1>
        <p className="mx-auto mt-4 max-w-lg text-sm leading-7 text-slate-600 sm:text-base">
          这个链接可能已经失效，或者地址输入有误。你可以从下面的入口继续。
        </p>
        <div className="mt-8 grid gap-3 sm:grid-cols-3">
          {recoveryLinks.map(({ href, label, icon: Icon, primary }) => (
            <Link
              className={`inline-flex min-h-12 items-center justify-center gap-2 rounded-xl border px-4 text-sm font-black transition focus-visible:ring-2 focus-visible:ring-blue-600 ${
                primary
                  ? 'border-slate-950 bg-slate-950 text-white hover:bg-blue-700'
                  : 'border-blue-200 bg-white text-slate-800 hover:border-blue-400 hover:bg-blue-50'
              }`}
              href={href}
              key={href}
            >
              <Icon className="size-4" aria-hidden="true" />
              {label}
              {primary ? <ArrowRight className="size-4" aria-hidden="true" /> : null}
            </Link>
          ))}
        </div>
      </section>
    </main>
  );
}
