import type { JobView } from '@/api/types';
import { isFlagged } from '@/lib/level';

/** How the Stress Mode panel compares a job before and after degradation. Counts and a rate. */
export interface JobSummary {
  rows: number;
  flagged: number;
  /** Share of fields closed on the machine, or null when the job has no run report yet. */
  localCloseRate: number | null;
}

export function summarizeJob(job: JobView): JobSummary {
  const flagged = job.rows.reduce(
    (sum, row) => sum + Object.values(row.fields).filter((f) => isFlagged(f.level)).length,
    0,
  );
  return {
    rows: job.rows.length,
    flagged,
    localCloseRate: job.report ? job.report.local_close_rate : null,
  };
}

/** A derived job is ready for comparison once it is no longer queued or running. */
export function isSettled(job: JobView): boolean {
  return job.status !== 'queued' && job.status !== 'running';
}

/** Polling cadence and ceiling for the derived job (PLAN T25: under 30 s on a 5-page job). */
export const STRESS_POLL_MS = 1500;
export const STRESS_TIMEOUT_MS = 120_000;

/** Every degradation Stress Mode offers, in the order of PLAN §10.3. */
export const STRESS_OPS = ['nohl', 'rot', 'blur', 'jpeg', 'fax'] as const;
export type StressOp = (typeof STRESS_OPS)[number];
