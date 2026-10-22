import { CloudOff } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import { CandidatePicker } from '@/components/CandidatePicker';
import { EvidenceLens } from '@/components/EvidenceLens';
import { OrphanThumb } from '@/components/OrphanThumb';
import { ReadingTrail } from '@/components/ReadingTrail';
import { ReviewTable, visibleRows } from '@/components/ReviewTable';
import type { RowFilter, SelectedCell } from '@/components/ReviewTable';
import { common, dialogs, review } from '@/copy/he';
import type { ConfidenceLevel } from '@/api/types';
import { Badge } from '@/design/Badge';
import { ConfidenceRibbon } from '@/design/ConfidenceRibbon';
import { Button } from '@/design/Button';
import { Dialog } from '@/design/Dialog';
import { EmptyState } from '@/design/EmptyState';
import { columnLabel } from '@/lib/columns';
import { fieldText, isIsolatedField, minutesUntil } from '@/lib/format';
import { stepFlagged, useReviewKeyboard } from '@/lib/keyboard';
import { isFlagged } from '@/lib/level';
import {
  CLOCK_TICK_MS,
  cellOrder,
  findResult,
  flaggedOf,
  pageIdFor,
  shouldWarnExpiry,
} from '@/lib/review';
import { ltr } from '@/lib/rtl';
import { useDocumentTitle } from '@/lib/useDocumentTitle';
import { useJob } from '@/lib/useJob';
import { useNow } from '@/lib/useNow';
import { useReviewMutations } from '@/lib/useReviewMutations';

type Tab = 'rows' | 'orphans';

const SEGMENTS = 'flex gap-1 rounded-pill bg-surface-sunken p-1';

function segment(active: boolean): string {
  return `min-h-8 rounded-pill px-4 text-sm font-medium transition-colors duration-fast ${
    active ? 'bg-surface-raised text-primary-900 shadow-card' : 'text-ink-muted hover:text-ink-strong'
  }`;
}

export type ReviewPageClient = Pick<
  ApiClient,
  'getJob' | 'editField' | 'undo' | 'confirm' | 'fetchCrop'
>;

export interface ReviewProps {
  /** Injected in tests; the app uses the configured client. */
  client?: ReviewPageClient;
  /** Injected in tests so the expiry warning is deterministic. */
  clock?: () => Date;
}

const systemClock = (): Date => new Date();

