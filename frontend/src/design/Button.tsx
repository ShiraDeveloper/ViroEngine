import { Loader2 } from 'lucide-react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

const BASE =
  'inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors ' +
  'duration-fast ease-token disabled:cursor-not-allowed disabled:opacity-60';

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-primary-700 text-ink-inverse hover:bg-primary-900 active:bg-primary-900',
  secondary:
    'border border-primary-500 bg-surface-raised text-primary-700 hover:bg-primary-100 active:bg-primary-100',
  ghost: 'bg-transparent text-primary-700 hover:bg-primary-100 active:bg-primary-100',
  danger: 'bg-accent-700 text-ink-inverse hover:bg-accent-500 active:bg-accent-500',
};

const SIZE: Record<ButtonSize, string> = {
  sm: 'min-h-8 px-3 text-sm',
  md: 'min-h-10 px-4 text-base',
  lg: 'min-h-12 px-5 text-lg',
};

/** Class string shared by <Button> and anchors that must look like one (e.g. downloads). */
export function buttonClasses(variant: ButtonVariant = 'primary', size: ButtonSize = 'md'): string {
  return `${BASE} ${VARIANT[variant]} ${SIZE[size]}`;
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  /** Icon on the inline-start side (the right in RTL). Decorative: hidden from assistive tech. */
  iconStart?: ReactNode;
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  iconStart,
  disabled,
  className,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={className ? `${buttonClasses(variant, size)} ${className}` : buttonClasses(variant, size)}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? (
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
      ) : iconStart ? (
        <span aria-hidden="true" className="inline-flex">
          {iconStart}
        </span>
      ) : null}
      {children}
    </button>
  );
}
