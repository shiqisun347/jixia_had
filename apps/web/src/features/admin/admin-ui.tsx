import type { ReactNode } from 'react';
import { useEffect } from 'react';
import { AlertCircle, Inbox } from 'lucide-react';

import { cn } from '@/lib/cn';

import { useOptionalToast } from '@/components/ui/toast-provider';

export const adminFieldClass =
  'w-full min-h-11 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-950 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400';

export function AdminPageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="relative flex flex-wrap items-end justify-between gap-5 border-b border-slate-200 pb-6">
      <span
        className="absolute -bottom-px left-0 h-0.5 w-20 rounded-full bg-blue-600"
        aria-hidden="true"
      />
      <div className="max-w-3xl">
        <p className="text-[0.65rem] font-black tracking-[0.16em] text-blue-600">{eyebrow}</p>
        <h1 className="mt-2 text-[1.8rem] font-black tracking-[-0.035em] text-slate-950">
          {title}
        </h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-6 text-slate-600">{description}</p>
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

export function AdminPanel({
  title,
  description,
  action,
  children,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        'rounded-2xl border border-slate-200 bg-white shadow-[0_12px_38px_rgba(15,23,42,0.045)]',
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <h2 className="text-sm font-black tracking-[-0.015em] text-slate-950">{title}</h2>
          {description ? (
            <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

export function AdminFeedback({
  message,
  tone = 'info',
}: {
  message: string;
  tone?: 'info' | 'error';
}) {
  const toast = useOptionalToast();

  useEffect(() => {
    if (message && tone === 'error') toast?.showToast({ message, tone: 'error' });
  }, [message, toast, tone]);

  // Errors are transient global feedback. Keep informational empty-state/status
  // messages in the page flow where they can provide context without stacking.
  if (!message || tone === 'error') return null;
  return (
    <div
      role="status"
      className="flex items-start gap-2 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800"
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

export function AdminEmpty({ children = '暂无数据' }: { children?: ReactNode }) {
  return (
    <div className="grid min-h-36 place-items-center rounded-xl border border-dashed border-slate-200 bg-slate-50/70 px-5 text-center text-sm text-slate-500">
      <div>
        <Inbox className="mx-auto mb-2 size-5 text-slate-400" aria-hidden="true" />
        {children}
      </div>
    </div>
  );
}

export function Field({
  label,
  name,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  name: string;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <input className={adminFieldClass} name={name} {...props} />
    </label>
  );
}

export function SelectField({
  label,
  name,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  name: string;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <select className={adminFieldClass} name={name} {...props}>
        {children}
      </select>
    </label>
  );
}

export function TextArea({
  label,
  name,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; name: string }) {
  return (
    <label className="grid gap-1.5 text-xs font-bold text-slate-600">
      {label}
      <textarea className={cn(adminFieldClass, 'min-h-28 resize-y')} name={name} {...props} />
    </label>
  );
}
