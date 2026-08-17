import type { HTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: 'neutral' | 'red' | 'blue' | 'lime' | 'green' | 'amber';
};

export function Badge({ className, tone = 'neutral', ...props }: BadgeProps) {
  return <span className={cn('jx-badge', `jx-badge--${tone}`, className)} {...props} />;
}
