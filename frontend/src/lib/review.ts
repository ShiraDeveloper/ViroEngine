import type { CommitmentRow, FieldEditRequest, FieldKey, FieldResult } from '@/api/types';
import { COLUMNS } from '@/lib/columns';
import type { CellRef } from '@/lib/keyboard';
import { isFlagged } from '@/lib/level';

/**
 * Minutes before the job TTL at which the review screen warns. Losing an in-progress review to
 * the automatic purge is the worst outcome (PLAN T23), so the warning has to leave time to act.
 */
export const EXPIRY_WARNING_MINUTES = 5;

/** How often the screen re-reads the clock for the expiry warning. Coarse: the unit is minutes. */
export const CLOCK_TICK_MS = 15_000;

/** Every cell of the rows, row by row, in reading order. */
export function cellOrder(rows: readonly CommitmentRow[]): CellRef[] {
  return rows.flatMap((row) =>
    COLUMNS.filter((column) => row.fields[column.key] !== undefined).map((column) => ({
      rowId: row.row_id,
      field: column.key,
    })),
  );
}

/** Flagged (level D) cells among `order`. */
export function flaggedOf(rows: readonly CommitmentRow[], order: readonly CellRef[]): CellRef[] {
  return order.filter((cell) => {
    const result = rows.find((row) => row.row_id === cell.rowId)?.fields[cell.field];
    return result !== undefined && isFlagged(result.level);
  });
}

export function findResult(
  rows: readonly CommitmentRow[],
  cell: CellRef | null,
): FieldResult | undefined {
  if (!cell) return undefined;
  return rows.find((row) => row.row_id === cell.rowId)?.fields[cell.field];
}

/**
 * The page a field was read from. The contract carries no per-field page id, so this is the
 * row's first page. For a two-page row that can be the wrong page: raise it with the API owner.
 */
export function pageIdFor(row: CommitmentRow | undefined): string | null {
  return row?.page_ids[0] ?? null;
}

/**
 * What the row looks like the instant an edit is sent, before the server answers. A human
 * decision closes the field at level B; the server's answer replaces this either way.
 */
export function applyOptimisticEdit(
  row: CommitmentRow,
  field: FieldKey,
  req: FieldEditRequest,
): CommitmentRow {
  const current = row.fields[field];
  if (!current) return row;
  const picked =
    req.pick_candidate_index != null ? current.candidates[req.pick_candidate_index]?.value : undefined;
  const value = req.pick_candidate_index != null ? (picked ?? null) : (req.value ?? null);
  const edited: FieldResult = {
    ...current,
    value,
    display_value: value,
    level: value === null ? 'D' : 'B',
    reason: value === null ? current.reason : null,
  };
  return { ...row, fields: { ...row.fields, [field]: edited }, status: 'edited' };
}

export function replaceRow(rows: readonly CommitmentRow[], next: CommitmentRow): CommitmentRow[] {
  return rows.map((row) => (row.row_id === next.row_id ? next : row));
}

/** True when the job expires in the future but within the warning window. */
export function shouldWarnExpiry(minutesLeft: number): boolean {
  return minutesLeft > 0 && minutesLeft <= EXPIRY_WARNING_MINUTES;
}
