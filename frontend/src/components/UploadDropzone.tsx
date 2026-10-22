import { UploadCloud } from 'lucide-react';
import { useId, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';

import { upload } from '@/copy/he';
import { ACCEPT_ATTRIBUTE } from '@/lib/uploadValidation';

export interface UploadDropzoneProps {
  /** Batch mode accepts many files; single mode takes one. */
  multiple: boolean;
  disabled?: boolean;
  onFiles: (files: File[]) => void;
}

/**
 * Drop target backed by a real file input, so it is operable by keyboard and screen reader:
 * the input is visually hidden but focusable, and the label shows its focus ring.
 */
export function UploadDropzone({ multiple, disabled = false, onFiles }: UploadDropzoneProps) {
  const inputId = useId();
  const [dragging, setDragging] = useState(false);

  function onChange(event: ChangeEvent<HTMLInputElement>): void {
    const files = Array.from(event.target.files ?? []);
    if (files.length > 0) onFiles(files);
    // Allow choosing the same file again after removing it.
    event.target.value = '';
  }

  function onDrop(event: DragEvent<HTMLLabelElement>): void {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  }

  const border = dragging ? 'border-primary-500 bg-primary-100' : 'border-primary-300 bg-surface-raised';
  return (
    <div className="relative mt-5">
      <input
        id={inputId}
        type="file"
        multiple={multiple}
        accept={ACCEPT_ATTRIBUTE}
        disabled={disabled}
        onChange={onChange}
        className="peer sr-only"
      />
      <label
        htmlFor={inputId}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex cursor-pointer flex-col items-center gap-3 rounded-lg border-2 border-dashed p-7 text-center peer-focus-visible:shadow-focus peer-disabled:cursor-not-allowed peer-disabled:opacity-60 ${border}`}
      >
        <UploadCloud aria-hidden="true" className="size-8 text-primary-500" />
        <span className="text-lg font-semibold text-ink-strong">{upload.dropTitle}</span>
        <span className="text-sm text-ink-muted">{upload.dropHint}</span>
        <span className="text-sm font-medium text-primary-700 underline">{upload.browse}</span>
        <span className="sr-only">{upload.fileInputLabel}</span>
      </label>
    </div>
  );
}
