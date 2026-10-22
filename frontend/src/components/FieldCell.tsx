import { CircleCheck, CircleDashed, ScanBarcode, TriangleAlert } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { ConfidenceLevel, FieldKey, FieldResult } from '@/api/types';
import { Td } from '@/design/Table';
import { common, review } from '@/copy/he';
import { fieldText, isIsolatedField } from '@/lib/format';
import { LEVEL_STYLE } from '@/lib/level';
import { ltr } from '@/lib/rtl';

/** Status icons are never mirrored in RTL. A: the barcode itself confirmed the value. */
const STATUS_ICON: Record<ConfidenceLevel, LucideIcon> = {
  A: ScanBarcode,
  B: CircleCheck,
  C: CircleDashed,
  D: TriangleAlert,
};

export interface FieldCellProps {
  rowId: string;
  field: FieldKey;
  result: FieldResult | undefined;
  selected: boolean;
  onSelect?: (rowId: string, field: FieldKey) => void;
}

/**
 * One extracted field: the value first, a quiet status caption under it (icon + text + colour,
 * never colour alone). Verified cells stay calm; only what needs the technician is tinted and
 * carries an accent edge and the reason. An empty value renders a dash, never a guess.
 */
export function FieldCell({ rowId, field, result, selected, onSelect }: FieldCellProps) {
  if (!result) {
    return (
      <Td className="px-4 py-3 text-ink-muted">
        <span aria-hidden="true">{common.notAvailable}</span>
        <span className="sr-only">{review.emptyFieldNote}</span>
      </Td>
    );
  }

  const text = fieldText(result);
  const reasonId = `reason-${rowId}-${field}`;
  const reasonText = result.reason ?? (text === null ? review.emptyFieldNote : null);
  const style = LEVEL_STYLE[result.level];
  const Icon = STATUS_ICON[result.level];

  return (
    <Td
      data-level={result.level}
      data-field={field}
      data-row-id={rowId}
      aria-describedby={reasonText ? reasonId : undefined}
      className={`border-s-[3px] transition-colors duration-fast ${style.cell} ${
        selected ? 'outline outline-2 -outline-offset-2 outline-primary-500' : ''
      }`}
    >
      <button
        type="button"
        data-cell-button=""
        aria-pressed={selected}
        onClick={() => onSelect?.(rowId, field)}
        className="flex min-h-16 w-full flex-col items-start justify-center gap-1 px-4 py-3 text-start hover:bg-primary-100/40"
      >
        <span
          className={`text-[15px] font-semibold leading-snug ${
            text === null ? 'text-ink-muted' : 'text-ink-strong'
          } ${isIsolatedField(field) ? 'font-mono tracking-wide' : ''}`}
        >
          {text === null ? (
            <span aria-hidden="true">{common.notAvailable}</span>
          ) : isIsolatedField(field) ? (
            ltr(text)
          ) : (
            text
          )}
        </span>
        <span
          title={review.levels[result.level]}
          className={`inline-flex items-center gap-1 whitespace-nowrap text-[11px] font-medium ${style.caption}`}
        >
          <Icon aria-hidden="true" className="size-3.5 shrink-0" />
          <span className={result.level === 'D' ? undefined : 'sr-only'}>
            {review.levels[result.level]}
          </span>
        </span>
        {reasonText && result.level === 'D' ? (
          <span id={reasonId} className="text-[11px] leading-snug text-ink-muted">
            {reasonText}
          </span>
        ) : reasonText ? (
          <span id={reasonId} className="sr-only">
            {reasonText}
          </span>
        ) : null}
      </button>
    </Td>
  );
}
