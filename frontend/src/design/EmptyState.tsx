import type { ReactNode } from 'react';

export interface EmptyStateProps {
  title: string;
  hint?: string;
  /** Decorative illustration or icon. */
  illustration?: ReactNode;
  action?: ReactNode;
}

/** Explains rather than apologises (ui-hebrew.md §4). */
export function EmptyState({ title, hint, illustration, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-surface-border bg-surface-raised p-6 text-center">
      {illustration ? (
        <div aria-hidden="true" className="text-ink-muted">
          {illustration}
        </div>
      ) : null}
      <p className="text-lg font-semibold text-ink-strong">{title}</p>
      {hint ? <p className="text-sm text-ink-muted">{hint}</p> : null}
      {action}
    </div>
  );
}
