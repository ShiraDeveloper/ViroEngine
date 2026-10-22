import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { CommitmentRow } from '@/api/types';
import { review } from '@/copy/he';

import {
  candidate,
  cellOf,
  fakeClient,
  field,
  flaggedJob,
  job,
  renderReview,
  row,
} from './reviewSupport';

describe('review workspace', () => {
  it('renders_all_cell_states_with_icon_and_text', async () => {
    const client = fakeClient(
      job({
        rows: [
          row('r1', {
            sender: field('sender', 'A'),
            first_name: field('first_name', 'B'),
            last_name: field('last_name', 'C'),
            national_id: field('national_id', 'D'),
          }),
        ],
      }),
    );
    const { container } = renderReview(client);
    await screen.findByRole('table');

    for (const [key, level] of [
      ['sender', 'A'],
      ['first_name', 'B'],
      ['last_name', 'C'],
      ['national_id', 'D'],
    ] as const) {
      const cell = cellOf(container, 'r1', key);
      expect(cell).toHaveAttribute('data-level', level);
      expect(within(cell).getByText(review.levels[level])).toBeVisible();
      expect(cell.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    }
  });

  it('ribbon_shows_tick_per_flagged_field', async () => {
    const client = fakeClient(
      job({
        rows: [
          row('r1', {
            first_name: field('first_name', 'D'),
            last_name: field('last_name', 'D'),
            sender: field('sender', 'B'),
          }),
        ],
      }),
    );
    const { container } = renderReview(client);
    await screen.findByRole('table');
    expect(container.querySelectorAll('[data-ribbon] [data-tick]')).toHaveLength(2);
  });

  it('filter_shows_only_flagged_rows', async () => {
    const client = fakeClient(
      job({ rows: [row('r1', { sender: field('sender', 'A') }), row('r2', { sender: field('sender', 'D') })] }),
    );
    const { container } = renderReview(client);
    await screen.findByRole('table');
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: review.filterReview }));
    expect(container.querySelectorAll('tbody tr')).toHaveLength(1);
    expect(container.querySelector('tbody tr')).toHaveAttribute('data-row-level', 'D');
  });

  it('orphans_tab_lists_unattached_pages', async () => {
    const client = fakeClient(job({ rows: [], orphan_page_ids: ['o1', 'o2'] }));
    const { container } = renderReview(client);
    fireEvent.click(await screen.findByRole('button', { name: review.orphansTab }));

    await waitFor(() => expect(container.querySelectorAll('[data-orphan]')).toHaveLength(2));
    expect(screen.getAllByText(review.orphanReason)).toHaveLength(2);
    await waitFor(() => expect(client.fetchCrop).toHaveBeenCalledTimes(2));
    expect(client.fetchCrop.mock.calls.map((call) => call[1]).sort()).toEqual(['o1', 'o2']);
  });

  it.each([
    { minutesAhead: 5, warned: true },
    { minutesAhead: 6, warned: false },
    { minutesAhead: 1, warned: true },
  ])(
    'expiry_warning_appears_five_minutes_before_ttl: $minutesAhead min ahead -> $warned',
    async ({ minutesAhead, warned }) => {
      const now = new Date('2026-10-25T10:00:00Z');
      const expires = new Date(now.getTime() + minutesAhead * 60_000).toISOString();
      const client = fakeClient(job({ expires_at: expires, rows: [] }));
      renderReview(client, () => now);
      await screen.findByRole('heading', { name: review.title });
      if (warned) {
        expect(await screen.findByRole('alert')).toHaveTextContent(review.expiryWarning(minutesAhead));
      } else {
        expect(screen.queryByRole('alert')).toBeNull();
      }
    },
  );

  it('failed_edit_rolls_back_and_shows_toast', async () => {
    let reject: (reason: Error) => void = () => undefined;
    const pending = new Promise<CommitmentRow>((_resolve, fail) => {
      reject = fail;
    });
    const client = fakeClient(flaggedJob(), { editField: () => pending });
    const { container } = renderReview(client);
    await screen.findByRole('table');

    const target = () => cellOf(container, 'r2', 'receipt_date');
    fireEvent.click(within(target()).getByRole('button'));
    fireEvent.click(await screen.findByRole('button', { name: /^1\s.*18\/06\/2026/ }));

    // Optimistic: the value and the resolved state show before the server answers.
    await waitFor(() => expect(target()).toHaveAttribute('data-level', 'B'));
    expect(target()).toHaveTextContent('18/06/2026');

    await act(async () => {
      reject(new Error('network'));
      await Promise.resolve();
    });

    await waitFor(() => expect(target()).toHaveAttribute('data-level', 'D'));
    expect(target()).not.toHaveTextContent('18/06/2026');
    expect(await screen.findByText(review.editFailedTitle)).toBeInTheDocument();
  });

  it('confirm_navigates_to_export_when_it_succeeds', async () => {
    const client = fakeClient(job({ rows: [row('r1', { sender: field('sender', 'A') })] }));
    renderReview(client);
    fireEvent.click(await screen.findByRole('button', { name: review.confirm }));
    expect(await screen.findByText('export-page')).toBeInTheDocument();
    expect(client.confirm).toHaveBeenCalledWith('j1');
  });

  it('manual_typing_sends_value_edit', async () => {
    const client = fakeClient(flaggedJob());
    const { container } = renderReview(client);
    await screen.findByRole('table');
    fireEvent.click(within(cellOf(container, 'r2', 'receipt_date')).getByRole('button'));
    const input = await screen.findByLabelText(review.manualLabel);
    fireEvent.change(input, { target: { value: '17/06/2026' } });
    fireEvent.click(screen.getByRole('button', { name: review.manualApply }));
    await waitFor(() =>
      expect(client.editField).toHaveBeenCalledWith('j1', 'r2', 'receipt_date', { value: '17/06/2026' }),
    );
  });

  it('candidate_picker_offers_at_most_three_numbered_buttons', async () => {
    const many = job({
      rows: [
        row('r1', {
          first_name: field('first_name', 'D', {
            candidates: [candidate('a'), candidate('b'), candidate('c'), candidate('d')],
          }),
        }),
      ],
    });
    const { container } = renderReview(fakeClient(many));
    await screen.findByRole('table');
    fireEvent.click(within(cellOf(container, 'r1', 'first_name')).getByRole('button'));
    const picks = await screen.findAllByRole('button', { name: /^[1-3]\s/ });
    expect(picks).toHaveLength(3);
  });
});
