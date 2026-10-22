import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import type { JobView } from '@/api/types';

export function jobQueryKey(jobId: string): readonly ['job', string] {
  return ['job', jobId];
}

/** Server state for one job. SSE events will update this cache entry directly (T22/T21). */
export function useJob(jobId: string, client: Pick<ApiClient, 'getJob'> = api) {
  return useQuery<JobView>({
    queryKey: jobQueryKey(jobId),
    queryFn: () => client.getJob(jobId),
  });
}