export function Review({ client = api, clock = systemClock }: ReviewProps = {}) {
  useDocumentTitle(review.title);
  const { jobId = '' } = useParams();
  const navigate = useNavigate();
  const { data: job, isPending, isError, refetch } = useJob(jobId, client);
  const actions = useReviewMutations(jobId, client);
  const now = useNow(CLOCK_TICK_MS, clock);

  const [filter, setFilter] = useState<RowFilter>('all');
  const [tab, setTab] = useState<Tab>('rows');
  const [selected, setSelected] = useState<SelectedCell | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [manualSignal, setManualSignal] = useState(0);

  const rows = useMemo(() => job?.rows ?? [], [job]);
  const order = useMemo(() => cellOrder(visibleRows(rows, filter)), [rows, filter]);
  const flagged = useMemo(() => flaggedOf(rows, order), [rows, order]);

  const selectedRow = rows.find((row) => row.row_id === selected?.rowId);
  const selectedResult = findResult(rows, selected);

  function move(direction: 1 | -1): boolean {
    const next = stepFlagged(order, flagged, selected, direction);
    if (!next) return false;
    setSelected(next);
    return true;
  }

  function pick(index: number): void {
    if (!selected || !selectedResult?.candidates[index]) return;
    actions.edit(selected.rowId, selected.field, { pick_candidate_index: index });
  }

  function accept(): void {
    const first = selectedResult?.candidates[0];
    if (!selected || !selectedResult || !first) return;
    // Enter accepts what is highlighted; a cell that already holds that value has nothing to accept.
    if (!isFlagged(selectedResult.level) && first.value === selectedResult.value) return;
    pick(0);
  }

  useReviewKeyboard({
    flaggedCells: flagged,
    onNext: () => move(1),
    onPrev: () => move(-1),
    onAccept: accept,
    onPick: pick,
    onEdit: () => {
      if (selected) setManualSignal((signal) => signal + 1);
    },
    onUndo: actions.undo,
    onFilter: () => document.querySelector<HTMLElement>('[data-filter-group] button')?.focus(),
    onHelp: () => setHelpOpen(true),
    onEscape: () => setSelected(null),
  });

  // Keep DOM focus on the selected cell so Tab, Enter and screen readers all agree where she is.
  useEffect(() => {
    if (!selected) return;
    const cell = Array.from(document.querySelectorAll<HTMLElement>('td[data-row-id]')).find(
      (element) =>
        element.dataset.rowId === selected.rowId && element.dataset.field === selected.field,
    );
    cell?.querySelector<HTMLElement>('[data-cell-button]')?.focus();
  }, [selected]);

  if (isPending) {
    return (
      <>
        <h1>{review.title}</h1>
        <p role="status" className="mt-4 text-ink-muted">
          {common.loading}
        </p>
      </>
    );
  }

  if (isError) {
    return (
      <>
        <h1>{review.title}</h1>
        <div className="mt-4">
          <EmptyState
            title={common.loadFailed}
            action={<Button onClick={() => void refetch()}>{common.retry}</Button>}
          />
        </div>
      </>
    );
  }

  const selectedText = fieldText(selectedResult);
  const minutesLeft = minutesUntil(job.expires_at, now);
  const isolate = selected ? isIsolatedField(selected.field) : false;
  const pageId = pageIdFor(selectedRow);

  const levelCounts: Record<ConfidenceLevel, number> = { A: 0, B: 0, C: 0, D: 0 };
  for (const row of job.rows) {
    for (const result of Object.values(row.fields)) {
      if (result) levelCounts[result.level] += 1;
    }
  }

  const totalFields = levelCounts.A + levelCounts.B + levelCounts.C + levelCounts.D;
  const readiness =
    totalFields === 0 ? 0 : Math.round(((totalFields - levelCounts.D) / totalFields) * 100);
  const foundBeside = selectedResult?.candidates
    .flatMap((c) => c.notes ?? [])
    .find((n) => n.startsWith('label:'))
    ?.slice('label:'.length);

  async function onConfirm(): Promise<void> {
    if (await actions.confirm()) navigate(`/jobs/${encodeURIComponent(jobId)}/export`);
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1>{review.title}</h1>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)}>
            {dialogs.shortcutsOpen}
          </Button>
          <Button onClick={() => void onConfirm()} loading={actions.isConfirming}>
            {review.confirm}
          </Button>
        </div>
      </div>

      {shouldWarnExpiry(minutesLeft) ? (
        <p role="alert" className="mt-2 rounded-md border border-state-single-border bg-state-single-bg p-3 text-sm text-state-single-fg">
          {review.expiryWarning(minutesLeft)}
        </p>
      ) : null}

      {(job.notices ?? []).includes('AI_UNAVAILABLE') ? (
        <div
          role="alert"
          className="mt-4 flex items-start gap-3 rounded-xl border border-state-review-border bg-state-review-bg p-4 text-state-review-fg"
        >
          <CloudOff aria-hidden="true" className="mt-1 size-5 shrink-0" />
          <div className="flex flex-col gap-1">
            <p className="font-semibold">{review.aiUnavailableTitle}</p>
            <p className="text-sm">{review.aiUnavailableBody}</p>
          </div>
        </div>
      ) : null}

      <section
        aria-label={review.summaryLabel}
        className="mt-4 grid gap-4 rounded-xl border border-surface-border bg-surface-raised p-4 shadow-card lg:grid-cols-[220px_1fr]"
      >
        <div className="flex items-center gap-4 rounded-lg bg-gradient-to-bl from-brand-from via-brand-via to-brand-to p-4 text-ink-inverse">
          <div
            role="img"
            aria-label={review.readinessLabel(readiness)}
            className="grid size-20 shrink-0 place-items-center rounded-full"
            style={{
              background: `conic-gradient(var(--ring-fill) ${readiness * 3.6}deg, var(--ring-track) 0deg)`,
            }}
          >
            <span className="grid size-16 place-items-center rounded-full bg-primary-900 text-lg font-bold">
              {`${readiness}%`}
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-semibold">{review.readinessTitle}</span>

          </div>
        </div>
        <div className="flex flex-col gap-3">
        <dl className="grid grid-cols-3 gap-3">
          {[
            { label: review.statRows, value: job.rows.length, tone: 'text-primary-900' },
            {
              label: review.statVerified,
              value: levelCounts.A + levelCounts.B + levelCounts.C,
              tone: 'text-state-verified-fg',
            },
            { label: review.statFlagged, value: levelCounts.D, tone: 'text-state-review-fg' },
          ].map((stat) => (
            <div key={stat.label} className="flex flex-col rounded-lg bg-surface-base px-4 py-3">
              <dt className="order-2 text-xs text-ink-muted">{stat.label}</dt>
              <dd className={`order-1 text-2xl font-bold ${stat.tone}`}>{stat.value}</dd>
            </div>
          ))}
        </dl>
        <ConfidenceRibbon level="A" flaggedCount={levelCounts.D} aggregate={levelCounts} />
        <p aria-live="polite" className="sr-only">
          {flagged.length === 0 ? review.noFlaggedLeft : review.flaggedSummary(flagged.length)}
        </p>
        </div>
      </section>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {job.orphan_page_ids.length > 0 || tab === 'orphans' ? (
          <div role="group" aria-label={review.rowsTab} className={SEGMENTS}>
            {(['rows', 'orphans'] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={tab === value}
                onClick={() => setTab(value)}
                className={segment(tab === value)}
              >
                {value === 'rows' ? review.rowsTab : review.orphansTab}
              </button>
            ))}
          </div>
        ) : null}
        {tab === 'rows' ? (
          <div role="group" aria-label={review.filterLabel} data-filter-group="" className={SEGMENTS}>
            {(['all', 'review'] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
                className={segment(filter === value)}
              >
                {value === 'all' ? review.filterAll : review.filterReview}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {tab === 'orphans' ? (
        <section aria-labelledby="orphans-title" className="mt-5">
          <h2 id="orphans-title" className="text-xl">
            {review.orphansTitle}
          </h2>
          {job.orphan_page_ids.length === 0 ? (
            <div className="mt-3">
              <EmptyState title={review.orphansEmpty} />
            </div>
          ) : (
            <ul className="mt-3 flex flex-col gap-2">
              {job.orphan_page_ids.map((orphanId) => (
                <OrphanThumb key={orphanId} jobId={jobId} pageId={orphanId} client={client} />
              ))}
            </ul>
          )}
        </section>
      ) : (
        <div className="mt-5 grid gap-5 2xl:grid-cols-[minmax(0,3fr)_minmax(340px,1fr)]">
          <ReviewTable rows={job.rows} filter={filter} selected={selected} onSelectCell={setSelected} />
          <aside
            aria-labelledby="evidence-title"
            className="flex flex-col gap-4 self-start rounded-lg border border-surface-border bg-surface-raised p-4 shadow-card"
          >
            <h2 id="evidence-title" className="text-xl">
              {review.evidenceTitle}
            </h2>
            {selected && selectedResult ? (
              <>
                <div aria-live="polite" className="flex flex-col gap-2">
                  <p className="text-sm text-ink-muted">{columnLabel(selected.field)}</p>
                  <p className="text-lg text-ink-strong">
                    {selectedText === null ? common.notAvailable : isolate ? ltr(selectedText) : selectedText}
                  </p>
                  <Badge level={selectedResult.level} />
                  {foundBeside ? (
                    <p className="text-sm text-ink-base">
                      <span className="text-ink-muted">{review.foundBeside}</span>{' '}
                      <span className="font-semibold">{foundBeside}</span>
                    </p>
                  ) : null}
                  {selectedResult.reason ? (
                    <p className="text-sm text-ink-base">{selectedResult.reason}</p>
                  ) : selectedText === null ? (
                    <p className="text-sm text-ink-base">{review.emptyFieldNote}</p>
                  ) : null}
                </div>

                <EvidenceLens
                  jobId={jobId}
                  pageId={pageId ?? ''}
                  box={pageId ? selectedResult.box : null}
                  label={columnLabel(selected.field)}
                  client={client}
                />

                <section aria-labelledby="trail-title" className="flex flex-col gap-2">
                  <h3 id="trail-title" className="text-base font-semibold text-ink-strong">
                    {review.trailTitle}
                  </h3>
                  <ReadingTrail trail={selectedResult.trail} isolateValues={isolate} />
                </section>

                <section aria-labelledby="candidates-title" className="flex flex-col gap-2">
                  <h3 id="candidates-title" className="text-base font-semibold text-ink-strong">
                    {review.candidatesTitle}
                  </h3>
                  <CandidatePicker
                    candidates={selectedResult.candidates}
                    isolateValues={isolate}
                    focusManualSignal={manualSignal}
                    onPick={pick}
                    onType={(value) => actions.edit(selected.rowId, selected.field, { value })}
                    onClear={() => actions.edit(selected.rowId, selected.field, { value: null })}
                  />
                </section>
              </>
            ) : (
              <p className="text-sm text-ink-muted">{review.evidenceHint}</p>
            )}
          </aside>
        </div>
      )}

      <Dialog open={helpOpen} onOpenChange={setHelpOpen} title={dialogs.shortcutsTitle}>
        <dl className="flex flex-col gap-2">
          {review.shortcutRows.map((row) => (
            <div key={row.keys} className="flex items-baseline justify-between gap-4">
              <dt>{ltr(row.keys)}</dt>
              <dd className="text-sm text-ink-base">{row.action}</dd>
            </div>
          ))}
        </dl>
      </Dialog>
    </>
  );
}
