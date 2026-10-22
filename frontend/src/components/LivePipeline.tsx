import { Check, CircleAlert, Cloud, Loader2 } from 'lucide-react';

import { processing, review } from '@/copy/he';
import { CLOUD_STAGES, STAGES, pipState } from '@/lib/pipelineState';
import type { Counters, PageProgress, PipState, StageId } from '@/lib/pipelineState';
import { ltr } from '@/lib/rtl';

export interface LivePipelineProps {
  pages: PageProgress[];
  counters: Counters;
  onSelectPage?: (id: string) => void;
}

const PIP_FILL: Record<PipState, string> = {
  done: 'border-transparent',
  running: 'border-primary-500 bg-surface-raised motion-safe:animate-pulse',
  pending: 'border-surface-border bg-surface-sunken',
  failed: 'border-state-review-border bg-state-review-bg',
};

/** Local stages fill with the local colour, the escalation stage with the cloud colour. */
function pipClasses(stage: StageId, state: PipState): string {
  const base = `inline-flex size-5 items-center justify-center rounded-full border ${PIP_FILL[state]}`;
  if (state !== 'done') return base;
  return `${base} ${CLOUD_STAGES.has(stage) ? 'bg-cloud' : 'bg-local'}`;
}

function PipIcon({ stage, state }: { stage: StageId; state: PipState }) {
  const className = 'size-3 text-ink-inverse';
  if (state === 'done') {
    return CLOUD_STAGES.has(stage) ? (
      <Cloud aria-hidden="true" className={className} />
    ) : (
      <Check aria-hidden="true" className={className} />
    );
  }
  if (state === 'running') return <Loader2 aria-hidden="true" className="size-3 text-primary-500" />;
  if (state === 'failed') return <CircleAlert aria-hidden="true" className="size-3 text-state-review-fg" />;
  return null;
}

function Pips({ page }: { page: PageProgress }) {
  return (
    <ol aria-label={processing.pipelineLabel} className="mt-3 flex flex-wrap gap-1">
      {STAGES.map((stage, index) => {
        const state = pipState(page, index);
        const cloud = CLOUD_STAGES.has(stage);
        return (
          <li
            key={stage}
            data-stage={stage}
            data-state={state}
            data-kind={cloud ? 'cloud' : 'local'}
            className={pipClasses(stage, state)}
          >
            <PipIcon stage={stage} state={state} />
            <span className="sr-only">
              {processing.stageNames[stage]}: {processing.stageState[state]}
              {cloud && state === 'done' ? `, ${review.cloudChip}` : ''}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function CounterStrip({ counters }: { counters: Counters }) {
  const items = [
    { label: processing.kpiLocal, value: counters.fieldsClosedLocal, key: 'local' },
    { label: processing.kpiCrops, value: counters.cropsSent, key: 'crops' },
    { label: processing.kpiFlagged, value: counters.flagged, key: 'flagged' },
  ];
  return (
    <dl
      aria-label={processing.countersLabel}
      className="flex flex-wrap gap-5 rounded-lg border border-surface-border bg-surface-raised p-4"
    >
      {items.map(({ label, value, key }) => (
        <div key={key} data-counter={key}>
          <dt className="text-sm text-ink-muted">{label}</dt>
          <dd className="text-2xl font-semibold text-ink-strong">{ltr(String(value))}</dd>
        </div>
      ))}
    </dl>
  );
}

function statusText(status: PageProgress['status']): string {
  if (status === 'done') return processing.pageDone;
  if (status === 'running') return processing.pageRunning;
  if (status === 'failed') return processing.failedPage;
  return processing.pageQueued;
}

/** Counter strip plus one card per page with eleven stage pips (S0..S10). */
export function LivePipeline({ pages, counters, onSelectPage }: LivePipelineProps) {
  return (
    <section className="flex flex-col gap-4">
      <CounterStrip counters={counters} />
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {pages.map((page) => {
          const name = processing.pageName(page.label ?? page.pageId);
          const header = (
            <>
              <span className="font-medium text-ink-strong">{name}</span>
              <span className="text-sm text-ink-muted">{statusText(page.status)}</span>
            </>
          );
          return (
            <li
              key={page.pageId}
              data-page={page.pageId}
              data-status={page.status}
              className="rounded-lg border border-surface-border bg-surface-raised p-4 shadow-card"
            >
              {onSelectPage ? (
                <button
                  type="button"
                  onClick={() => onSelectPage(page.pageId)}
                  className="flex w-full items-center justify-between gap-2 text-start"
                >
                  {header}
                </button>
              ) : (
                <div className="flex items-center justify-between gap-2">{header}</div>
              )}
              <Pips page={page} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
