import { CheckCircle2, CircleAlert, FileText, Loader2, ScanText, ShieldCheck, Sparkles } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { processing, review } from '@/copy/he';
import { CLOUD_STAGES, STAGES, pipState } from '@/lib/pipelineState';
import type { Counters, PageProgress, PipState, StageId } from '@/lib/pipelineState';
import { ltr } from '@/lib/rtl';

export interface LivePipelineProps {
  pages: PageProgress[];
  counters: Counters;
  onSelectPage?: (id: string) => void;
}

const SEGMENT_FILL: Record<PipState, string> = {
  done: '',
  running: 'bg-primary-300 motion-safe:animate-pulse',
  pending: 'bg-surface-sunken',
  failed: 'bg-state-review-border',
};

/** A finished local stage fills with the local colour, the cloud read with the cloud colour. */
function segmentClasses(stage: StageId, state: PipState): string {
  const base = `h-1.5 flex-1 rounded-full transition-colors duration-base ${SEGMENT_FILL[state]}`;
  if (state !== 'done') return base;
  return `${base} ${CLOUD_STAGES.has(stage) ? 'bg-cloud' : 'bg-local'}`;
}

/** Thin segmented bar, one segment per stage (S0..S10); each keeps its spoken state. */
function StageBar({ page }: { page: PageProgress }) {
  return (
    <ol aria-label={processing.pipelineLabel} className="mt-3 flex gap-0.5">
      {STAGES.map((stage, index) => {
        const state = pipState(page, index);
        const cloud = CLOUD_STAGES.has(stage);
        return (
          <li
            key={stage}
            data-stage={stage}
            data-state={state}
            data-kind={cloud ? 'cloud' : 'local'}
            className={segmentClasses(stage, state)}
          >
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

/** What the page is doing now, in words: the stage family rather than a stage code. */
function phaseOf(page: PageProgress): { text: string; icon: LucideIcon } {
  if (page.status === 'failed') return { text: processing.failedPage, icon: CircleAlert };
  if (page.status === 'done') return { text: processing.pageDone, icon: CheckCircle2 };
  if (page.status === 'queued' || page.stageIndex < 0) return { text: processing.pageQueued, icon: FileText };
  const stage = STAGES[page.stageIndex] ?? 'S0';
  if (stage === 'S9') return { text: processing.phaseAi, icon: Sparkles };
  if (stage === 'S8' || stage === 'S10') return { text: processing.phaseVerify, icon: ShieldCheck };
  return { text: processing.phasePrepare, icon: ScanText };
}

/** "file#3" -> page number 3; anything else is shown as it is. */
function pageNumber(page: PageProgress): string {
  const tail = page.pageId.split('#').pop() ?? page.pageId;
  return page.label ?? tail;
}

function CounterStrip({ counters }: { counters: Counters }) {
  const items = [
    { label: processing.kpiLocal, value: counters.fieldsClosedLocal, key: 'local', tone: 'text-state-verified-fg' },
    { label: processing.kpiCrops, value: counters.cropsSent, key: 'crops', tone: 'text-teal-700' },
    { label: processing.kpiFlagged, value: counters.flagged, key: 'flagged', tone: 'text-state-review-fg' },
  ];
  return (
    <dl
      aria-label={processing.countersLabel}
      className="grid grid-cols-3 gap-3 rounded-xl border border-surface-border bg-surface-raised p-4 shadow-card"
    >
      {items.map(({ label, value, key, tone }) => (
        <div key={key} data-counter={key} className="flex flex-col rounded-lg bg-surface-base px-4 py-3">
          <dt className="text-xs text-ink-muted">{label}</dt>
          <dd className={`text-2xl font-bold ${tone}`}>{ltr(String(value))}</dd>
        </div>
      ))}
    </dl>
  );
}

const STATUS_TONE: Record<PageProgress['status'], string> = {
  done: 'bg-state-verified-bg text-state-verified-fg',
  running: 'bg-primary-100 text-primary-700',
  queued: 'bg-surface-sunken text-ink-muted',
  failed: 'bg-state-review-bg text-state-review-fg',
};

/** Counter strip plus one compact card per page: page number, phase in words, stage bar. */
export function LivePipeline({ pages, counters, onSelectPage }: LivePipelineProps) {
  return (
    <section className="flex flex-col gap-4">
      <CounterStrip counters={counters} />
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {pages.map((page) => {
          const phase = phaseOf(page);
          const PhaseIcon = page.status === 'running' ? Loader2 : phase.icon;
          const header = (
            <>
              <span className="flex items-center gap-2">
                <span className="grid size-9 place-items-center rounded-lg bg-primary-100 font-bold text-primary-900">
                  {ltr(pageNumber(page))}
                </span>
                <span className="font-semibold text-ink-strong">{processing.pageName(pageNumber(page))}</span>
              </span>
              <span
                className={`inline-flex items-center gap-1 rounded-pill px-2 py-1 text-xs font-medium ${STATUS_TONE[page.status]}`}
              >
                <PhaseIcon
                  aria-hidden="true"
                  className={`size-3.5 ${page.status === 'running' ? 'motion-safe:animate-spin' : ''}`}
                />
                {phase.text}
              </span>
            </>
          );
          return (
            <li
              key={page.pageId}
              data-page={page.pageId}
              data-status={page.status}
              className="rounded-xl border border-surface-border bg-surface-raised p-4 shadow-card transition-shadow duration-base hover:shadow-raised"
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
              <StageBar page={page} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
