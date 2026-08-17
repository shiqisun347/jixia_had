'use client';

import { CircleAlert, CircleCheck, Info, X } from 'lucide-react';
import { usePathname } from 'next/navigation';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import type { ReactNode } from 'react';

type ToastTone = 'success' | 'error' | 'info';

type ToastInput = {
  message: string;
  tone?: ToastTone;
  duration?: number;
};

type ToastItem = ToastInput & { id: number };

type ToastContextValue = {
  showToast: (input: ToastInput | string) => void;
  dismissToast: (id: number) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);
const DEFAULT_DURATION: Record<ToastTone, number> = {
  success: 4_000,
  info: 4_800,
  error: 6_000,
};
const MAX_VISIBLE_TOASTS = 3;

export function isRoomToastPath(pathname: string) {
  return pathname.startsWith('/rooms/') && pathname !== '/rooms/create';
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside ToastProvider');
  return context;
}

/** Useful for shared components that can also render in isolated tests/stories. */
export function useOptionalToast(): ToastContextValue | null {
  return useContext(ToastContext);
}

export function ToastProvider({
  children,
  pathnameOverride,
}: Readonly<{ children: ReactNode; pathnameOverride?: string }>) {
  const currentPathname = usePathname() || '/';
  const pathname = pathnameOverride ?? currentPathname;
  const [items, setItems] = useState<ToastItem[]>([]);
  const [modalToastHost, setModalToastHost] = useState<HTMLElement | null>(null);
  const nextId = useRef(0);
  const timers = useRef(new Map<number, number>());
  const activeMessages = useRef(new Set<string>());
  const lastMessage = useRef<{ value: string; at: number } | null>(null);

  const dismissToast = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.current.delete(id);
    setItems((current) => {
      const item = current.find((candidate) => candidate.id === id);
      if (item) activeMessages.current.delete(item.message);
      return current.filter((candidate) => candidate.id !== id);
    });
  }, []);

  const showToast = useCallback(
    (input: ToastInput | string) => {
      const normalized = typeof input === 'string' ? { message: input } : input;
      const message = normalized.message.trim();
      if (!message) return;
      const now = Date.now();
      if (lastMessage.current?.value === message && now - lastMessage.current.at < 1_200) {
        return;
      }
      if (activeMessages.current.has(message)) return;
      lastMessage.current = { value: message, at: now };
      const tone = normalized.tone ?? 'info';
      const id = ++nextId.current;
      activeMessages.current.add(message);
      const duration = normalized.duration ?? DEFAULT_DURATION[tone];
      setItems((current) => {
        const dropCount = Math.max(0, current.length - (MAX_VISIBLE_TOASTS - 1));
        current.slice(0, dropCount).forEach((item) => {
          const staleTimer = timers.current.get(item.id);
          if (staleTimer !== undefined) window.clearTimeout(staleTimer);
          timers.current.delete(item.id);
          activeMessages.current.delete(item.message);
        });
        return [...current.slice(dropCount), { ...normalized, message, tone, id }];
      });
      const timer = window.setTimeout(() => dismissToast(id), duration);
      timers.current.set(id, timer);
    },
    [dismissToast],
  );

  useEffect(
    () => () => {
      timers.current.forEach((timer) => window.clearTimeout(timer));
      timers.current.clear();
      activeMessages.current.clear();
    },
    [],
  );

  useEffect(() => {
    const syncHost = () => {
      setModalToastHost(document.querySelector<HTMLElement>('[data-toast-host="modal"]'));
    };
    syncHost();
    const observer = new MutationObserver(syncHost);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  const value = useMemo(() => ({ showToast, dismissToast }), [dismissToast, showToast]);
  const inModal = modalToastHost !== null;
  const inWaitingRoom = isRoomToastPath(pathname);

  const viewport = (
    <div
      aria-label="操作提示"
      className={
        inModal
          ? `pointer-events-none sticky top-0 z-[200] flex flex-col items-stretch gap-3 ${items.length ? 'mb-3' : ''}`
          : `toast-page-viewport${inWaitingRoom ? ' toast-page-viewport--room' : ''} pointer-events-none fixed inset-x-4 top-[calc(var(--jx-header-height)+1rem)] z-[200] flex flex-col items-end gap-3 sm:left-auto sm:right-6 sm:max-w-sm`
      }
      data-toast-placement={inModal ? 'modal' : 'page'}
      role="region"
    >
      {items.map((item) => {
        const Icon =
          item.tone === 'success' ? CircleCheck : item.tone === 'error' ? CircleAlert : Info;
        return (
          <div
            aria-atomic="true"
            aria-live={item.tone === 'error' ? 'assertive' : 'polite'}
            className={`jx-toast pointer-events-none flex w-full items-start gap-3 rounded-2xl border px-4 py-3.5 text-sm font-bold shadow-[0_18px_55px_rgba(15,35,62,0.2)] backdrop-blur-xl ${
              item.tone === 'success'
                ? 'border-lime-200 bg-[#f7ffdf]/95 text-[#3d5600]'
                : item.tone === 'error'
                  ? 'border-red-200 bg-red-50/95 text-red-800'
                  : 'border-blue-200 bg-blue-50/95 text-blue-800'
            }`}
            key={item.id}
            role={item.tone === 'error' ? 'alert' : 'status'}
          >
            <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            <span className="min-w-0 flex-1 leading-6">{item.message}</span>
            <button
              aria-label="关闭提示"
              className="pointer-events-auto -mr-1 rounded-lg p-1 transition hover:bg-black/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"
              onClick={() => dismissToast(item.id)}
              type="button"
            >
              <X aria-hidden="true" className="size-4" />
            </button>
          </div>
        );
      })}
    </div>
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {modalToastHost && typeof document !== 'undefined'
        ? createPortal(viewport, modalToastHost)
        : viewport}
    </ToastContext.Provider>
  );
}
