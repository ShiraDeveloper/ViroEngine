import * as RadixToast from '@radix-ui/react-toast';
import { CircleAlert, CircleCheck, Info } from 'lucide-react';
import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

export type ToastTone = 'success' | 'error' | 'info';

export interface ToastMessage {
  tone: ToastTone;
  title: string;
  description?: string;
}

interface ToastEntry extends ToastMessage {
  id: number;
}

interface ToastApi {
  push: (message: ToastMessage) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const TONE_STYLE: Record<ToastTone, { classes: string; Icon: typeof Info }> = {
  success: {
    classes: 'border-state-verified-border bg-state-verified-bg text-state-verified-fg',
    Icon: CircleCheck,
  },
  error: {
    classes: 'border-state-review-border bg-state-review-bg text-state-review-fg',
    Icon: CircleAlert,
  },
  info: {
    classes: 'border-state-neutral-border bg-state-neutral-bg text-state-neutral-fg',
    Icon: Info,
  },
};

/** Radix Toast host. Toasts announce themselves politely and carry icon + text + colour. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ToastEntry[]>([]);

  const push = useCallback((message: ToastMessage) => {
    setEntries((current) => [...current, { ...message, id: Date.now() + current.length }]);
  }, []);

  const api = useMemo<ToastApi>(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      <RadixToast.Provider swipeDirection="right">
        {children}
        {entries.map(({ id, tone, title, description }) => {
          const { classes, Icon } = TONE_STYLE[tone];
          return (
            <RadixToast.Root
              key={id}
              onOpenChange={(open) => {
                if (!open) setEntries((current) => current.filter((entry) => entry.id !== id));
              }}
              className={`flex items-start gap-3 rounded-md border p-3 shadow-raised ${classes}`}
            >
              <Icon aria-hidden="true" className="mt-1 size-4 shrink-0" />
              <div>
                <RadixToast.Title className="text-sm font-semibold">{title}</RadixToast.Title>
                {description ? (
                  <RadixToast.Description className="text-sm">{description}</RadixToast.Description>
                ) : null}
              </div>
            </RadixToast.Root>
          );
        })}
        <RadixToast.Viewport className="fixed bottom-4 end-4 z-50 flex w-96 max-w-[90vw] flex-col gap-2" />
      </RadixToast.Provider>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error('useToast must be used inside <ToastProvider>');
  return api;
}
