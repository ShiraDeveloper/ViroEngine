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

  const border = dragging
    ? 'border-teal-500 bg-teal-100 scale-[1.01]'
    : 'border-primary-300 bg-gradient-to-b from-surface-raised to-primary-100/40 hover:border-primary-500';
  return (
    <div className="relative">
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
        className={`flex cursor-pointer flex-col items-center gap-3 rounded-lg border-2 border-dashed px-5 py-8 text-center transition-all duration-base ease-token peer-focus-visible:shadow-focus peer-disabled:cursor-not-allowed peer-disabled:opacity-60 ${border}`}
      >
        <span className="grid size-16 place-items-center rounded-full bg-gradient-to-br from-primary-700 to-teal-500 text-ink-inverse shadow-raised">
          <UploadCloud aria-hidden="true" className="size-8" />
        </span>
        <span className="text-xl font-semibold text-ink-strong">{upload.dropTitle}</span>
        <span className="rounded-pill bg-surface-sunken px-3 py-1 text-xs font-medium text-ink-base">
          {upload.dropHint}
        </span>
        <span className="text-sm font-medium text-primary-700 underline underline-offset-4">
          {upload.browse}
        </span>
        <span className="sr-only">{upload.fileInputLabel}</span>
      </label>
    </div>
  );
}
