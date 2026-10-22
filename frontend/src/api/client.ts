import { mockClient } from './mockClient';
import { openEventStream } from './sse';
import type {
  Box,
  CommitmentRow,
  FieldEditRequest,
  FieldKey,
  JobView,
  SseEvent,
  UploadResponse,
} from './types';

/** The one surface the UI talks to. The mock and the HTTP client implement it identically. */
export interface ApiClient {
  login(code: string): Promise<{ ok: boolean }>;
  uploadSingle(file: File, idempotencyKey: string): Promise<UploadResponse>;
  uploadBatch(files: File[], idempotencyKey: string): Promise<UploadResponse>;
  getJob(jobId: string): Promise<JobView>;
  /** Returns an unsubscribe function. */
  subscribe(jobId: string, on: (e: SseEvent) => void): () => void;
  editField(
    jobId: string,
    rowId: string,
    field: FieldKey,
    req: FieldEditRequest,
  ): Promise<CommitmentRow>;
  undo(jobId: string, rowId: string): Promise<CommitmentRow>;
  confirm(jobId: string): Promise<JobView>;
  cropUrl(jobId: string, pageId: string, box: Box, pad?: number): string;
  /** The crop image as a Blob. A browser <img> cannot send X-API-Key, so the Lens fetches here. */
  fetchCrop(jobId: string, pageId: string, box: Box, pad?: number): Promise<Blob>;
  exportUrl(jobId: string, fmt: 'xlsx' | 'csv' | 'json'): string;
}

/** A non-2xx API answer. `status` is the HTTP status; the body is never surfaced to the UI. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`API error ${status}`);
    this.name = 'ApiError';
    this.status = status;
  }
}

const ACCESS_CODE_KEY = 'safeline.accessCode';
const DEFAULT_CROP_PAD = 0.02;

export function getStoredAccessCode(): string | null {
  return sessionStorage.getItem(ACCESS_CODE_KEY);
}

export function clearStoredAccessCode(): void {
  sessionStorage.removeItem(ACCESS_CODE_KEY);
}

export interface HttpClientOptions {
  baseUrl: string;
  apiKey: string;
  fetchImpl?: typeof fetch;
}

/** Real client for the FastAPI backend (docs/PLAN.md §6 API table). */
export function createHttpClient(options: HttpClientOptions): ApiClient {
  const { baseUrl, apiKey } = options;
  const doFetch = options.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args));
  const keyHeader: Record<string, string> = { 'X-API-Key': apiKey };

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await doFetch(`${baseUrl}${path}`, {
      ...init,
      headers: { ...keyHeader, ...init.headers },
    });
    if (!response.ok) throw new ApiError(response.status);
    return (await response.json()) as T;
  }

  function cropPath(jobId: string, pageId: string, box: Box, pad: number): string {
    return (
      `/jobs/${encodeURIComponent(jobId)}/pages/${encodeURIComponent(pageId)}/crop` +
      `?box=${box.join(',')}&pad=${pad}`
    );
  }

  function uploadHeaders(idempotencyKey: string): Record<string, string> {
    return {
      'X-Access-Code': getStoredAccessCode() ?? '',
      'Idempotency-Key': idempotencyKey,
    };
  }

  return {
    // The API has no login route: the access code is checked on upload (401/403 there).
    // Here we only remember it for the session.
    login(code) {
      const trimmed = code.trim();
      if (trimmed === '') return Promise.resolve({ ok: false });
      sessionStorage.setItem(ACCESS_CODE_KEY, trimmed);
      return Promise.resolve({ ok: true });
    },
    uploadSingle(file, idempotencyKey) {
      const body = new FormData();
      body.append('file', file);
      return request<UploadResponse>('/documents', {
        method: 'POST',
        body,
        headers: uploadHeaders(idempotencyKey),
      });
    },
    uploadBatch(files, idempotencyKey) {
      const body = new FormData();
      files.forEach((file) => body.append('files', file));
      return request<UploadResponse>('/batches', {
        method: 'POST',
        body,
        headers: uploadHeaders(idempotencyKey),
      });
    },
    getJob: (jobId) => request<JobView>(`/jobs/${encodeURIComponent(jobId)}`),
    subscribe: (jobId, on) =>
      openEventStream(`${baseUrl}/jobs/${encodeURIComponent(jobId)}/events`, on, {
        headers: keyHeader,
      }),
    editField: (jobId, rowId, field, req) =>
      request<CommitmentRow>(
        `/jobs/${encodeURIComponent(jobId)}/rows/${encodeURIComponent(rowId)}/fields/${field}`,
        {
          method: 'PATCH',
          body: JSON.stringify(req),
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    undo: (jobId, rowId) =>
      request<CommitmentRow>(
        `/jobs/${encodeURIComponent(jobId)}/rows/${encodeURIComponent(rowId)}/undo`,
        { method: 'POST' },
      ),
    confirm: (jobId) =>
      request<JobView>(`/jobs/${encodeURIComponent(jobId)}/confirm`, { method: 'POST' }),
    cropUrl: (jobId, pageId, box, pad = DEFAULT_CROP_PAD) =>
      `${baseUrl}${cropPath(jobId, pageId, box, pad)}`,
    async fetchCrop(jobId, pageId, box, pad = DEFAULT_CROP_PAD) {
      const response = await doFetch(`${baseUrl}${cropPath(jobId, pageId, box, pad)}`, {
        headers: keyHeader,
      });
      if (!response.ok) throw new ApiError(response.status);
      return response.blob();
    },
    exportUrl: (jobId, fmt) => `${baseUrl}/jobs/${encodeURIComponent(jobId)}/export?format=${fmt}`,
  };
}

/** VITE_USE_MOCK=1 selects the fixture-backed client; anything else targets the real API. */
export function selectClient(env: {
  VITE_USE_MOCK?: string;
  VITE_API_BASE?: string;
  VITE_API_KEY?: string;
}): ApiClient {
  if (env.VITE_USE_MOCK === '1') return mockClient;
  return createHttpClient({ baseUrl: env.VITE_API_BASE ?? '/v1', apiKey: env.VITE_API_KEY ?? '' });
}

export const api: ApiClient = selectClient(import.meta.env);

export const IS_MOCK: boolean = import.meta.env.VITE_USE_MOCK === '1';
