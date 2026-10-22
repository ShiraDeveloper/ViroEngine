import type { FieldKey, FieldResult } from '@/api/types';
import { Badge } from '@/design/Badge';
import { Td } from '@/design/Table';
import { common, review } from '@/copy/he';
import { fieldText, isIsolatedField } from '@/lib/format';
import { LEVEL_STYLE } from '@/lib/level';
import { ltr } from '@/lib/rtl';

export interface FieldCellProps {
  rowId: string;
  field: FieldKey;
  result: FieldResult | undefined;
  selected: boolean;
  onSelect?: (rowId: string, field: FieldKey) => void;
}

/**
 * One extracted field. State is always icon + text + colour: the badge carries the first two,
 * the tinted cell carries the third. An empty value renders a dash and the reason, never a guess.
 */
export function FieldCell({ rowId, field, result, selected, onSelect }: FieldCellProps) {
  if (!result) {
    return (
      <Td className="px-3 py-2 text-ink-muted">
        <span aria-hidden="true">{common.notAvailable}</span>
        <span className="sr-only">{review.emptyFieldNote}</span>
      </Td>
    );
  }

  const text = fieldText(result);
  const reasonId = `reason-${rowId}-${field}`;
  const reasonText = result.reason ?? (text === null ? review.emptyFieldNote : null);

  return (
    <Td
      data-level={result.level}
      data-field={field}
      data-row-id={rowId}
      aria-describedby={reasonText ? reasonId : undefined}
      className={`border-s-2 ${LEVEL_STYLE[result.level].surface} ${selected ? 'ring-2 ring-inset ring-primary-500' : ''}`}
    >
      <button
        type="button"
        data-cell-button=""
        aria-pressed={selected}
        onClick={() => onSelect?.(rowId, field)}
        className="flex w-full flex-col items-start gap-1 px-3 py-2 text-start"
      >
        <span className="text-sm text-ink-strong">
          {text === null ? (
            <span aria-hidden="true">{common.notAvailable}</span>
          ) : isIsolatedField(field) ? (
            ltr(text)
          ) : (
            text
          )}
        </span>
        <Badge level={result.level} />
        {reasonText ? (
          <span id={reasonId} className="text-xs text-ink-base">
            {reasonText}
          </span>
        ) : null}
      </button>
    </Td>
  );
}
