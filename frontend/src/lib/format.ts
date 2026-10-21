import type { FieldKey, FieldResult } from '@/api/types';

/** Fields whose values are digits, IDs or dates and must be isolated with ltr(). */
const ISOLATED_FIELDS: ReadonlySet<FieldKey> = new Set<FieldKey>([
  'national_id',
  'central_number',
  'receipt_date',
  'commitment_number',
]);

export function isIsolatedField(field: FieldKey): boolean {
  return ISOLATED_FIELDS.has(field);
}

/** Text to show for a field: the display form when present, otherwise the raw value, else null. */
export function fieldText(result: FieldResult | undefined): string | null {
  if (!result) return null;
  return result.display_value ?? result.value;
}

/** ISO date (YYYY-MM-DD) to DD/MM/YYYY. Returns the input unchanged when it is not ISO. */
export function formatDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return iso;
  return `${match[3]}/${match[2]}/${match[1]}`;
}

/** Ratio 0..1 to a whole-number percentage string, e.g. 0.68 -> "68%". */
export function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

/** Milliseconds to "820ms" or "1.4s". */
export function formatDuration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** Bytes to "512KB" / "3.2MB". */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

/** Whole minutes from `now` until `expiresAt` (ISO). Never negative. */
export function minutesUntil(expiresAt: string, now: Date): number {
  const diff = new Date(expiresAt).getTime() - now.getTime();
  return Math.max(0, Math.ceil(diff / 60_000));
}
