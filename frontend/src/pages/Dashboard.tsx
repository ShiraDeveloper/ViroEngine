import { ChartNoAxesColumn } from 'lucide-react';

import { dashboard } from '@/copy/he';
import { EmptyState } from '@/design/EmptyState';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

/** Stub: KPI cards, ablation table and Stress Mode arrive with T25/T26. Shows no numbers. */
export function Dashboard() {
  useDocumentTitle(dashboard.title);
  return (
    <>
      <h1>{dashboard.title}</h1>
      <div className="mt-5">
        <EmptyState
          illustration={<ChartNoAxesColumn className="size-8" />}
          title={dashboard.noData}
        />
      </div>
    </>
  );
}
