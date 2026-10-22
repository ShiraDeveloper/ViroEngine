import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';
import type { Mock } from 'vitest';

import type {
  Candidate,
  CommitmentRow,
  ConfidenceLevel,
  FieldKey,
  FieldResult,
  JobView,
  ReadingTrailStep,
} from '@/api/types';
import { ToastProvider } from '@/design/Toast';
import { Review } from '@/pages/Review';
import type { ReviewPageClient } from '@/pages/Review';

export const BOX: [number, number, number, number] = [0.3, 0.55, 0.34, 0.8];

export function candidate(value: string, reader: Candidate['reader'] = 'tesseract'): Candidate {
  return {
    value,
    reader,
    source: 'printed',
    box: BOX,
    reader_confidence: 0.8,
    label_distance: null,
    region: null,
  };
}

export function step(overrides: Partial<ReadingTrailStep> = {}): ReadingTrailStep {
  return {
    stage: 'S6',
    reader: 'tesseract',
    outcome: 'value',
    value: 'abc',
    reader_confidence: 0.8,
    duration_ms: 10,
    cloud: false,
    ...overrides,
  };
}

export function field(
  key: FieldKey,
  level: ConfidenceLevel,
  overrides: Partial<FieldResult> = {},
): FieldResult {
  return {
    field: key,
    value: level === 'D' ? null : `${key}-value`,
    display_value: level === 'D' ? null : `${key}-value`,
    level,
    score: 0.5,
    source: 'printed',
    box: BOX,
    candidates: [],
    validations: [],
    trail: [],
    reason: level === 'D' ? 'reason-text' : null,
    escalated: false,
    ...overrides,
  };
}

export function row(
  rowId: string,
  fields: Partial<Record<FieldKey, FieldResult>>,
  pageIds: string[] = ['p-' + rowId],
): CommitmentRow {
  const levels = Object.values(fields).map((f) => f.level);
  return {
    row_id: rowId,
    group_id: 'g-' + rowId,
    page_ids: pageIds,
    fields,
    central_numbers: [],
    row_level: levels.includes('D') ? 'D' : 'B',
    status: 'auto',
  };
}

export function job(overrides: Partial<JobView> = {}): JobView {
  return {
    job_id: 'j1',
    status: 'needs_review',
    expires_at: '2099-01-01T00:00:00+00:00',
    page_count: 4,
    pages_done: 4,
    rows: [],
    orphan_page_ids: [],
    report: null,
    ...overrides,
  };
}

/** Two flagged fields, in reading order: r1 first_name, then r2 receipt_date. */
export function flaggedJob(): JobView {
  return job({
    rows: [
      row('r1', {
        first_name: field('first_name', 'D', {
          candidates: [candidate('anna'), candidate('annie', 'gemini')],
          trail: [step({ value: 'anna' }), step({ reader: 'gemini', cloud: true, value: 'annie' })],
        }),
        receipt_date: field('receipt_date', 'B'),
      }),
      row('r2', {
        receipt_date: field('receipt_date', 'D', { candidates: [candidate('18/06/2026')] }),
        commitment_number: field('commitment_number', 'C'),
      }),
    ],
  });
}

export interface FakeClient extends ReviewPageClient {
  getJob: Mock<ReviewPageClient['getJob']>;
  editField: Mock<ReviewPageClient['editField']>;
  undo: Mock<ReviewPageClient['undo']>;
  confirm: Mock<ReviewPageClient['confirm']>;
  fetchCrop: Mock<ReviewPageClient['fetchCrop']>;
}

/** A client over one in-memory job. Edits echo back the row the way the real API would. */
export function fakeClient(initial: JobView, overrides: Partial<ReviewPageClient> = {}): FakeClient {
  const base: FakeClient = {
    getJob: vi.fn<ReviewPageClient['getJob']>(() => Promise.resolve(structuredClone(initial))),
    editField: vi.fn<ReviewPageClient['editField']>((_jobId, rowId, key, req) => {
      const source = initial.rows.find((r) => r.row_id === rowId);
      if (!source) return Promise.reject(new Error('unknown row'));
      const current = source.fields[key];
      const value = req.pick_candidate_index != null
        ? (current?.candidates[req.pick_candidate_index]?.value ?? null)
        : (req.value ?? null);
      const next: CommitmentRow = {
        ...source,
        fields: { ...source.fields, [key]: { ...field(key, 'B'), ...current, value, display_value: value, level: 'B', reason: null } },
        status: 'edited',
      };
      return Promise.resolve(next);
    }),
    undo: vi.fn<ReviewPageClient['undo']>((_jobId, rowId) => {
      const source = initial.rows.find((r) => r.row_id === rowId);
      return source ? Promise.resolve(structuredClone(source)) : Promise.reject(new Error('unknown row'));
    }),
    confirm: vi.fn<ReviewPageClient['confirm']>(() => Promise.resolve({ ...initial, status: 'confirmed' as const })),
    fetchCrop: vi.fn<ReviewPageClient['fetchCrop']>(() => Promise.resolve(new Blob(['x'], { type: 'image/png' }))),
  };
  // An override replaces the spy; tests that override do not assert on that method.
  return { ...base, ...overrides } as FakeClient;
}

export function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrap = (node: React.ReactElement) => (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  );
  const view = render(wrap(ui));
  return { ...view, update: (node: React.ReactElement) => view.rerender(wrap(node)) };
}

export function renderReview(client: ReviewPageClient, clock?: () => Date) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/jobs/j1/review']}>
          <Routes>
            <Route path="/jobs/:jobId/review" element={<Review client={client} clock={clock} />} />
            <Route path="/jobs/:jobId/export" element={<p>export-page</p>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

export function cellOf(container: HTMLElement, rowId: string, key: FieldKey): HTMLElement {
  const element = Array.from(container.querySelectorAll<HTMLElement>('td[data-row-id]')).find(
    (td) => td.dataset.rowId === rowId && td.dataset.field === key,
  );
  if (!element) throw new Error(`no cell ${rowId}/${key}`);
  return element;
}
