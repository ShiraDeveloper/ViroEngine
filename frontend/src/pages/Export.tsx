import { useParams } from 'react-router-dom';

import { api } from '@/api/client';
import { common, exportScreen } from '@/copy/he';
import { buttonClasses } from '@/design/Button';
import { EmptyState } from '@/design/EmptyState';
import { useToast } from '@/design/Toast';
import { useDocumentTitle } from '@/lib/useDocumentTitle';
import { useJob } from '@/lib/useJob';
import { flaggedFields } from '@/components/ReviewTable';

const FORMATS = [
  { fmt: 'xlsx', label: exportScreen.xlsx },
  { fmt: 'csv', label: exportScreen.csv },
  { fmt: 'json', label: exportScreen.json },
] as const;

/** Summary, a warning while level-D fields remain, three download links and the API note. */
export function Export() {
  useDocumentTitle(exportScreen.title);
  const toast = useToast();
  const { jobId = '' } = useParams();
  const { data: job, isPending, isError } = useJob(jobId);

  if (isPending || isError) {
    return (
      <>
        <h1>{exportScreen.title}</h1>
        {isPending ? (
          <p role="status" className="mt-4 text-ink-muted">
            {common.loading}
          </p>
        ) : (
          <div className="mt-4">
            <EmptyState title={common.loadFailed} />
          </div>
        )}
      </>
    );
  }

  const flagged = job.rows.reduce((sum, row) => sum + flaggedFields(row).length, 0);

  return (
    <>
      <h1>{exportScreen.title}</h1>
      <p className="mt-4 text-lg text-ink-strong">{exportScreen.summary(job.rows.length, flagged)}</p>
      {flagged > 0 ? (
        <p className="mt-2 rounded-md bg-state-review-bg p-3 text-sm text-state-review-fg">
          {exportScreen.warnFlagged}
        </p>
      ) : null}
      <div className="mt-5 flex flex-wrap gap-3">
        {FORMATS.map(({ fmt, label }) => (
          <a
            key={fmt}
            href={api.exportUrl(jobId, fmt)}
            download
            onClick={() => toast.push({ tone: 'success', title: exportScreen.done })}
            className={buttonClasses(fmt === 'xlsx' ? 'primary' : 'secondary')}
          >
            {label}
          </a>
        ))}
      </div>
      <p className="mt-5 text-sm text-ink-muted">{exportScreen.apiNote}</p>
    </>
  );
}
