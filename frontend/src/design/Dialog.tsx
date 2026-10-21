import * as RadixDialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';

import { common } from '@/copy/he';

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
}

/** Radix Dialog: focus trap, Esc to close and ARIA wiring come from the primitive. */
export function Dialog({ open, onOpenChange, title, description, children }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 bg-primary-900/40" />
        <RadixDialog.Content
          // Without a description, opt out explicitly so Radix does not warn.
          {...(description ? {} : { 'aria-describedby': undefined })}
          className="fixed inset-x-0 top-1/2 mx-auto w-[min(32rem,90vw)] -translate-y-1/2 rounded-lg bg-surface-raised p-5 shadow-raised"
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <RadixDialog.Title className="text-xl font-semibold text-ink-strong">
              {title}
            </RadixDialog.Title>
            <RadixDialog.Close
              aria-label={common.close}
              className="rounded-sm p-1 text-ink-muted hover:bg-surface-sunken"
            >
              <X aria-hidden="true" className="size-4" />
            </RadixDialog.Close>
          </div>
          {description ? (
            <RadixDialog.Description className="mb-4 text-sm text-ink-muted">
              {description}
            </RadixDialog.Description>
          ) : null}
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
