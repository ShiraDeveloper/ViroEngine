import { useEffect, useRef } from 'react';

import type { FieldKey } from '@/api/types';

export interface CellRef {
  rowId: string;
  field: FieldKey;
}

export function sameCell(a: CellRef | null, b: CellRef | null): boolean {
  return a !== null && b !== null && a.rowId === b.rowId && a.field === b.field;
}

/**
 * The next (`direction` 1) or previous (-1) flagged cell relative to `current`, in table order.
 * `current` need not be flagged: a cell that was just resolved is still the reference point.
 * Returns null past either end, so the caller can let the browser move focus on (no keyboard trap).
 */
export function stepFlagged(
  order: readonly CellRef[],
  flagged: readonly CellRef[],
  current: CellRef | null,
  direction: 1 | -1,
): CellRef | null {
  const position = (cell: CellRef): number => order.findIndex((other) => sameCell(other, cell));
  const known = current ? position(current) : -1;
  const from = known === -1 ? (direction === 1 ? -1 : order.length) : known;
  const ranked = flagged
    .map((cell) => ({ cell, at: position(cell) }))
    .filter(({ at }) => at !== -1 && (direction === 1 ? at > from : at < from))
    .sort((a, b) => (direction === 1 ? a.at - b.at : b.at - a.at));
  return ranked[0]?.cell ?? null;
}

export type KeyAction =
  | { type: 'next' }
  | { type: 'prev' }
  | { type: 'accept' }
  | { type: 'pick'; index: number }
  | { type: 'edit' }
  | { type: 'undo' }
  | { type: 'filter' }
  | { type: 'help' }
  | { type: 'escape' };

export interface KeyLike {
  key: string;
  code: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
}

const PICK_CODE = /^(?:Digit|Numpad)([1-3])$/;

/**
 * Map a key press to a review action (docs/PLAN.md §9.5). Letters use `code`, not `key`, so the
 * shortcuts work while the keyboard is set to Hebrew. Pure: no DOM access.
 */
export function keyToAction(event: KeyLike): KeyAction | null {
  const { key, code, shiftKey, ctrlKey, metaKey, altKey } = event;
  const command = ctrlKey || metaKey;
  const letter = (name: string): boolean => code === `Key${name.toUpperCase()}` || (code === '' && key.toLowerCase() === name);

  if (altKey) return null;
  if (key === 'Tab' && !command) return { type: shiftKey ? 'prev' : 'next' };
  if (command) return letter('z') && !shiftKey ? { type: 'undo' } : null;
  if (key === 'Enter') return { type: 'accept' };
  if (key === 'Escape') return { type: 'escape' };
  const pick = PICK_CODE.exec(code)?.[1] ?? (/^[1-3]$/.test(key) ? key : undefined);
  if (pick) return { type: 'pick', index: Number(pick) - 1 };
  if (letter('e')) return { type: 'edit' };
  if (code === 'Slash' || key === '/' || key === '?') {
    return shiftKey || key === '?' ? { type: 'help' } : { type: 'filter' };
  }
  return null;
}

export interface ReviewKeyboardOptions {
  /** Flagged cells in table order. Empty means Tab keeps its browser meaning. */
  flaggedCells: readonly CellRef[];
  /** Return false when there is nothing to move to, so Tab is left to the browser. */
  onNext(): boolean;
  onPrev(): boolean;
  onAccept(): void;
  onPick(index: number): void;
  onEdit(): void;
  onUndo(): void;
  onFilter?(): void;
  onHelp?(): void;
  onEscape?(): void;
}

function isTextEntry(target: HTMLElement | null): boolean {
  if (!target) return false;
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target.isContentEditable
  );
}

/** A control that owns Enter itself, unless it is a table cell button. */
function ownsEnter(target: HTMLElement | null): boolean {
  if (!target) return false;
  const control = target.closest('button, a, summary');
  return control !== null && !control.hasAttribute('data-cell-button');
}

/** Tab is only taken over from the page body or from inside the review table. */
function inTabScope(target: HTMLElement | null): boolean {
  return target === null || target === document.body || target.closest('[data-review-grid]') !== null;
}

/**
 * Review shortcuts as one window listener over a pure key map. Nothing fires while a dialog is
 * open or while the user types in a field.
 */
export function useReviewKeyboard(options: ReviewKeyboardOptions): void {
  const latest = useRef(options);
  latest.current = options;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.isComposing) return;
      if (document.querySelector('[role="dialog"]')) return;
      const action = keyToAction(event);
      if (!action) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (isTextEntry(target)) return;
      const handlers = latest.current;

      switch (action.type) {
        case 'next':
        case 'prev': {
          if (!inTabScope(target) || handlers.flaggedCells.length === 0) return;
          const moved = action.type === 'next' ? handlers.onNext() : handlers.onPrev();
          if (moved) event.preventDefault();
          return;
        }
        case 'accept':
          if (ownsEnter(target)) return;
          event.preventDefault();
          handlers.onAccept();
          return;
        case 'pick':
          event.preventDefault();
          handlers.onPick(action.index);
          return;
        case 'edit':
          event.preventDefault();
          handlers.onEdit();
          return;
        case 'undo':
          event.preventDefault();
          handlers.onUndo();
          return;
        case 'filter':
          event.preventDefault();
          handlers.onFilter?.();
          return;
        case 'help':
          event.preventDefault();
          handlers.onHelp?.();
          return;
        case 'escape':
          handlers.onEscape?.();
          return;
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
