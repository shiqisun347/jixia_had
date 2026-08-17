'use client';

import { AlertTriangle, Info, LoaderCircle } from 'lucide-react';
import { type ReactNode, useEffect, useRef } from 'react';
import { AlertDialog } from 'radix-ui';

import { Button } from './button';

type ConfirmDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  loading?: boolean;
  tone?: 'danger' | 'primary';
  icon?: ReactNode;
};

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  onConfirm,
  loading = false,
  tone = 'danger',
  icon,
}: ConfirmDialogProps) {
  const Icon = tone === 'danger' ? AlertTriangle : Info;
  const synchronousClickGate = useRef(false);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

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

  const confirmOnce = () => {
    if (synchronousClickGate.current || loading) return;
    synchronousClickGate.current = true;
    try {
      onConfirm();
    } finally {
      queueMicrotask(() => {
        synchronousClickGate.current = false;
      });
    }
  };
  return (
    <AlertDialog.Root
      open={open}
      onOpenChange={(nextOpen) => {
        if (!loading) onOpenChange(nextOpen);
      }}
    >
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-[110] bg-slate-950/40 backdrop-blur-sm data-[state=closed]:animate-out data-[state=open]:animate-in" />
        <AlertDialog.Content
          className="fixed left-1/2 top-1/2 z-[111] w-[min(calc(100vw-2rem),420px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-slate-200 bg-white p-6 shadow-[0_24px_80px_rgba(15,23,42,0.22)] outline-none"
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
          <div
            className={`grid size-10 place-items-center rounded-xl ${tone === 'danger' ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'}`}
          >
            {icon ?? <Icon className="size-5" aria-hidden="true" />}
          </div>
          <AlertDialog.Title className="mt-4 text-lg font-black text-slate-950">
            {title}
          </AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-sm leading-6 text-slate-600">
            {description}
          </AlertDialog.Description>
          <div className="mt-6 flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <Button disabled={loading} variant="secondary">
                {cancelLabel}
              </Button>
            </AlertDialog.Cancel>
            <Button
              aria-busy={loading}
              disabled={loading}
              onClick={confirmOnce}
              variant={tone === 'danger' ? 'danger' : 'primary'}
            >
              {loading ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
              {loading ? '正在处理' : confirmLabel}
            </Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
