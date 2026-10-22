import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { FieldKey } from '@/api/types';
import { keyToAction, stepFlagged } from '@/lib/keyboard';
import type { CellRef, KeyLike } from '@/lib/keyboard';

import { fakeClient, flaggedJob, renderReview } from './reviewSupport';

const cell = (rowId: string, field: FieldKey): CellRef => ({ rowId, field });

const ORDER: CellRef[] = [
  cell('r1', 'first_name'),
  cell('r1', 'receipt_date'),
  cell('r2', 'receipt_date'),
  cell('r2', 'commitment_number'),
];
const FLAGGED: CellRef[] = [cell('r1', 'first_name'), cell('r2', 'receipt_date')];

function key(overrides: Partial<KeyLike>): KeyLike {
  return { key: '', code: '', shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...overrides };
}

function press(init: KeyboardEventInit): void {
  fireEvent.keyDown(document.activeElement ?? document.body, init);
}

describe('keyboard reducer', () => {
  it('tab_moves_only_between_flagged_cells', () => {
    expect(stepFlagged(ORDER, FLAGGED, null, 1)).toEqual(cell('r1', 'first_name'));
    expect(stepFlagged(ORDER, FLAGGED, cell('r1', 'first_name'), 1)).toEqual(cell('r2', 'receipt_date'));
    // From an unflagged cell it still lands on the next flagged one, never on a neighbour.
    expect(stepFlagged(ORDER, FLAGGED, cell('r1', 'receipt_date'), 1)).toEqual(cell('r2', 'receipt_date'));
    expect(stepFlagged(ORDER, FLAGGED, cell('r2', 'commitment_number'), -1)).toEqual(cell('r2', 'receipt_date'));
  });

  it('tab_past_either_end_returns_null_so_focus_can_leave', () => {
    expect(stepFlagged(ORDER, FLAGGED, cell('r2', 'receipt_date'), 1)).toBeNull();
    expect(stepFlagged(ORDER, FLAGGED, cell('r1', 'first_name'), -1)).toBeNull();
    expect(stepFlagged(ORDER, [], null, 1)).toBeNull();
  });

  it.each([
    { name: 'tab', event: key({ key: 'Tab' }), action: { type: 'next' } },
    { name: 'shift_tab', event: key({ key: 'Tab', shiftKey: true }), action: { type: 'prev' } },
    { name: 'enter', event: key({ key: 'Enter' }), action: { type: 'accept' } },
    { name: 'digit_2', event: key({ key: '2', code: 'Digit2' }), action: { type: 'pick', index: 1 } },
    { name: 'numpad_3', event: key({ key: '3', code: 'Numpad3' }), action: { type: 'pick', index: 2 } },
    { name: 'e_latin', event: key({ key: 'e', code: 'KeyE' }), action: { type: 'edit' } },
    // On a Hebrew layout the E key types a Hebrew letter; the physical key still works.
    { name: 'e_hebrew_layout', event: key({ key: 'ק', code: 'KeyE' }), action: { type: 'edit' } },
    { name: 'ctrl_z_hebrew_layout', event: key({ key: 'ז', code: 'KeyZ', ctrlKey: true }), action: { type: 'undo' } },
    { name: 'slash', event: key({ key: '/', code: 'Slash' }), action: { type: 'filter' } },
    { name: 'question', event: key({ key: '?', code: 'Slash', shiftKey: true }), action: { type: 'help' } },
    { name: 'escape', event: key({ key: 'Escape' }), action: { type: 'escape' } },
  ])('maps_$name_to_its_action', ({ event, action }) => {
    expect(keyToAction(event)).toEqual(action);
  });

  it.each([
    { name: 'digit_4', event: key({ key: '4', code: 'Digit4' }) },
    { name: 'ctrl_tab', event: key({ key: 'Tab', ctrlKey: true }) },
    { name: 'alt_e', event: key({ key: 'e', code: 'KeyE', altKey: true }) },
    { name: 'plain_letter', event: key({ key: 'x', code: 'KeyX' }) },
  ])('ignores_$name', ({ event }) => {
    expect(keyToAction(event)).toBeNull();
  });
});

describe('keyboard flow on the review screen', () => {
  async function ready() {
    const client = fakeClient(flaggedJob());
    const view = renderReview(client);
    await screen.findByRole('table');
    return { client, ...view };
  }

  function focusedCell(): string | null {
    const td = document.activeElement?.closest('td');
    return td ? `${td.dataset.rowId}/${td.dataset.field}` : null;
  }

  it('tab_walks_the_flagged_cells_in_order', async () => {
    await ready();
    press({ key: 'Tab' });
    await waitFor(() => expect(focusedCell()).toBe('r1/first_name'));
    press({ key: 'Tab' });
    await waitFor(() => expect(focusedCell()).toBe('r2/receipt_date'));
    press({ key: 'Tab', shiftKey: true });
    await waitFor(() => expect(focusedCell()).toBe('r1/first_name'));
  });

  it('number_keys_pick_candidates', async () => {
    const { client } = await ready();
    press({ key: 'Tab' });
    await waitFor(() => expect(focusedCell()).toBe('r1/first_name'));
    press({ key: '2', code: 'Digit2' });
    await waitFor(() =>
      expect(client.editField).toHaveBeenCalledWith('j1', 'r1', 'first_name', { pick_candidate_index: 1 }),
    );
  });

  it('number_key_without_that_candidate_does_nothing', async () => {
    const { client } = await ready();
    press({ key: 'Tab' });
    press({ key: 'Tab' });
    await waitFor(() => expect(focusedCell()).toBe('r2/receipt_date'));
    press({ key: '3', code: 'Digit3' });
    expect(client.editField).not.toHaveBeenCalled();
  });

  it('enter_accepts_highlighted_candidate', async () => {
    const { client } = await ready();
    press({ key: 'Tab' });
    await waitFor(() => expect(focusedCell()).toBe('r1/first_name'));
    press({ key: 'Enter' });
    await waitFor(() =>
      expect(client.editField).toHaveBeenCalledWith('j1', 'r1', 'first_name', { pick_candidate_index: 0 }),
    );
  });

  it('ctrl_z_calls_undo', async () => {
    const { client } = await ready();
    press({ key: 'Tab' });
    await waitFor(() => expect(focusedCell()).toBe('r1/first_name'));
    press({ key: '1', code: 'Digit1' });
    await waitFor(() => expect(client.editField).toHaveBeenCalledTimes(1));

    press({ key: 'z', code: 'KeyZ', ctrlKey: true });
    await waitFor(() => expect(client.undo).toHaveBeenCalledWith('j1', 'r1'));
  });

  it('ctrl_z_with_nothing_to_undo_makes_no_api_call', async () => {
    const { client } = await ready();
    press({ key: 'z', code: 'KeyZ', ctrlKey: true });
    expect(await screen.findByText('אין שינוי לביטול.')).toBeInTheDocument();
    expect(client.undo).not.toHaveBeenCalled();
  });

  it('shortcuts_are_ignored_while_typing_in_the_manual_field', async () => {
    const { client } = await ready();
    press({ key: 'Tab' });
    await waitFor(() => expect(focusedCell()).toBe('r1/first_name'));
    press({ key: 'e', code: 'KeyE' });
    const input = await screen.findByRole('textbox');
    await waitFor(() => expect(document.activeElement).toBe(input));
    fireEvent.keyDown(input, { key: '1', code: 'Digit1' });
    expect(client.editField).not.toHaveBeenCalled();
  });
});
