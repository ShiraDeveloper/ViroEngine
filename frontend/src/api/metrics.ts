import type { FieldKey, RunMetric } from './types';

/** Mirrors `eval/run_eval.py::FieldBreakdown`. Counts and rates only, never a field value. */
export interface FieldBreakdown {
  field: FieldKey;
  n: number;
  accuracy: number | null;
  flag_rate: number | null;
  silent_error: number | null;
  /** A reason from the closed set in PLAN §5.2, or null when nothing failed. */
  top_failure_reason: string | null;
}

export type EvalSplit = 'dev' | 'heldout' | 'all';

/** Mirrors `eval/run_eval.py::EvalReport`. A rate is null when its denominator is zero. */
export interface EvalReport {
  split: EvalSplit;
  /** ISO date (YYYY-MM-DD) the run was made. */
  date: string;
  local_only: boolean;
  ablation: string | null;
  pages: number;
  fields: number;
  accuracy: number | null;
  printed_accuracy: number | null;
  printed_n: number;
  handwritten_accuracy: number | null;
  handwritten_n: number;
  silent_error: number | null;
  flag_recall: number | null;
  flag_precision: number | null;
  flag_rate: number | null;
  local_close_rate: number | null;
  escalation_rate: number | null;
  cloud_calls: number;
  measured_cost_usd: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  per_field: FieldBreakdown[];
}

/**
 * `GET /v1/metrics/eval`. `report` is the latest run. `ablation` (one report per configuration,
 * from `eval/results/ablation.json`) and `learning` (`run_metric` rows) are optional: until the
 * API serves them the dashboard shows dashes and an explanatory empty state, never made-up numbers.
 */
export type EvalMetricsResponse =
  | { available: false }
  | {
      available: true;
      report: EvalReport;
      ablation?: EvalReport[];
      learning?: RunMetric[];
    };
