import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { api } from '@/api/client';
import type { ApiClient } from '@/api/client';
import type { Box } from '@/api/types';

export type CropClient = Pick<ApiClient, 'fetchCrop'>;

export interface CropState {
  /** Object URL of the crop, or null while loading or when it is unavailable. */
  url: string | null;
  isPending: boolean;
  isError: boolean;
}

/**
 * Crop image for a box. Fetched as a Blob because an <img> cannot send X-API-Key. The object URL
 * is revoked when the crop changes or the component unmounts, so images do not outlive the view.
 */
export function useCrop(
  jobId: string,
  pageId: string | null,
  box: Box | null,
  pad?: number,
  client: CropClient = api,
): CropState {
  const enabled = pageId !== null && box !== null;
  const query = useQuery<Blob>({
    queryKey: ['crop', jobId, pageId, box?.join(',') ?? null, pad ?? null],
    queryFn: () =>
      pad === undefined
        ? client.fetchCrop(jobId, pageId ?? '', box ?? [0, 0, 1, 1])
        : client.fetchCrop(jobId, pageId ?? '', box ?? [0, 0, 1, 1], pad),
    enabled,
    retry: false,
    staleTime: Infinity,
  });

  const [url, setUrl] = useState<string | null>(null);
  const blob = query.data;
  useEffect(() => {
    if (!blob) {
      setUrl(null);
      return undefined;
    }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  return { url, isPending: enabled && query.isPending, isError: enabled && query.isError };
}
