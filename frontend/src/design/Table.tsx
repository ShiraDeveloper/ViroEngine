import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from 'react';

export interface TableProps {
  /** Accessible name of the table, announced by screen readers. */
  caption: string;
  children: ReactNode;
}

/** Real <table> semantics. Columns follow the document direction (right to left in Hebrew). */
export function Table({ caption, children }: TableProps) {
  return (
    <div className="overflow-x-auto rounded-lg border border-surface-border bg-surface-raised shadow-card">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="bg-surface-sunken">{children}</thead>;
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody>{children}</tbody>;
}

export function Tr({ className, ...rest }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={className ?? 'border-t border-surface-border'} {...rest} />;
}

export function Th({ scope = 'col', className, ...rest }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      scope={scope}
      className={`px-3 py-2 text-start text-sm font-semibold text-ink-strong ${className ?? ''}`}
      {...rest}
    />
  );
}

export function Td({ className, ...rest }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={`align-top ${className ?? ''}`} {...rest} />;
}
