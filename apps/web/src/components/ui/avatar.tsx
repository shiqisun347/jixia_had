import type { HTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

type AvatarProps = HTMLAttributes<HTMLDivElement> & {
  name: string;
  accent?: 'red' | 'blue' | 'lime' | 'neutral';
  size?: 'sm' | 'md' | 'lg' | 'xl';
  status?: 'online' | 'offline' | 'away';
};

export function Avatar({
  name,
  accent = 'neutral',
  size = 'md',
  status,
  className,
  ...props
}: AvatarProps) {
  const initials = name.trim().slice(0, 1) || '?';
  return (
    <div
      className={cn('jx-avatar', `jx-avatar--${accent}`, `jx-avatar--${size}`, className)}
      {...props}
    >
      <span aria-hidden="true">{initials}</span>
      {status ? (
        <i
          className={`jx-avatar__status jx-avatar__status--${status}`}
          aria-label={status === 'online' ? '在线' : '离线'}
        />
      ) : null}
    </div>
  );
}
