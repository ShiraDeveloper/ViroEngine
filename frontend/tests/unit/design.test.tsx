import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { mockClient, resetMock } from '@/api/mockClient';
import type { CommitmentRow, ConfidenceLevel, JobView } from '@/api/types';
import { ReviewTable } from '@/components/ReviewTable';
import { review } from '@/copy/he';
import { Badge } from '@/design/Badge';
import { ltr } from '@/lib/rtl';

const SRC_DIR = path.resolve(__dirname, '../../src');
const LEVELS: ConfidenceLevel[] = ['A', 'B', 'C', 'D'];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

async function fixtureRows(): Promise<CommitmentRow[]> {
  const job: JobView = await mockClient.getJob('demo');
  return job.rows;
}

describe('design system', () => {
  beforeEach(() => resetMock());

  it.each(LEVELS)('renders_all_four_badge_levels_with_icon_and_text: level %s', (level) => {
    const { container } = render(<Badge level={level} />);
    expect(screen.getByText(review.levels[level])).toBeVisible();
    expect(container.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(container.firstElementChild).toHaveAttribute('data-level', level);
  });

  it('ltr_wrapper_isolates_numbers', () => {
    render(<p>{ltr('X132767')}</p>);
    const span = screen.getByText('X132767');
    expect(span).toHaveAttribute('dir', 'ltr');
    expect(span).toHaveClass('font-mono', 'tabular-nums');
  });

  it('review_table_renders_fixture_rows', async () => {
    const rows = await fixtureRows();
    render(<ReviewTable rows={rows} filter="all" selected={null} />);

    const body = screen.getAllByRole('rowgroup')[1];
    expect(body).toBeDefined();
    const bodyRows = within(body as HTMLElement).getAllByRole('row');
    expect(bodyRows).toHaveLength(4);

    const cell = (rowIndex: number, field: string): Element | null =>
      bodyRows[rowIndex]?.querySelector(`[data-field="${field}"]`) ?? null;

    // Green: barcode-verified central number (level A).
    expect(cell(0, 'central_number')).toHaveClass('bg-state-verified-bg');
    // Amber: single-read commitment number (level C).
    expect(cell(1, 'commitment_number')).toHaveClass('bg-state-single-bg');
    // Orange: conflicting receipt date, and the empty flagged commitment (level D).
    expect(cell(2, 'receipt_date')).toHaveClass('bg-state-review-bg');
    expect(cell(3, 'commitment_number')).toHaveClass('bg-state-review-bg');

    // Ribbon on every row, coloured by row level.
    expect(bodyRows.map((row) => row.getAttribute('data-row-level'))).toEqual(['A', 'C', 'D', 'D']);
    expect(bodyRows[0]?.querySelector('[data-ribbon="A"]')).not.toBeNull();
  });

  it('review_table_never_renders_a_guess_for_empty_fields', async () => {
    const rows = await fixtureRows();
    render(<ReviewTable rows={rows} filter="review" selected={null} />);
    // The empty commitment number shows its reason, not a value.
    expect(screen.getByText('לא נמצא בטופס')).toBeInTheDocument();
  });

  it('no_literal_hex_in_components', () => {
    const allowed = new Set(['tokens.ts', 'tailwind.config.ts']);
    const hex = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/;
    const offenders = sourceFiles(SRC_DIR)
      .filter((file) => !allowed.has(path.basename(file)))
      .filter((file) => hex.test(readFileSync(file, 'utf8')))
      .map((file) => path.relative(SRC_DIR, file));
    expect(offenders).toEqual([]);
  });

  it('no_physical_direction_classes_in_components', () => {
    const physical = /["'`\s:](?:ml|mr|pl|pr)-\d|\btext-(?:left|right)\b/;
    const offenders = sourceFiles(SRC_DIR)
      .filter((file) => physical.test(readFileSync(file, 'utf8')))
      .map((file) => path.relative(SRC_DIR, file));
    expect(offenders).toEqual([]);
  });
});
