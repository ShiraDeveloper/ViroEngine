import { FileText, ShieldCheck, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, IS_MOCK, api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import { UploadDropzone } from '@/components/UploadDropzone';
import { upload } from '@/copy/he';
import { Button } from '@/design/Button';
import { formatFileSize } from '@/lib/format';
import { ltr } from '@/lib/rtl';
import { DEMO_JOB_ID, setCurrentJobId } from '@/lib/session';
import { checkFiles, rejectionMessage } from '@/lib/uploadValidation';
import type { Rejection } from '@/lib/uploadValidation';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

type Mode = 'single' | 'batch';

const SEGMENT =
  'min-h-9 rounded-pill px-4 text-sm font-medium transition-colors duration-fast ease-token';

export interface UploadProps {
  /** Injected in tests; the app uses the configured client. */
  client?: Pick<ApiClient, 'uploadSingle' | 'uploadBatch'>;
}

interface RejectedEntry {
  name: string;
  reason: Rejection;
}

function sameFile(a: File, b: File): boolean {
  return a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
}

/** Message for a failed upload; says what happened, not the server's body. */
function failureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return upload.accessDenied;
    if (error.status === 413) return upload.tooLarge;
    if (error.status === 415) return upload.rejectedType;
  }
  return upload.failed;
}

export function Upload({ client = api }: UploadProps = {}) {
  useDocumentTitle(upload.title);
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>('single');
  const [files, setFiles] = useState<File[]>([]);
  const [rejected, setRejected] = useState<RejectedEntry[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Synchronous guard: state updates are async, so a fast double-click would otherwise slip through.
  const inFlight = useRef(false);

  function chooseMode(next: Mode): void {
    setMode(next);
    if (next === 'single') setFiles((current) => current.slice(0, 1));
  }

  function addFiles(incoming: File[]): void {
    const { accepted, rejected: refused } = checkFiles(incoming);
    setRejected(refused.map(({ file, reason }) => ({ name: file.name, reason })));
    setFailure(null);
    setFiles((current) => {
      if (mode === 'single') return accepted.slice(0, 1).length > 0 ? accepted.slice(0, 1) : current;
      const fresh = accepted.filter((file) => !current.some((existing) => sameFile(existing, file)));
      return [...current, ...fresh];
    });
  }

  async function start(): Promise<void> {
    const first = files[0];
    if (inFlight.current || first === undefined) return;
    inFlight.current = true;
    setBusy(true);
    setFailure(null);
    // A fresh key per attempt: a retry after a failure is a new request, a double-click is not.
    const idempotencyKey = crypto.randomUUID();
    try {
      const result =
        mode === 'single'
          ? await client.uploadSingle(first, idempotencyKey)
          : await client.uploadBatch(files, idempotencyKey);
      setCurrentJobId(result.job_id);
      navigate(`/jobs/${encodeURIComponent(result.job_id)}/processing`);
    } catch (error) {
      setFailure(failureMessage(error));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <header className="flex flex-col gap-1 text-center">
        <h1 className="text-3xl">{upload.heroTitle}</h1>
        <p className="text-ink-muted">{upload.heroBody}</p>
      </header>
      <div>
        <section
          aria-labelledby="upload-title"
          className="flex flex-col gap-4 rounded-xl border border-surface-border bg-surface-raised p-5 shadow-card"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="upload-title" className="text-xl">
              {upload.title}
            </h2>
            <div
              role="group"
              aria-label={upload.modeLabel}
              className="flex gap-1 rounded-pill bg-surface-sunken p-1"
            >
              {(['single', 'batch'] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={mode === value}
                  onClick={() => chooseMode(value)}
                  className={`${SEGMENT} ${
                    mode === value
                      ? 'bg-primary-900 text-ink-inverse shadow-card'
                      : 'text-ink-base hover:bg-surface-raised'
                  }`}
                >
                  {value === 'single' ? upload.single : upload.batch}
                </button>
              ))}
            </div>
          </div>
          <UploadDropzone multiple={mode === 'batch'} disabled={busy} onFiles={addFiles} />
          {rejected.length > 0 || failure ? (
            <ul
              role="alert"
              className="flex flex-col gap-1 rounded-md border border-state-review-border bg-state-review-bg p-3 text-sm text-state-review-fg"
            >
              {rejected.map(({ name, reason }) => (
                <li key={`${name}-${reason}`}>
                  {ltr(name)} <span>{rejectionMessage(reason)}</span>
                </li>
              ))}
              {failure ? <li>{failure}</li> : null}
            </ul>
          ) : null}
          {files.length > 0 ? (
            <section aria-labelledby="selected-files" className="flex flex-col gap-3">
              <h3 id="selected-files" aria-live="polite" className="text-base font-semibold text-ink-strong">
                {upload.selectedCount(files.length)}
              </h3>
              <ul className="flex flex-col gap-2">
                {files.map((file) => (
                  <li
                    key={`${file.name}-${file.size}-${file.lastModified}`}
                    className="flex items-center justify-between gap-3 rounded-md border border-surface-border bg-surface-base px-3 py-2"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <FileText aria-hidden="true" className="size-4 shrink-0 text-primary-500" />
                      <span className="truncate">
                        {ltr(file.name)} · <span className="text-ink-muted">{ltr(formatFileSize(file.size))}</span>
                      </span>
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      aria-label={upload.remove(file.name)}
                      onClick={() => setFiles((current) => current.filter((other) => other !== file))}
                    >
                      <X aria-hidden="true" className="size-4" />
                    </Button>
                  </li>
                ))}
              </ul>
              <Button size="lg" loading={busy} onClick={() => void start()} className="w-full sm:w-auto">
                {busy ? upload.uploading : upload.start}
              </Button>
            </section>
          ) : null}
          <p className="flex items-start gap-2 text-sm text-ink-muted">
            <ShieldCheck aria-hidden="true" className="mt-1 size-4 shrink-0 text-teal-700" />
            <span>{upload.privacyNote}</span>
          </p>
        </section>

        {IS_MOCK ? (
          <div className="mt-3 text-center">
            <Button
              variant="ghost"
              onClick={() => {
                setCurrentJobId(DEMO_JOB_ID);
                navigate(`/jobs/${DEMO_JOB_ID}/processing`);
              }}
            >
              {upload.demoJob}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
