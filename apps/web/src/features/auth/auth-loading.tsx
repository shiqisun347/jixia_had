import { LoaderCircle } from 'lucide-react';

export function AuthLoading({ label = '正在确认登录状态' }: Readonly<{ label?: string }>) {
  return (
    <main className="jx-page-grid jx-page-viewport grid place-items-center px-6">
      <div
        className="jx-glass flex items-center gap-3 rounded-2xl px-6 py-5 text-sm font-bold text-slate-700"
        role="status"
      >
        <LoaderCircle className="size-5 animate-spin text-blue-600" aria-hidden="true" />
        {label}
      </div>
    </main>
  );
}
