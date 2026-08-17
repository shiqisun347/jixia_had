'use client';

import { AlertTriangle, Check, ChevronDown, Info, LoaderCircle, RefreshCw, X } from 'lucide-react';
import type {
  ButtonHTMLAttributes,
  ComponentPropsWithoutRef,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from 'react';
import { forwardRef, useEffect, useRef } from 'react';
import { Dialog, DropdownMenu } from 'radix-ui';

import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useSingleFlight } from '@/hooks/use-single-flight';
import { cn } from '@/lib/cn';

type ButtonTone = 'primary' | 'secondary' | 'ghost' | 'danger';

export const adminActionItemBaseClass =
  'flex min-h-9 cursor-pointer items-center rounded-lg border border-transparent px-3 text-xs font-bold outline-none transition data-[disabled]:cursor-not-allowed data-[disabled]:border-slate-200 data-[disabled]:bg-slate-100 data-[disabled]:!text-slate-400 data-[disabled]:opacity-100 data-[highlighted]:bg-slate-100';

const buttonToneClass: Record<ButtonTone, string> = {
  primary:
    'border-blue-600 bg-blue-600 text-white shadow-[0_10px_24px_rgba(37,99,235,0.2)] hover:border-blue-700 hover:bg-blue-700',
  secondary: 'border-slate-200 bg-white text-slate-700 hover:border-blue-300 hover:text-blue-700',
  ghost: 'border-transparent bg-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-950',
  danger: 'border-red-200 bg-red-50 text-red-700 hover:border-red-300 hover:bg-red-100',
};

type AdminButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: ButtonTone;
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
};

export const AdminButton = forwardRef<HTMLButtonElement, AdminButtonProps>(function AdminButton(
  { tone = 'secondary', size = 'md', loading = false, className, children, ...props },
  ref,
) {
  return (
    <button
      {...props}
      className={cn(
        'inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border px-3.5 text-sm font-bold transition-[background-color,border-color,box-shadow,transform,color] duration-150 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100 disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400 disabled:shadow-none disabled:opacity-100',
        size === 'sm' ? 'min-h-9 rounded-lg px-3 text-xs' : null,
        size === 'lg' ? 'min-h-12 rounded-2xl px-5' : null,
        buttonToneClass[tone],
        className,
      )}
      disabled={props.disabled || loading}
      ref={ref}
    >
      {loading ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
      {children}
    </button>
  );
});

type AdminRefreshButtonProps = Omit<AdminButtonProps, 'loading' | 'onClick'> & {
  label?: string;
  onRefresh: () => Promise<unknown>;
};

export function AdminRefreshButton({
  label = '刷新',
  onRefresh,
  ...props
}: AdminRefreshButtonProps) {
  const { isPending, run } = useSingleFlight();

  return (
    <AdminButton
      {...props}
      aria-busy={isPending}
      loading={isPending}
      onClick={() => {
        void run(onRefresh).catch(() => undefined);
      }}
      type={props.type ?? 'button'}
    >
      {!isPending ? <RefreshCw className="size-4" aria-hidden="true" /> : null}
      {label}
    </AdminButton>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<
    string,
    { label: string; tone: 'green' | 'amber' | 'red' | 'blue' | 'neutral' }
  > = {
    ACTIVE: { label: '启用', tone: 'green' },
    ENABLED: { label: '启用', tone: 'green' },
    READY: { label: '就绪', tone: 'green' },
    FINISHED: { label: '已结束', tone: 'blue' },
    SUCCEEDED: { label: '成功', tone: 'green' },
    RUNNING: { label: '进行中', tone: 'blue' },
    PAUSED: { label: '已暂停', tone: 'amber' },
    PENDING: { label: '等待中', tone: 'amber' },
    GENERATING_AUDIO: { label: '生成中', tone: 'amber' },
    DISABLED: { label: '停用', tone: 'neutral' },
    TERMINATED: { label: '已终止', tone: 'red' },
    FAILED: { label: '失败', tone: 'red' },
  };
  const item = map[status] ?? { label: status, tone: 'neutral' as const };
  return (
    <span className={`admin-status admin-status--${item.tone}`}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {item.label}
    </span>
  );
}

export function AdminToolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50/80 p-2',
        className,
      )}
    >
      {children}
    </div>
  );
}

export function AdminSelect({
  label,
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className="inline-flex min-h-9 items-center gap-2 text-xs font-bold text-slate-500">
      <span className="sr-only">{label}</span>
      <select
        {...props}
        aria-label={label}
        className={cn(
          'min-h-9 rounded-lg border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700 outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100',
          className,
        )}
      />
    </label>
  );
}

