import type { JobView, SseEvent } from '@/api/types';

/** The eleven pipeline stages S0..S10, in execution order. */
export const STAGES = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9', 'S10'] as const;
export type StageId = (typeof STAGES)[number];

/** Stages that send a crop to a cloud provider; every other stage resolves on the machine. */
export const CLOUD_STAGES: ReadonlySet<StageId> = new Set<StageId>(['S9']);

export type PageStatus = 'queued' | 'running' | 'done' | 'failed';

export interface PageProgress {
  pageId: string;
  label: string | null;
  /** Index into STAGES of the furthest stage reported, or -1 before the first event. */
  stageIndex: number;
  status: PageStatus;
}

export interface Counters {
  pagesDone: number;
  pagesTotal: number;
  fieldsClosedLocal: number;
  cropsSent: number;
  flagged: number;
}

export interface PipelineState {
  pages: PageProgress[];
  counters: Counters;
  /** Set by the `done` event; null while the job is running. */
  doneStatus: JobView['status'] | null;
}

export type PipState = 'done' | 'running' | 'pending' | 'failed';

/** State of one pip for a page. A stage is done once the page reached a later one. */
export function pipState(page: PageProgress, index: number): PipState {
  if (index < page.stageIndex) return 'done';
  if (index > page.stageIndex) return 'pending';
  if (page.status === 'failed') return 'failed';
  if (page.status === 'done') return 'done';
  return page.status === 'running' ? 'running' : 'pending';
}

function stageIndexOf(stage: string): number {
  return STAGES.indexOf(stage as StageId);
}

/** Seed the state from a job snapshot: known pages start queued, counters from the report. */
export function initialState(job: JobView): PipelineState {
  const pageIds = [...job.rows.flatMap((row) => row.page_ids), ...job.orphan_page_ids];
  const unique = [...new Set(pageIds)];
  const finished = job.status === 'needs_review' || job.status === 'confirmed';
  const report = job.report;
  return {
    pages: unique.map((pageId) => ({
      pageId,
      label: null,
      stageIndex: finished ? STAGES.length - 1 : -1,
      status: finished ? 'done' : 'queued',
    })),
    counters: {
      pagesDone: job.pages_done,
      pagesTotal: job.page_count,
      fieldsClosedLocal: report?.fields_closed_local ?? 0,
      cropsSent: (report?.tier1_calls ?? 0) + (report?.tier2_calls ?? 0),
      flagged: report?.fields_flagged ?? 0,
    },
    doneStatus: finished ? job.status : null,
  };
}

/** Pure reducer: one SSE event in, the next state out. Stage progress never moves backwards. */
export function reduceEvent(state: PipelineState, event: SseEvent): PipelineState {
  switch (event.event) {
    case 'page': {
      const { page_id: pageId, label, stage, status } = event.data;
      const index = stageIndexOf(stage);
      const existing = state.pages.find((page) => page.pageId === pageId);
      const base: PageProgress = existing ?? { pageId, label: null, stageIndex: -1, status: 'queued' };
      const next: PageProgress = {
        ...base,
        label: label ?? base.label,
        stageIndex: Math.max(base.stageIndex, index),
        status,
      };
      return {
        ...state,
        pages: existing
          ? state.pages.map((page) => (page.pageId === pageId ? next : page))
          : [...state.pages, next],
      };
    }
    case 'counters':
      return {
        ...state,
        counters: {
          ...state.counters,
          pagesDone: event.data.pages_done,
          pagesTotal: event.data.pages_total,
          fieldsClosedLocal: event.data.fields_closed_local,
          cropsSent: event.data.crops_sent,
        },
      };
    case 'done':
      return {
        ...state,
        counters: { ...state.counters, flagged: event.data.flagged },
        doneStatus: event.data.status,
      };
    case 'row':
    case 'error':
      return state;
  }
}
