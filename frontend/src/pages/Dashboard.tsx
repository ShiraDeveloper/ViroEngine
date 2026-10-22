import { useQuery } from '@tanstack/react-query';
import { ChartNoAxesColumn } from 'lucide-react';

import { api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import type { EvalReport } from '@/api/metrics';
import { AblationTable } from '@/components/AblationTable';
import { FieldAccuracyTable } from '@/components/FieldAccuracyTable';
import { KpiCard } from '@/components/KpiCard';
import { LearningChart } from '@/components/LearningChart';
import { StressModeButton } from '@/components/StressModeButton';
import type { StressClient } from '@/components/StressModeButton';
import { common, dashboard } from '@/copy/he';
import { Button } from '@/design/Button';
import { EmptyState } from '@/design/EmptyState';
import { formatRate, formatLatency, provenanceOf, silentErrorTone } from '@/lib/metrics';
import { getCurrentJobId } from '@/lib/session';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

export type DashboardClient = Pick<ApiClient, 'getEvalMetrics'> & Partial<StressClient>;

export interface DashboardProps {
  /** Injected in tests; the app uses the configured client. */
  client?: DashboardClient;
  /** Source job of Stress Mode. Defaults to the job open in this tab. */
  jobId?: string;
}

function Kpis({ report }: { report: EvalReport }) {
  const at = provenanceOf(report);
  return (
    <div
      role="group"
      aria-label={dashboard.kpiGroupLabel}
      className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-4"
    >
      <KpiCard
        prominent
        label={dashboard.kpi.silent}
        value={formatRate(report.silent_error)}
        tone={silentErrorTone(report.silent_error)}
        measuredAt={at}
      />
      <KpiCard
        label={dashboard.kpi.flagRecall}
        value={formatRate(report.flag_recall)}
        tone="neutral"
        measuredAt={at}
      />
      <KpiCard
        label={dashboard.kpi.localRate}
        value={formatRate(report.local_close_rate)}
        tone="neutral"
        measuredAt={at}
      />
      <KpiCard
        label={dashboard.kpi.accuracy}
        value={formatRate(report.accuracy)}
        tone="neutral"
        hint={dashboard.accuracyHint(
          formatRate(report.printed_accuracy),
          formatRate(report.handwritten_accuracy),
        )}
        measuredAt={at}
      />
      <KpiCard
        label={dashboard.kpi.calls}
        value={String(report.cloud_calls)}
        tone="neutral"
        hint={dashboard.callsHint(`$${report.measured_cost_usd.toFixed(2)}`)}
        measuredAt={at}
      />
      <KpiCard
        label={dashboard.kpi.time}
        value={formatLatency(report.p50_latency_ms)}
        tone="neutral"
        measuredAt={at}
      />
    </div>
  );
}

export function Dashboard({ client = api, jobId }: DashboardProps = {}) {
  useDocumentTitle(dashboard.title);
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ['eval-metrics'],
    queryFn: () => client.getEvalMetrics(),
  });

  const stressClient: StressClient | null =
    client.stress && client.getJob ? { stress: client.stress, getJob: client.getJob } : null;
  const sourceJob = jobId ?? getCurrentJobId();

  let body;
  if (isPending) {
    body = (
      <p className="mt-4 text-ink-muted" role="status">
        {common.loading}
      </p>
    );
  } else if (isError) {
    body = (
      <div className="mt-4 flex flex-col items-start gap-3" role="alert">
        <p>{common.loadFailed}</p>
        <Button variant="secondary" onClick={() => void refetch()}>
          {common.retry}
        </Button>
      </div>
    );
  } else if (!data.available) {
    body = (
      <div className="mt-5">
        <EmptyState
          illustration={<ChartNoAxesColumn className="size-8" />}
          title={dashboard.noData}
          hint={dashboard.noDataHint}
        />
      </div>
    );
  } else {
    body = (
      <>
        <Kpis report={data.report} />
        <h2 className="mt-7">{dashboard.fieldsTitle}</h2>
        <div className="mt-3">
          <FieldAccuracyTable fields={data.report.per_field} />
        </div>
        <h2 className="mt-7">{dashboard.ablationTitle}</h2>
        <div className="mt-3">
          <AblationTable reports={data.ablation ?? []} />
        </div>
        <p className="mt-2 text-sm text-ink-muted">{dashboard.ablationEmptyNote}</p>
        <h2 className="mt-7">{dashboard.learningTitle}</h2>
        <div className="mt-3">
          <LearningChart points={data.learning ?? []} />
        </div>
      </>
    );
  }

  return (
    <>
      <h1>{dashboard.title}</h1>
      {body}
      {stressClient ? (
        <>
          <h2 className="mt-7">{dashboard.stressTitle}</h2>
          <div className="mt-3">
            <StressModeButton jobId={sourceJob} client={stressClient} />
          </div>
        </>
      ) : null}
      <p className="mt-7 border-t border-surface-border pt-3 text-sm text-ink-muted">
        {dashboard.measuredNote}
      </p>
    </>
  );
}
