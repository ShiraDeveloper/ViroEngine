import { Link, useParams } from 'react-router-dom';

import { common, processing } from '@/copy/he';
import { buttonClasses } from '@/design/Button';
import { EmptyState } from '@/design/EmptyState';
import { ltr } from '@/lib/rtl';
import { useDocumentTitle } from '@/lib/useDocumentTitle';
import { useJob } from '@/lib/useJob';

/** Stub: the Live Pipeline grid and SSE wiring arrive with T21/T22. Shows job progress only. */
export function Processing() {
  useDocumentTitle(processing.title);
  const { jobId = '' } = useParams();
  const { data: job, isPending, isError } = useJob(jobId);

  return (
    <>
      <h1>{processing.title}</h1>
      {isPending ? (
        <p role="status" className="mt-4 text-ink-muted">
          {common.loading}
        </p>
      ) : isError ? (
        <div className="mt-4">
          <EmptyState title={common.loadFailed} />
        </div>
      ) : (
        <div className="mt-4 flex flex-col items-start gap-4">
          <p aria-live="polite" className="text-lg text-ink-strong">
            {processing.progress(job.pages_done, job.page_count)}
          </p>
          <p className="text-sm text-ink-muted">
            {processing.pagesTitle}: {ltr(`${job.pages_done}/${job.page_count}`)}
          </p>
          <Link to={`/jobs/${encodeURIComponent(jobId)}/review`} className={buttonClasses('primary')}>
            {processing.goToReview}
          </Link>
        </div>
      )}
    </>
  );
}