export function AdminSearch({
  label = '搜索',
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label?: string }) {
  return (
    <label className="relative min-w-52 flex-1">
      <span className="sr-only">{label}</span>
      <input
        {...props}
        aria-label={label}
        className="min-h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-800 outline-none placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
        type="search"
      />
    </label>
  );
}

export function AdminPagination({
  page,
  totalPages,
  total,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4 text-xs font-semibold text-slate-600">
      <span>
        第 {page} / {totalPages} 页 · 共 {total} 条
      </span>
      <div className="flex gap-2">
        <AdminButton
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          size="sm"
          type="button"
        >
          上一页
        </AdminButton>
        <AdminButton
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          size="sm"
          type="button"
        >
          下一页
        </AdminButton>
      </div>
    </div>
  );
}

export function AdminActionMenu({
  label = '更多操作',
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger asChild>
        <button
          aria-label={label}
          className="inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700 shadow-sm transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100 data-[state=open]:border-blue-300 data-[state=open]:bg-blue-50 data-[state=open]:text-blue-800"
          type="button"
        >
          <span aria-hidden="true">操作</span>
          <ChevronDown className="size-4" aria-hidden="true" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          className="z-[120] min-w-44 rounded-xl border border-slate-200 bg-white p-1.5 shadow-[0_18px_50px_rgba(15,23,42,0.16)]"
          data-admin-action-menu=""
          sideOffset={6}
        >
          {children}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export function AdminActionItem({
  tone = 'default',
  children,
  ...props
}: ComponentPropsWithoutRef<typeof DropdownMenu.Item> & {
  tone?: 'default' | 'danger';
}) {
  return (
    <DropdownMenu.Item
      {...props}
      className={cn(
        adminActionItemBaseClass,
        tone === 'danger'
          ? 'text-red-700 data-[highlighted]:border-red-100 data-[highlighted]:bg-red-50'
          : 'text-slate-700',
        props.className,
      )}
    >
      {children}
    </DropdownMenu.Item>
  );
}

export function AdminDrawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const rememberOutsideFocus = (event: FocusEvent) => {
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        target !== document.body &&
        !contentRef.current?.contains(target) &&
        !target.closest('[data-admin-action-menu]')
      ) {
        returnFocusRef.current = target;
      }
    };
    document.addEventListener('focusin', rememberOutsideFocus);
    return () => document.removeEventListener('focusin', rememberOutsideFocus);
  }, []);

  return (
    <Dialog.Root modal open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[100] bg-slate-950/35 backdrop-blur-[2px] data-[state=closed]:animate-out data-[state=open]:animate-in" />
        <Dialog.Content
          className="fixed inset-y-0 right-0 z-[101] flex w-full max-w-xl flex-col border-l border-slate-200 bg-white shadow-[-18px_0_55px_rgba(15,23,42,0.16)] outline-none data-[state=closed]:animate-out data-[state=open]:animate-in sm:w-[min(100vw,560px)]"
          onCloseAutoFocus={(event) => {
            const target = returnFocusRef.current;
            returnFocusRef.current = null;
            if (target?.isConnected) {
              event.preventDefault();
              target.focus();
            }
          }}
          ref={contentRef}
        >
          <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-5">
            <div>
              <Dialog.Title className="text-lg font-black tracking-[-0.02em] text-slate-950">
                {title}
              </Dialog.Title>
              {description ? (
                <Dialog.Description className="mt-1 text-xs leading-5 text-slate-500">
                  {description}
                </Dialog.Description>
              ) : null}
            </div>
            <Dialog.Close asChild>
              <button
                aria-label="关闭抽屉"
                className="grid size-9 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100"
                type="button"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>
          <div className="relative min-h-0 flex-1 overflow-y-auto px-6 py-5">
            <div data-toast-host="modal" />
            {children}
          </div>
          {footer ? (
            <div className="border-t border-slate-100 bg-slate-50/80 px-6 py-4">{footer}</div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function AdminConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = '确认操作',
  onConfirm,
  loading = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel?: string;
  onConfirm: () => void;
  loading?: boolean;
}) {
  return (
    <ConfirmDialog
      confirmLabel={confirmLabel}
      description={description}
      loading={loading}
      onConfirm={onConfirm}
      onOpenChange={onOpenChange}
      open={open}
      title={title}
    />
  );
}

export function AdminNotice({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'success' | 'warning';
  children: ReactNode;
}) {
  const Icon = tone === 'success' ? Check : tone === 'warning' ? AlertTriangle : Info;
  return (
    <div className={`admin-notice admin-notice--${tone}`} role="status">
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}
