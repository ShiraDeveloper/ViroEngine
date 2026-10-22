import type { EvalReport, EvalSplit } from '@/api/metrics';
import { dashboard } from '@/copy/he';
import { formatDuration } from '@/lib/format';

export type KpiTone = 'good' | 'warn' | 'bad' | 'neutral';

/**
 * Silent-error thresholds (PLAN T26): at most 1% is good, at most 3% a warning, above that bad.
 * Structural: they define the colour of one card and gate nothing in the engine.
 */
export const SILENT_GOOD_MAX = 0.01;
export const SILENT_WARN_MAX = 0.03;

export function silentErrorTone(rate: number | null): KpiTone {
  if (rate === null) return 'neutral';
  if (rate <= SILENT_GOOD_MAX) return 'good';
  if (rate <= SILENT_WARN_MAX) return 'warn';
  return 'bad';
}

/** A missing measurement is a dash, never a zero. */
export function formatRate(rate: number | null | undefined, digits = 1): string {
  return rate == null ? '—' : `${(rate * 100).toFixed(digits)}%`;
}

export function formatLatency(ms: number | null | undefined): string {
  return ms == null ? '—' : formatDuration(ms);
}

/** `2026-10-27` to `27.10`. Anything else is returned unchanged. */
export function formatShortDate(iso: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso);
  return match ? `${match[2]}.${match[1]}` : iso;
}

/** "held-out · 27.10": the split and date every number is shown with. */
export function provenanceOf(report: EvalReport): string {
  const split: EvalSplit = report.split;
  return dashboard.provenance(dashboard.splitNames[split], formatShortDate(report.date));
}
