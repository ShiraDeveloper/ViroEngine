import { useEffect, useRef } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import type { ResilientOptions } from '@/api/sse';
import { LivePipeline } from '@/components/LivePipeline';
import { common, processing } from '@/copy/he';
import { buttonClasses } from '@/design/Button';
import { EmptyState } from '@/design/EmptyState';
import { useDocumentTitle } from '@/lib/useDocumentTitle';
import { useJobProgress } from '@/lib/useJobProgress';
import type { ProgressClient } from '@/lib/useJobProgress';

export interface ProcessingProps {
  /** Injected in tests; the app uses the configured client. */
  client?: ProgressClient;
  resilient?: ResilientOptions;
}

/** Live Pipeline on SSE. Opens the review screen on `done` unless the user has scrolled. */
export function Processing({ client, resilient }: ProcessingProps = {}) {
  useDocumentTitle(processing.title);
  const { jobId = '' } = useParams();
  const navigate = useNavigate();
  const { isPending, isError, state, degraded } = useJobProgress(jobId, client, resilient);

  // An unexpected navigation while the user is reading is worse than a click.
  const userScrolled = useRef(false);
  useEffect(() => {
    // Only a scroll away from the top counts: browsers also fire scroll events for layout changes.
    const onScroll = (): void => {
      if (window.scrollY > 0) userScrolled.current = true;
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const doneStatus = state?.doneStatus ?? null;
  const reviewPath = `/jobs/${encodeURIComponent(jobId)}/review`;
  useEffect(() => {
    const reviewable = doneStatus === 'needs_review' || doneStatus === 'confirmed';
    if (reviewable && !userScrolled.current) navigate(reviewPath);
  }, [doneStatus, navigate, reviewPath]);

  if (isPending) {
    return (
      <>
        <h1>{processing.title}</h1>
        <p role="status" className="mt-4 text-ink-muted">
          {common.loading}
        </p>
      </>
    );
  }
  if (isError || !state) {
    return (
      <>
        <h1>{processing.title}</h1>
        <div className="mt-4">
          <EmptyState title={common.loadFailed} />
        </div>
      </>
    );
  }

  const { counters } = state;
  const percent =
    doneStatus !== null
      ? 100
      : counters.pagesTotal > 0
        ? Math.round((counters.pagesDone / counters.pagesTotal) * 100)
        : 0;
  return (
    <>
      <h1>{processing.title}</h1>
      <div className="mt-4 flex flex-col gap-4">
        <div className="flex flex-col gap-2 rounded-xl border border-surface-border bg-surface-raised p-4 shadow-card">
          <div className="flex items-center justify-between gap-3">
            <p aria-live="polite" className="text-lg text-ink-strong">
              {doneStatus ? processing.finished : processing.progress(counters.pagesDone, counters.pagesTotal)}
            </p>
            <span className="text-2xl font-bold text-primary-700">{`${percent}%`}</span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            aria-label={processing.pipelineLabel}
            className="h-3 overflow-hidden rounded-pill bg-surface-sunken"
          >
            <div
              className="h-full rounded-pill bg-gradient-to-l from-teal-500 to-primary-500 transition-all duration-slow ease-token"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
        {degraded ? (
          <p className="rounded-md bg-state-neutral-bg p-3 text-sm text-state-neutral-fg">
            {processing.pollingNotice}
          </p>
        ) : null}
        <LivePipeline pages={state.pages} counters={counters} />
        <div className="flex flex-col items-start gap-1">
          {/* Until the scan reaches 100% the review would show half a job: the button waits. */}
          {doneStatus ? (
            <Link to={reviewPath} className={buttonClasses('primary')}>
              {processing.goToReview}
            </Link>
          ) : (
            <>
              <button
                type="button"
                disabled
                aria-describedby="review-wait"
                className={buttonClasses('secondary')}
              >
                {processing.goToReview}
              </button>
              <p id="review-wait" className="text-sm text-ink-muted">
                {processing.reviewWhenDone}
              </p>
            </>
          )}
        </div>
      </div>
    </>
  );
}
