import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Box } from '@/api/types';
import { EvidenceLens } from '@/components/EvidenceLens';
import { ReadingTrail } from '@/components/ReadingTrail';
import { review } from '@/copy/he';

import {
  BOX,
  cellOf,
  fakeClient,
  field,
  flaggedJob,
  job,
  renderReview,
  renderWithProviders,
  row,
  step,
} from './reviewSupport';

const FIRST: Box = [0.1, 0.2, 0.2, 0.6];
const SECOND: Box = [0.5, 0.1, 0.6, 0.9];

describe('evidence lens', () => {
  it('selecting_cell_requests_crop_with_correct_box', async () => {
    const client = fakeClient(flaggedJob());
    const { container } = renderReview(client);
    await screen.findByRole('table');

    fireEvent.click(within(cellOf(container, 'r2', 'receipt_date')).getByRole('button'));

    await waitFor(() => expect(client.fetchCrop).toHaveBeenCalled());
    const [jobId, pageId, box] = client.fetchCrop.mock.calls[0] ?? [];
    expect([jobId, pageId, box]).toEqual(['j1', 'p-r2', BOX]);
    expect(await screen.findByRole('img', { name: review.lensAlt('תאריך קבלה') })).toBeInTheDocument();
  });

  it('frame_moves_to_selected_field', async () => {
    const client = fakeClient(job());
    const view = renderWithProviders(
      <EvidenceLens jobId="j1" pageId="p1" box={FIRST} client={client} />,
    );
    const frame = () => screen.getByTestId('lens-frame');
    expect(frame()).toHaveAttribute('data-box', FIRST.join(','));
    expect(frame().style.top).toBe('10%');

    view.update(
      <EvidenceLens jobId="j1" pageId="p1" box={SECOND} client={client} />,
    );
    expect(frame()).toHaveAttribute('data-box', SECOND.join(','));
    expect(frame().style.top).toBe('50%');
    // The glide comes from a CSS transition on the frame, which reduced motion collapses.
    expect(frame().className).toContain('duration-base');
    expect(frame().className).toContain('motion-reduce:transition-none');
  });

  it('missing_image_shows_placeholder_not_crash', async () => {
    const client = fakeClient(job(), { fetchCrop: () => Promise.reject(new Error('404')) });
    const { container } = renderWithProviders(<EvidenceLens jobId="j1" pageId="p1" box={FIRST} client={client} />);
    expect(await screen.findByText(review.lensMissing)).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });

  it('missing_box_shows_placeholder_without_requesting_a_crop', () => {
    const client = fakeClient(job());
    renderWithProviders(<EvidenceLens jobId="j1" pageId="p1" box={null} client={client} />);
    expect(screen.getByText(review.lensNoBox)).toBeInTheDocument();
    expect(client.fetchCrop).not.toHaveBeenCalled();
  });

  it('field_without_box_selected_in_review_does_not_crash', async () => {
    const client = fakeClient(job({ rows: [row('r1', { first_name: field('first_name', 'D', { box: null }) })] }));
    const { container } = renderReview(client);
    await screen.findByRole('table');
    fireEvent.click(within(cellOf(container, 'r1', 'first_name')).getByRole('button'));
    expect(await screen.findByText(review.lensNoBox)).toBeInTheDocument();
    expect(client.fetchCrop).not.toHaveBeenCalled();
  });
});

describe('reading trail', () => {
  it('reading_trail_marks_cloud_step_distinctly', () => {
    const { container } = renderWithProviders(
      <ReadingTrail
        trail={[
          step({ reader: 'tesseract', cloud: false, value: 'a' }),
          step({ reader: 'gemini', cloud: true, value: 'b' }),
        ]}
      />,
    );
    const items = Array.from(container.querySelectorAll('li'));
    expect(items.map((li) => li.dataset.cloud)).toEqual(['false', 'true']);
    // Wording, icon and colour all differ; the note appears on the cloud step only.
    expect(within(items[0] as HTMLElement).queryByText(review.cloudChip)).toBeNull();
    expect(within(items[1] as HTMLElement).getByText(review.cloudChip)).toBeInTheDocument();
    expect(items[0]?.className).toContain('border-local');
    expect(items[1]?.className).toContain('border-cloud');
  });

  it('reading_trail_says_so_when_empty', () => {
    renderWithProviders(<ReadingTrail trail={[]} />);
    expect(screen.getByText(review.trailEmpty)).toBeInTheDocument();
  });
});
