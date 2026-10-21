import { Check, Contrast, TriangleAlert } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { ConfidenceLevel } from '@/api/types';
import { review } from '@/copy/he';
import { LEVEL_STYLE } from '@/lib/level';

/** Status icons are never mirrored in RTL. */
const LEVEL_ICON: Record<ConfidenceLevel, LucideIcon> = {
  A: Check,
  B: Check,
  C: Contrast,
  D: TriangleAlert,
};

export interface BadgeProps {
  level: ConfidenceLevel;
  /** Show the Hebrew text. When false the text stays available to screen readers. */
  withText?: boolean;
}

/** Confidence badge: always icon + text + colour, never colour alone (PLAN §9.4). */
export function Badge({ level, withText = true }: BadgeProps) {
  const Icon = LEVEL_ICON[level];
  const text = review.levels[level];
  return (
    <span
      data-level={level}
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-start text-xs font-medium ${LEVEL_STYLE[level].surface}`}
    >
      <Icon aria-hidden="true" className="size-3" />
      <span className={withText ? undefined : 'sr-only'}>{text}</span>
    </span>
  );
}
