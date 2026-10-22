import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';

import type { Candidate } from '@/api/types';
import { review } from '@/copy/he';
import { Button } from '@/design/Button';
import { ltr } from '@/lib/rtl';

/** The keyboard offers keys 1-3, so no more candidates are shown. */
const MAX_CANDIDATES = 3;

export interface CandidatePickerProps {
  candidates: readonly Candidate[];
  onPick: (index: number) => void;
  onType: (value: string) => void;
  onClear: () => void;
  /** Isolate values as digits/dates (LTR). */
  isolateValues?: boolean;
  /** Changing this number moves focus to the manual entry box (the `E` shortcut). */
  focusManualSignal?: number;
}

/** Up to three numbered candidates, plus manual entry and clear. Nothing is chosen for her. */
export function CandidatePicker({
  candidates,
  onPick,
  onType,
  onClear,
  isolateValues = false,
  focusManualSignal = 0,
}: CandidatePickerProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    if (focusManualSignal > 0) inputRef.current?.focus();
  }, [focusManualSignal]);

  function submit(event: FormEvent): void {
    event.preventDefault();
    const value = draft.trim();
    if (value === '') return;
    onType(value);
    setDraft('');
    inputRef.current?.blur();
  }

  function onInputKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key !== 'Escape') return;
    event.stopPropagation();
    setDraft('');
    event.currentTarget.blur();
  }

  const shown = candidates.slice(0, MAX_CANDIDATES);

  return (
    <div className="flex flex-col gap-3">
      {shown.length === 0 ? (
        <p className="text-sm text-ink-muted">{review.noCandidates}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {shown.map((candidate, index) => (
            <li key={`${candidate.reader}-${index}`}>
              <Button
                variant="secondary"
                className="w-full justify-start"
                onClick={() => onPick(index)}
              >
                <span className="font-mono font-semibold">
                  {index + 1}
                </span>
                {/* Explicit spaces keep the accessible name readable: "1 18/06/2026 OCR". */}
                {' '}
                <span>
                  {isolateValues ? ltr(candidate.value) : candidate.value}
                </span>
                {' '}
                <span className="text-xs text-ink-muted">
                  {review.readers[candidate.reader]}
                </span>
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={submit} className="flex items-end gap-2">
        <label className="flex flex-1 flex-col gap-1 text-sm text-ink-base">
          {review.manualLabel}
          <input
            ref={inputRef}
            value={draft}
            dir={isolateValues ? 'ltr' : undefined}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onInputKeyDown}
            className="min-h-10 rounded-md border border-surface-border bg-surface-raised px-3 text-base text-ink-strong"
          />
        </label>
        <Button type="submit" size="md" disabled={draft.trim() === ''}>
          {review.manualApply}
        </Button>
      </form>

      <Button variant="ghost" size="sm" className="self-start" onClick={onClear}>
        {review.clearField}
      </Button>
    </div>
  );
}
