import type { FieldBreakdown } from '@/api/metrics';
import { dashboard } from '@/copy/he';
import { Table, TBody, Td, Th, THead, Tr } from '@/design/Table';
import { formatRate } from '@/lib/metrics';
import { ltr } from '@/lib/rtl';

export interface FieldAccuracyTableProps {
  fields: readonly FieldBreakdown[];
}

/** Per-field accuracy, flag rate and the dominant failure reason, so the weak spot stands out. */
export function FieldAccuracyTable({ fields }: FieldAccuracyTableProps) {
  const c = dashboard.fieldsColumns;
  return (
    <Table caption={dashboard.fieldsCaption}>
      <THead>
        <Tr>
          <Th>{c.field}</Th>
          <Th>{c.n}</Th>
          <Th>{c.accuracy}</Th>
          <Th>{c.flagRate}</Th>
          <Th>{c.silent}</Th>
          <Th>{c.reason}</Th>
        </Tr>
      </THead>
      <TBody>
        {fields.map((f) => (
          <Tr key={f.field} data-field={f.field}>
            <Th scope="row" className="font-medium">
              {dashboard.fieldNames[f.field]}
            </Th>
            <Td className="px-3 py-2">{ltr(String(f.n))}</Td>
            <Td className="px-3 py-2">{ltr(formatRate(f.accuracy))}</Td>
            <Td className="px-3 py-2">{ltr(formatRate(f.flag_rate))}</Td>
            <Td className="px-3 py-2">{ltr(formatRate(f.silent_error))}</Td>
            <Td className="px-3 py-2" data-reason>
              {f.top_failure_reason === null ? dashboard.noReason : ltr(f.top_failure_reason)}
            </Td>
          </Tr>
        ))}
      </TBody>
    </Table>
  );
}
