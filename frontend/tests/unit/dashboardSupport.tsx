import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';

import type { EvalMetricsResponse, EvalReport, FieldBreakdown } from '@/api/metrics';
import type { RunMetric } from '@/api/types';
import { Dashboard } from '@/pages/Dashboard';
import type { DashboardClient } from '@/pages/Dashboard';

export function breakdown(overrides: Partial<FieldBreakdown> = {}): FieldBreakdown {
  return {
    field: 'receipt_date',
    n: 40,
    accuracy: 0.9,
    flag_rate: 0.2,
    silent_error: 0.01,
    top_failure_reason: null,
    ...overrides,
  };
}

export function report(overrides: Partial<EvalReport> = {}): EvalReport {
  return {
    split: 'dev',
    date: '2026-10-26',
    local_only: true,
    ablation: null,
    pages: 33,
    fields: 231,
    accuracy: 0.93,
    printed_accuracy: 0.97,
    printed_n: 150,
    handwritten_accuracy: 0.81,
    handwritten_n: 81,
    silent_error: 0.008,
    flag_recall: 0.95,
    flag_precision: 0.6,
    flag_rate: 0.12,
    local_close_rate: 0.74,
    escalation_rate: 0.2,
    cloud_calls: 0,
    measured_cost_usd: 0,
    p50_latency_ms: 4200,
    p95_latency_ms: 9100,
    per_field: [breakdown()],
    ...overrides,
  };
}

export function runMetric(rate: number, at: string): RunMetric {
  return {
    at,
    fields_total: 100,
    fields_closed_local: Math.round(rate * 100),
    fields_escalated: 10,
    fields_flagged: 5,
    local_close_rate: rate,
    provider_calls: 0,
    engine_version: 'test',
  };
}

export function metricsClient(response: EvalMetricsResponse): DashboardClient {
  return { getEvalMetrics: vi.fn(() => Promise.resolve(response)) };
}

export function renderDashboard(client: DashboardClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Dashboard client={client} jobId="j1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
