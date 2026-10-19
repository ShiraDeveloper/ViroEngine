import { UploadCloud } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { upload } from '@/copy/he';
import { Button } from '@/design/Button';
import { DEMO_JOB_ID, setCurrentJobId } from '@/lib/session';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

type Mode = 'single' | 'batch';

/** Stub: the dropzone and upload flow arrive with T21. Mode selection and the demo link work. */
export function Upload() {
  useDocumentTitle(upload.title);
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>('single');

  return (
    <>
      <h1>{upload.title}</h1>
      <div role="group" aria-label={upload.modeLabel} className="mt-5 flex gap-2">
        <Button
          variant={mode === 'single' ? 'primary' : 'secondary'}
          aria-pressed={mode === 'single'}
          onClick={() => setMode('single')}
        >
          {upload.single}
        </Button>
        <Button
          variant={mode === 'batch' ? 'primary' : 'secondary'}
          aria-pressed={mode === 'batch'}
          onClick={() => setMode('batch')}
        >
          {upload.batch}
        </Button>
      </div>
      <div className="mt-5 flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-primary-300 bg-surface-raised p-7 text-center">
        <UploadCloud aria-hidden="true" className="size-8 text-primary-500" />
        <p className="text-lg font-semibold text-ink-strong">{upload.dropTitle}</p>
        <p className="text-sm text-ink-muted">{upload.dropHint}</p>
      </div>
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
