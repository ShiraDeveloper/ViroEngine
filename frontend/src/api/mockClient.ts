import fixtureJson from '../../../fixtures/sample_job.json';
import reviewFixtureJson from '../../../fixtures/sample_job_review.json';

import type { ApiClient } from './client';
import type { CommitmentRow, FieldResult, JobView, SseEvent } from './types';

/** The frozen contract fixture (T2). Fictitious values only. */
const contractFixture = fixtureJson as unknown as JobView;
/** Same job with a third flagged field, for the timed three-field review test (T23 E2E). */
const reviewFixture = reviewFixtureJson as unknown as JobView;
const fixture = import.meta.env.VITE_MOCK_FIXTURE === 'review' ? reviewFixture : contractFixture;

const MOCK_PAGE_IDS = ['fx#1', 'fx#2', 'fx#3', 'fx#4', 'fx#5', 'fx#6', 'fx#7'];
const MOCK_EVENT_INTERVAL_MS = 400;

function clone<T>(value: T): T {
  return structuredClone(value);
}

/** Mutable working copy so edit / undo / confirm behave like the real API within a session. */
let job: JobView = clone(fixture);
const history = new Map<string, CommitmentRow[]>();

/** Restore the pristine fixture. Used by tests. */
export function resetMock(): void {
  job = clone(fixture);
  history.clear();
}

function replaceRow(next: CommitmentRow): void {
  job = { ...job, rows: job.rows.map((row) => (row.row_id === next.row_id ? next : row)) };
}

function findRow(rowId: string): CommitmentRow {
  const row = job.rows.find((candidate) => candidate.row_id === rowId);
  if (!row) throw new Error(`mock: unknown row ${rowId}`);
  return row;
}

export const mockClient: ApiClient = {
  login: (code) => Promise.resolve({ ok: code.trim() !== '' }),

  uploadSingle: () =>
    Promise.resolve({ job_id: fixture.job_id, status: 'queued', page_count: fixture.page_count }),

  uploadBatch: () =>
    Promise.resolve({ job_id: fixture.job_id, status: 'queued', page_count: fixture.page_count }),

  getJob: () => Promise.resolve(clone(job)),

  subscribe(_jobId, on) {
    const timers: ReturnType<typeof setTimeout>[] = [];
    const emit = (event: SseEvent, index: number): void => {
      timers.push(setTimeout(() => on(event), (index + 1) * MOCK_EVENT_INTERVAL_MS));
    };
    MOCK_PAGE_IDS.forEach((pageId, i) => {
      emit({ event: 'page', data: { page_id: pageId, label: null, stage: 'S10', status: 'done', pct: 1 } }, i);
      emit(
        {
          event: 'counters',
          data: { pages_done: i + 1, pages_total: MOCK_PAGE_IDS.length, fields_closed_local: (i + 1) * 5, crops_sent: i },
        },
        i,
      );
    });
    emit(
      { event: 'done', data: { status: 'needs_review', flagged: 2, duration_ms: 2800 } },
      MOCK_PAGE_IDS.length,
    );
    return () => timers.forEach(clearTimeout);
  },

  editField(_jobId, rowId, field, req) {
    const row = findRow(rowId);
    const current = row.fields[field];
    if (!current) return Promise.reject(new Error(`mock: row has no field ${field}`));
    history.set(rowId, [...(history.get(rowId) ?? []), clone(row)]);

    let value: string | null = req.value ?? null;
    if (req.pick_candidate_index != null) {
      value = current.candidates[req.pick_candidate_index]?.value ?? null;
    }
    // Mock only: a human decision closes the field at level B and clears the reason.
    const edited: FieldResult = {
      ...current,
      value,
      display_value: value,
      level: value === null ? 'D' : 'B',
      reason: value === null ? current.reason : null,
    };
    const next: CommitmentRow = {
      ...row,
      fields: { ...row.fields, [field]: edited },
      status: 'edited',
    };
    replaceRow(next);
    return Promise.resolve(clone(next));
  },

  undo(_jobId, rowId) {
    const stack = history.get(rowId) ?? [];
    const previous = stack.pop();
    if (!previous) return Promise.reject(new Error('mock: nothing to undo'));
    replaceRow(previous);
    return Promise.resolve(clone(previous));
  },

  confirm: () => {
    job = { ...job, status: 'confirmed' };
    return Promise.resolve(clone(job));
  },

  cropUrl: (jobId, pageId) => `mock://crop/${jobId}/${pageId}`,

  // A placeholder drawing, so the Lens has something to magnify without a backend.
  fetchCrop: () =>
    Promise.resolve(
      new Blob(
        [
          '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40">' +
            '<rect width="120" height="40" fill="lightgray"/><path d="M8 28h104" stroke="gray"/></svg>',
        ],
        { type: 'image/svg+xml' },
      ),
    ),

  exportUrl: (jobId, fmt) => `mock://export/${jobId}.${fmt}`,
  // Demo mode exports the fixture rows as JSON whatever the format: nothing leaves the browser.
  fetchExport: (jobId) =>
    Promise.resolve(
      new Blob([JSON.stringify({ job_id: jobId, rows: job.rows })], {
        type: 'application/json',
      }),
    ),

  // Demo mode has no evaluation run: it reports "no data" rather than invent numbers.
  getEvalMetrics: () => Promise.resolve({ available: false }),

  // The derived job is the same fixture: enough to exercise the screen without a backend.
  stress: () =>
    Promise.resolve({ job_id: `${fixture.job_id}-stress`, status: 'needs_review', page_count: fixture.page_count }),
};
