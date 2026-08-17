import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { isRoomToastPath, ToastProvider, useToast } from './toast-provider';

function Trigger() {
  const { showToast } = useToast();
  return (
    <button
      onClick={() =>
        showToast({ message: '请先完成有效的设备检测', tone: 'error', duration: 1000 })
      }
    >
      触发提示
    </button>
  );
}

function PersistentTrigger() {
  const { showToast } = useToast();
  return (
    <button onClick={() => showToast({ message: '重复提示', tone: 'error', duration: 5_000 })}>
      触发重复提示
    </button>
  );
}

function QueueTrigger() {
  const { showToast } = useToast();
  return (
    <div>
      {[1, 2, 3, 4].map((index) => (
        <button
          key={index}
          onClick={() => showToast({ message: `队列提示 ${index}`, tone: 'info', duration: 5_000 })}
        >
          触发队列提示 {index}
        </button>
      ))}
    </div>
  );
}

describe('ToastProvider', () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('shows an actionable floating error and removes it automatically', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '触发提示' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请先完成有效的设备检测');
    expect(screen.getByRole('button', { name: '关闭提示' })).toBeVisible();

    act(() => vi.advanceTimersByTime(1_001));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('marks page toasts separately from modal toasts for layout positioning', () => {
    render(
      <ToastProvider>
        <div data-toast-host="modal" />
        <Trigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '触发提示' }));
    expect(document.querySelector('[data-toast-placement="modal"]')).toBeInTheDocument();
    expect(document.querySelector('[data-toast-placement="page"]')).not.toBeInTheDocument();
  });

  it('places ordinary page toasts below the global header', () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '触发提示' }));
    const viewport = document.querySelector('[data-toast-placement="page"]');
    expect(viewport).toHaveClass('top-[calc(var(--jx-header-height)+1rem)]');
    expect(viewport).not.toHaveClass('top-4');
  });

  it('reserves the room toolbar safe area only on room routes', () => {
    expect(isRoomToastPath('/rooms/room-1')).toBe(true);
    expect(isRoomToastPath('/rooms/create')).toBe(false);
    expect(isRoomToastPath('/lobby')).toBe(false);
    expect(isRoomToastPath('/matches/match-1')).toBe(false);
  });

  it('allows isolated stories to reproduce room-route toast placement', () => {
    render(
      <ToastProvider pathnameOverride="/rooms/story">
        <Trigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '触发提示' }));
    expect(document.querySelector('[data-toast-placement="page"]')).toHaveClass(
      'toast-page-viewport--room',
    );
  });

  it('lets page clicks pass through the toast while keeping close actionable', () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '触发提示' }));
    const toast = screen.getByRole('alert');
    expect(toast).toHaveClass('pointer-events-none');
    expect(screen.getByRole('button', { name: '关闭提示' })).toHaveClass('pointer-events-auto');
  });

  it('does not stack the same active message after the short debounce window', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <PersistentTrigger />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '触发重复提示' }));
    act(() => vi.advanceTimersByTime(1_300));
    fireEvent.click(screen.getByRole('button', { name: '触发重复提示' }));
    expect(screen.getAllByRole('alert')).toHaveLength(1);
  });

  it('keeps at most three messages and allows an evicted message to return', () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <QueueTrigger />
      </ToastProvider>,
    );

    for (const index of [1, 2, 3, 4]) {
      fireEvent.click(screen.getByRole('button', { name: `触发队列提示 ${index}` }));
      act(() => vi.advanceTimersByTime(1_301));
    }
    expect(screen.getAllByRole('status')).toHaveLength(3);
    expect(screen.queryByText('队列提示 1')).not.toBeInTheDocument();
    expect(screen.getByText('队列提示 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '触发队列提示 1' }));
    expect(screen.getAllByRole('status')).toHaveLength(3);
    expect(screen.getByText('队列提示 1')).toBeInTheDocument();
  });
});
