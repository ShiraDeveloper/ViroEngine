import { Braces, FileSpreadsheet, FileText, TriangleAlert } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useState } from 'react';
import { useParams } from 'react-router-dom';

import { api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import { common, exportScreen } from '@/copy/he';
import { Button } from '@/design/Button';
import { EmptyState } from '@/design/EmptyState';
import { useToast } from '@/design/Toast';
import { useDocumentTitle } from '@/lib/useDocumentTitle';
import { useJob } from '@/lib/useJob';
import { flaggedFields } from '@/components/ReviewTable';

type Format = 'xlsx' | 'csv' | 'json';

const FORMATS: { fmt: Format; label: string; hint: string; icon: LucideIcon }[] = [
  { fmt: 'xlsx', label: exportScreen.xlsx, hint: exportScreen.formatHints.xlsx, icon: FileSpreadsheet },
  { fmt: 'csv', label: exportScreen.csv, hint: exportScreen.formatHints.csv, icon: FileText },
  { fmt: 'json', label: exportScreen.json, hint: exportScreen.formatHints.json, icon: Braces },
];

export interface ExportProps {
  /** Injected in tests; the app uses the configured client. */
  client?: Pick<ApiClient, 'getJob' | 'fetchExport'>;
}

/** Hand a fetched file to the browser under `name`. */
function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Summary, a warning while level-D fields remain, one card per format and the API note. */
export function Export({ client = api }: ExportProps = {}) {
  useDocumentTitle(exportScreen.title);
  const toast = useToast();
  const { jobId = '' } = useParams();
  const { data: job, isPending, isError } = useJob(jobId, client);
  const [busy, setBusy] = useState<Format | null>(null);

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
  const total = job.rows.reduce((sum, row) => sum + Object.keys(row.fields).length, 0);

  async function download(fmt: Format): Promise<void> {
    setBusy(fmt);
    try {
      const blob = await client.fetchExport(jobId, fmt);
      saveBlob(blob, `safeline-${jobId.slice(0, 8)}.${fmt}`);
      toast.push({ tone: 'success', title: exportScreen.done });
    } catch {
      toast.push({ tone: 'error', title: exportScreen.downloadFailed });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <h1>{exportScreen.title}</h1>
      <p className="text-lg text-ink-strong">{exportScreen.summary(job.rows.length, flagged)}</p>
      <dl className="grid grid-cols-3 gap-3">
        {[
          { label: exportScreen.rowsLabel, value: job.rows.length, tone: 'text-primary-900' },
          { label: exportScreen.verifiedLabel, value: total - flagged, tone: 'text-state-verified-fg' },
          { label: exportScreen.flaggedLabel, value: flagged, tone: 'text-state-review-fg' },
        ].map((stat) => (
          <div
            key={stat.label}
            className="flex flex-col rounded-lg border border-surface-border bg-surface-raised px-4 py-3 shadow-card"
          >
            <dt className="order-2 text-xs text-ink-muted">{stat.label}</dt>
            <dd className={`order-1 text-2xl font-bold ${stat.tone}`}>{stat.value}</dd>
          </div>
        ))}
      </dl>
      {flagged > 0 ? (
        <p className="flex items-start gap-2 rounded-md border border-state-review-border bg-state-review-bg p-3 text-sm text-state-review-fg">
          <TriangleAlert aria-hidden="true" className="mt-1 size-4 shrink-0" />
          <span>{exportScreen.warnFlagged}</span>
        </p>
      ) : null}
      <div className="grid gap-4 md:grid-cols-3">
        {FORMATS.map(({ fmt, label, hint, icon: Icon }) => (
          <article
            key={fmt}
            className={`flex flex-col gap-3 rounded-xl border bg-surface-raised p-5 shadow-card ${
              fmt === 'xlsx' ? 'border-primary-500 ring-2 ring-primary-100' : 'border-surface-border'
            }`}
          >
            <span
              className={`grid size-12 place-items-center rounded-lg ${
                fmt === 'xlsx' ? 'bg-state-verified-bg text-state-verified-fg' : 'bg-surface-sunken text-primary-700'
              }`}
            >
              <Icon aria-hidden="true" className="size-6" />
            </span>
            <p className="text-sm text-ink-muted">{hint}</p>
            <Button
              variant={fmt === 'xlsx' ? 'primary' : 'secondary'}
              loading={busy === fmt}
              onClick={() => void download(fmt)}
            >
              {label}
            </Button>
          </article>
        ))}
      </div>
      <p className="text-sm text-ink-muted">{exportScreen.apiNote}</p>
    </div>
  );
}
