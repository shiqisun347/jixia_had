import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/cn';

const buttonVariants = cva(
  'inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold tracking-[-0.01em] transition-[background-color,border-color,box-shadow,transform,color] duration-200 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-200 active:translate-y-px active:shadow-none disabled:pointer-events-none disabled:cursor-not-allowed disabled:translate-y-0 disabled:border-slate-200 disabled:bg-slate-100 disabled:!text-slate-400 disabled:shadow-none disabled:opacity-100',
  {
    variants: {
      variant: {
        primary:
          'border border-[#b7ef00] bg-[#b7ef00] !text-[#253600] shadow-[0_8px_24px_rgba(164,220,0,0.28)] hover:-translate-y-0.5 hover:bg-[#c8ff24] hover:shadow-[0_12px_28px_rgba(164,220,0,0.34)]',
        secondary:
          'border border-[#d4e2f0] bg-white/80 text-[#1e2a3a] shadow-[0_7px_20px_rgba(60,100,145,0.08)] hover:-translate-y-0.5 hover:border-[#a9c6e7] hover:bg-white',
        ghost:
          'border border-transparent bg-transparent text-[#637087] hover:bg-[#edf4fb] hover:text-[#172033]',
        danger:
          'border border-[#ffd1d5] bg-[#fff5f5] text-[#c93543] hover:border-[#ffabb3] hover:bg-[#ffebed]',
      },
      size: {
        sm: 'min-h-9 rounded-lg px-3 text-xs',
        md: 'min-h-11 px-4',
        lg: 'min-h-13 rounded-2xl px-6 text-[0.95rem]',
        icon: 'h-10 min-h-10 w-10 rounded-xl p-0',
      },
    },
    defaultVariants: {
      variant: 'secondary',
      size: 'md',
    },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & { children?: ReactNode };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = 'button', ...props }, ref) => (
    <button
      className={cn(buttonVariants({ variant, size }), className)}
      ref={ref}
      type={type}
      {...props}
    />
  ),
);
Button.displayName = 'Button';

export { buttonVariants };
