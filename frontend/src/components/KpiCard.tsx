import { CircleAlert, CircleCheck, Minus, TriangleAlert } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { dashboard } from '@/copy/he';
import type { KpiTone } from '@/lib/metrics';
import { ltr } from '@/lib/rtl';

export interface KpiCardProps {
  label: string;
  value: string;
  tone: KpiTone;
  hint?: string;
  /** Split and date of the measurement. A card without provenance is not shown. */
  measuredAt: string;
  /** The first card is larger: the order of the cards is the argument. */
  prominent?: boolean;
}

const TONE: Record<KpiTone, { classes: string; Icon: LucideIcon }> = {
  good: {
    classes: 'border-state-verified-border bg-state-verified-bg text-state-verified-fg',
    Icon: CircleCheck,
  },
  warn: {
    classes: 'border-state-single-border bg-state-single-bg text-state-single-fg',
    Icon: TriangleAlert,
  },
  bad: {
    classes: 'border-state-review-border bg-state-review-bg text-state-review-fg',
    Icon: CircleAlert,
  },
  neutral: {
    classes: 'border-state-neutral-border bg-state-neutral-bg text-state-neutral-fg',
    Icon: Minus,
  },
};

/** One measured number with its tone (icon + text + colour) and mandatory provenance. */
export function KpiCard({ label, value, tone, hint, measuredAt, prominent = false }: KpiCardProps) {
  if (measuredAt.trim() === '') return null;
  const { classes, Icon } = TONE[tone];
  return (
    <section
      data-kpi
      data-tone={tone}
      aria-label={label}
      className={`flex flex-col gap-2 rounded-lg border p-4 shadow-card ${classes} ${
        prominent ? 'md:col-span-2' : ''
      }`}
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Icon aria-hidden="true" className="size-4" />
        {label}
        <span className="ms-auto text-xs font-medium">{dashboard.tone[tone]}</span>
      </h3>
      <p className={prominent ? 'text-3xl font-bold' : 'text-2xl font-bold'}>{ltr(value)}</p>
      {hint ? <p className="text-sm">{hint}</p> : null}
      <p className="text-xs">{ltr(measuredAt)}</p>
    </section>
  );
}
