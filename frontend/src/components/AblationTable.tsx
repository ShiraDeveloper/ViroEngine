import type { EvalReport } from '@/api/metrics';
import { dashboard } from '@/copy/he';
import { Table, TBody, Td, Th, THead, Tr } from '@/design/Table';
import { formatRate } from '@/lib/metrics';
import { ltr } from '@/lib/rtl';

/** Configurations in the order of the table: the full system, then each layer switched off. */
export const ABLATION_ROWS = [
  'none',
  'no_second_read',
  'no_digit_reader',
  'no_learning',
  'no_logo',
] as const;
export type AblationRow = (typeof ABLATION_ROWS)[number];

export interface AblationTableProps {
  reports: readonly EvalReport[];
}

/** An unmeasured cell is a dash. Zero would claim a measurement that was never made. */
function cell(report: EvalReport | undefined, pick: (r: EvalReport) => string): string {
  return report ? pick(report) : '—';
}

export function AblationTable({ reports }: AblationTableProps) {
  const c = dashboard.ablationColumns;
  const byName = new Map(reports.map((r) => [r.ablation ?? 'none', r]));
  return (
    <Table caption={dashboard.ablationCaption}>
      <THead>
        <Tr>
          <Th>{c.config}</Th>
          <Th>{c.printed}</Th>
          <Th>{c.handwritten}</Th>
          <Th>{c.silent}</Th>
          <Th>{c.flagRate}</Th>
          <Th>{c.local}</Th>
          <Th>{c.calls}</Th>
        </Tr>
      </THead>
      <TBody>
        {ABLATION_ROWS.map((name) => {
          const r = byName.get(name);
          return (
            <Tr key={name} data-config={name}>
              <Th scope="row" className="font-medium">
                {dashboard.ablationRows[name]}
              </Th>
              <Td className="px-3 py-2">{ltr(cell(r, (x) => formatRate(x.printed_accuracy)))}</Td>
              <Td className="px-3 py-2">
                {ltr(cell(r, (x) => formatRate(x.handwritten_accuracy)))}
              </Td>
              <Td className="px-3 py-2">{ltr(cell(r, (x) => formatRate(x.silent_error)))}</Td>
              <Td className="px-3 py-2">{ltr(cell(r, (x) => formatRate(x.flag_rate)))}</Td>
              <Td className="px-3 py-2">{ltr(cell(r, (x) => formatRate(x.local_close_rate)))}</Td>
              <Td className="px-3 py-2">{ltr(cell(r, (x) => String(x.cloud_calls)))}</Td>
            </Tr>
          );
        })}
      </TBody>
    </Table>
  );
}
