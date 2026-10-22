import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import { subscribeResilient } from '@/api/sse';
import type { ResilientOptions } from '@/api/sse';
import type { JobView, SseEvent } from '@/api/types';
import { initialState, reduceEvent } from '@/lib/pipelineState';
import type { PipelineState } from '@/lib/pipelineState';
import { jobQueryKey } from '@/lib/useJob';

/** The slice of the client this hook needs; tests pass a plain fake. */
export type ProgressClient = Pick<ApiClient, 'getJob' | 'subscribe'>;

export function progressQueryKey(jobId: string): readonly ['job', string, 'progress'] {
  return ['job', jobId, 'progress'];
}

export interface JobProgress {
  isPending: boolean;
  isError: boolean;
  state: PipelineState | undefined;
  /** True once the stream was lost and progress arrives by polling. */
  degraded: boolean;
}

/** Keep the job snapshot in step with an event without refetching it. */
function patchJob(job: JobView, event: SseEvent): JobView {
  if (event.event === 'counters') return { ...job, pages_done: event.data.pages_done };
  if (event.event === 'done') return { ...job, status: event.data.status };
  return job;
}

/**
 * Live progress of one job. SSE events are folded into React Query cache entries directly
 * (progress state and job snapshot); the job is fetched once, never per event.
 */
export function useJobProgress(
  jobId: string,
  client: ProgressClient = api,
  options: ResilientOptions = {},
): JobProgress {
  const queryClient = useQueryClient();
  const [degraded, setDegraded] = useState(false);

  const jobQuery = useQuery<JobView>({
    queryKey: jobQueryKey(jobId),
    queryFn: () => client.getJob(jobId),
  });
  const job = jobQuery.data;

  const progressQuery = useQuery<PipelineState>({
    queryKey: progressQueryKey(jobId),
    queryFn: () => (job ? initialState(job) : Promise.reject(new Error('job not loaded'))),
    enabled: job !== undefined,
    staleTime: Infinity,
    retry: false,
  });
  const state = progressQuery.data;
  const alreadyDone = state?.doneStatus != null;
  const ready = state !== undefined;

  const { reconnectDelaysMs, pollIntervalMs } = options;
  useEffect(() => {
    if (!ready || alreadyDone) return undefined;
    const progressKey = progressQueryKey(jobId);
    const jobKey = jobQueryKey(jobId);
    return subscribeResilient(
      { open: (on) => client.subscribe(jobId, on), poll: () => client.getJob(jobId) },
      (event) => {
        queryClient.setQueryData<PipelineState>(progressKey, (prev) =>
          prev ? reduceEvent(prev, event) : prev,
        );
        queryClient.setQueryData<JobView>(jobKey, (prev) => (prev ? patchJob(prev, event) : prev));
      },
      { reconnectDelaysMs, pollIntervalMs, onDegraded: () => setDegraded(true) },
    );
  }, [ready, alreadyDone, jobId, client, queryClient, reconnectDelaysMs, pollIntervalMs]);

  return {
    isPending: jobQuery.isPending || (job !== undefined && state === undefined),
    isError: jobQuery.isError || progressQuery.isError,
    state,
    degraded,
  };
}
