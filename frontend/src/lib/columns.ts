import type { FieldKey } from '@/api/types';
import { review } from '@/copy/he';

export interface Column {
  key: FieldKey;
  label: string;
}

/** The seven fields in guideline order (PLAN §9.5). Rendered right to left in the table. */
export const COLUMNS: readonly Column[] = [
  { key: 'sender', label: review.columns.sender },
  { key: 'first_name', label: review.columns.firstName },
  { key: 'last_name', label: review.columns.lastName },
  { key: 'national_id', label: review.columns.nationalId },
  { key: 'central_number', label: review.columns.central },
  { key: 'receipt_date', label: review.columns.receiptDate },
  { key: 'commitment_number', label: review.columns.commitment },
];

export function columnLabel(field: FieldKey): string {
  return COLUMNS.find((column) => column.key === field)?.label ?? field;
}
