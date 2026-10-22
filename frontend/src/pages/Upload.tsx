import { X } from 'lucide-react';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, api } from '@/api/client';
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
    <>
      <h1>{upload.title}</h1>
      <div role="group" aria-label={upload.modeLabel} className="mt-5 flex gap-2">
        <Button
          variant={mode === 'single' ? 'primary' : 'secondary'}
          aria-pressed={mode === 'single'}
          onClick={() => chooseMode('single')}
        >
          {upload.single}
        </Button>
        <Button
          variant={mode === 'batch' ? 'primary' : 'secondary'}
          aria-pressed={mode === 'batch'}
          onClick={() => chooseMode('batch')}
        >
          {upload.batch}
        </Button>
      </div>
      <UploadDropzone multiple={mode === 'batch'} disabled={busy} onFiles={addFiles} />
      {rejected.length > 0 || failure ? (
        <ul role="alert" className="mt-4 flex flex-col gap-1 text-sm text-state-review-fg">
          {rejected.map(({ name, reason }) => (
            <li key={`${name}-${reason}`}>
              {ltr(name)} <span>{rejectionMessage(reason)}</span>
            </li>
          ))}
          {failure ? <li>{failure}</li> : null}
        </ul>
      ) : null}
      {files.length > 0 ? (
        <section aria-labelledby="selected-files" className="mt-5">
          <h2 id="selected-files" aria-live="polite" className="text-lg">
            {upload.selectedCount(files.length)}
          </h2>
          <ul className="mt-2 flex flex-col gap-2">
            {files.map((file) => (
              <li
                key={`${file.name}-${file.size}-${file.lastModified}`}
                className="flex items-center justify-between gap-3 rounded-md border border-surface-border bg-surface-raised p-3"
              >
                <span>
                  {ltr(file.name)} · {ltr(formatFileSize(file.size))}
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
          <div className="mt-4">
            <Button loading={busy} onClick={() => void start()}>
              {busy ? upload.uploading : upload.start}
            </Button>
          </div>
        </section>
      ) : null}
      <p className="mt-4 text-sm text-ink-muted">{upload.privacyNote}</p>
      <div className="mt-5">
        <Button
          variant="secondary"
          onClick={() => {
            setCurrentJobId(DEMO_JOB_ID);
            navigate(`/jobs/${DEMO_JOB_ID}/processing`);
          }}
        >
          {upload.demoJob}
        </Button>
      </div>
    </>
  );
}
