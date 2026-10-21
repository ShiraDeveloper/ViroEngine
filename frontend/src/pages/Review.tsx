import { useState } from 'react';
import { useParams } from 'react-router-dom';

import type { CommitmentRow } from '@/api/types';
import { ReviewTable } from '@/components/ReviewTable';
import type { RowFilter, SelectedCell } from '@/components/ReviewTable';
import { common, review } from '@/copy/he';
import { Badge } from '@/design/Badge';
import { Button } from '@/design/Button';
import { EmptyState } from '@/design/EmptyState';
import { columnLabel } from '@/lib/columns';
import { fieldText, isIsolatedField, minutesUntil } from '@/lib/format';
import { ltr } from '@/lib/rtl';
import { useDocumentTitle } from '@/lib/useDocumentTitle';
import { useJob } from '@/lib/useJob';

type Tab = 'rows' | 'orphans';

function findCell(rows: readonly CommitmentRow[], selected: SelectedCell | null) {
  if (!selected) return undefined;
  return rows.find((row) => row.row_id === selected.rowId)?.fields[selected.field];
}

export function Review() {
  useDocumentTitle(review.title);
  const { jobId = '' } = useParams();
  const { data: job, isPending, isError, refetch } = useJob(jobId);
  const [filter, setFilter] = useState<RowFilter>('all');
  const [tab, setTab] = useState<Tab>('rows');
  const [selected, setSelected] = useState<SelectedCell | null>(null);

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

  const selectedResult = findCell(job.rows, selected);
  const selectedText = fieldText(selectedResult);
  const minutesLeft = minutesUntil(job.expires_at, new Date());

  return (
    <>
      <h1>{review.title}</h1>
      {minutesLeft > 0 ? (
        <p className="mt-2 text-sm text-ink-muted">
          {review.expiryWarning(minutesLeft)}
        </p>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <div role="group" aria-label={review.rowsTab} className="flex gap-2">
          <Button
            variant={tab === 'rows' ? 'primary' : 'secondary'}
            size="sm"
            aria-pressed={tab === 'rows'}
            onClick={() => setTab('rows')}
          >
            {review.rowsTab}
          </Button>
          <Button
            variant={tab === 'orphans' ? 'primary' : 'secondary'}
            size="sm"
            aria-pressed={tab === 'orphans'}
            onClick={() => setTab('orphans')}
          >
            {review.orphansTab}
          </Button>
        </div>
        {tab === 'rows' ? (
          <div role="group" aria-label={review.filterLabel} className="flex gap-2">
            <Button
              variant={filter === 'all' ? 'primary' : 'secondary'}
              size="sm"
              aria-pressed={filter === 'all'}
              onClick={() => setFilter('all')}
            >
              {review.filterAll}
            </Button>
            <Button
              variant={filter === 'review' ? 'primary' : 'secondary'}
              size="sm"
              aria-pressed={filter === 'review'}
              onClick={() => setFilter('review')}
            >
              {review.filterReview}
            </Button>
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
              {job.orphan_page_ids.map((pageId) => (
                <li key={pageId} className="rounded-md border border-surface-border bg-surface-raised p-3">
                  {ltr(pageId)}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <ReviewTable
            rows={job.rows}
            filter={filter}
            selected={selected}
            onSelectCell={setSelected}
          />
          <aside aria-labelledby="evidence-title" className="rounded-lg border border-surface-border bg-surface-raised p-4 shadow-card">
            <h2 id="evidence-title" className="text-xl">
              {review.evidenceTitle}
            </h2>
            {selected && selectedResult ? (
              <div aria-live="polite" className="mt-3 flex flex-col gap-2">
                <p className="text-sm text-ink-muted">{columnLabel(selected.field)}</p>
                <p className="text-lg text-ink-strong">
                  {selectedText === null
                    ? common.notAvailable
                    : isIsolatedField(selected.field)
                      ? ltr(selectedText)
                      : selectedText}
                </p>
                <Badge level={selectedResult.level} />
                {selectedResult.reason ? (
                  <p className="text-sm text-ink-base">{selectedResult.reason}</p>
                ) : selectedText === null ? (
                  <p className="text-sm text-ink-base">{review.emptyFieldNote}</p>
                ) : null}
              </div>
            ) : (
              <p className="mt-3 text-sm text-ink-muted">{review.evidenceHint}</p>
            )}
          </aside>
        </div>
      )}
      <p className="mt-5 text-sm text-ink-muted">{review.shortcuts}</p>
    </>
  );
}
