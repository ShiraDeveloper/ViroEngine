import type { ConfidenceLevel } from '@/api/types';

export interface LevelStyle {
  /** Cell / badge surface: background, border and text colour classes (full strings for Tailwind). */
  surface: string;
  /** Solid bar colour used by the Confidence Ribbon. */
  bar: string;
  /** Table cell: a calm surface for verified values, a tinted one with an accent edge to act on. */
  cell: string;
  /** Status caption under a value: icon and text colour only, no box. */
  caption: string;
  /** Token family, for tests and documentation. */
  token: 'verified' | 'single' | 'review';
}

/** A and B share the verified family, C is single, D is review (PLAN §5.2). */
export const LEVEL_STYLE: Record<ConfidenceLevel, LevelStyle> = {
  A: {
    surface: 'bg-state-verified-bg border-state-verified-border text-state-verified-fg',
    bar: 'bg-state-verified-border',
    cell: 'bg-state-verified-bg border-transparent',
    caption: 'text-state-verified-fg',
    token: 'verified',
  },
  B: {
    surface: 'bg-state-verified-bg border-state-verified-border text-state-verified-fg',
    bar: 'bg-state-verified-border',
    cell: 'bg-state-verified-bg border-transparent',
    caption: 'text-state-verified-fg',
    token: 'verified',
  },
  C: {
    surface: 'bg-state-single-bg border-state-single-border text-state-single-fg',
    bar: 'bg-state-single-border',
    cell: 'bg-state-single-bg border-state-single-border',
    caption: 'text-state-single-fg',
    token: 'single',
  },
  D: {
    surface: 'bg-state-review-bg border-state-review-border text-state-review-fg',
    bar: 'bg-state-review-border',
    cell: 'bg-state-review-bg border-state-review-border',
    caption: 'text-state-review-fg',
    token: 'review',
  },
};

export const LEVELS: readonly ConfidenceLevel[] = ['A', 'B', 'C', 'D'];

/** Level D is the only level that asks the technician to act. */
export function isFlagged(level: ConfidenceLevel): boolean {
  return level === 'D';
}
