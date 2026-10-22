import type { JobView, SseEvent } from './types';

/** Error codes the transport itself emits (page_id is always null). Not server-side errors. */
export const STREAM_FAILED = 'stream_failed';
export const STREAM_ENDED = 'stream_ended';

const EVENT_NAMES: ReadonlySet<string> = new Set(['page', 'counters', 'row', 'done', 'error']);

export interface SseParseResult {
  events: SseEvent[];
  /** Unconsumed tail: an incomplete frame to prepend to the next chunk. */
  rest: string;
}

/**
 * Parse text/event-stream frames (separated by a blank line) into typed events.
 * Unknown event names and frames whose data is not valid JSON are dropped, never thrown.
 */
export function parseSseChunk(buffer: string): SseParseResult {
  const normalised = buffer.replace(/\r\n/g, '\n');
  const frames = normalised.split('\n\n');
  const rest = frames.pop() ?? '';
  const events: SseEvent[] = [];
  for (const frame of frames) {
    let name = 'message';
    const dataLines: string[] = [];
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    if (!EVENT_NAMES.has(name) || dataLines.length === 0) continue;
    try {
      const data: unknown = JSON.parse(dataLines.join('\n'));
      events.push({ event: name, data } as SseEvent);
    } catch {
      // A malformed frame must not stop the stream.
    }
  }
  return { events, rest };
}

export interface OpenStreamOptions {
  headers?: Record<string, string>;
  fetchImpl?: typeof fetch;
}

/**
 * Open an SSE stream with fetch, because EventSource cannot send the X-API-Key header.
 * Returns an unsubscribe function. Network failures are reported as a recoverable `error` event.
 */
export function openEventStream(
  url: string,
  on: (event: SseEvent) => void,
  options: OpenStreamOptions = {},
): () => void {
  const controller = new AbortController();
  const doFetch = options.fetchImpl ?? fetch;

  void (async () => {
    try {
      const response = await doFetch(url, {
        headers: { Accept: 'text/event-stream', ...options.headers },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSseChunk(buffer);
        buffer = parsed.rest;
        parsed.events.forEach(on);
      }
      // A clean close without a `done` event is still a drop; the resilient layer reconnects.
      if (!controller.signal.aborted) {
        on({ event: 'error', data: { code: STREAM_ENDED, page_id: null, recoverable: true } });
      }
    } catch {
      if (!controller.signal.aborted) {
        on({ event: 'error', data: { code: STREAM_FAILED, page_id: null, recoverable: true } });
      }
    }
  })();

  return () => controller.abort();
}

/**
 * Reconnect schedule: 1s, 2s, 4s, 8s, 8s (doubling, capped at 8s), then polling every 3s.
 * Values are structural to the PLAN §T21 contract and are overridable for tests.
 */
export const DEFAULT_RECONNECT_DELAYS_MS: readonly number[] = [1000, 2000, 4000, 8000, 8000];
export const DEFAULT_POLL_INTERVAL_MS = 3000;

export interface ResilientOptions {
  reconnectDelaysMs?: readonly number[];
  pollIntervalMs?: number;
  /** Tells the UI the stream was lost and progress now arrives by polling. */
  onDegraded?: () => void;
}

export interface ResilientSource {
  /** Open one raw stream; returns its unsubscribe function. */
  open: (on: (event: SseEvent) => void) => () => void;
  /** Fetch the job snapshot used by the polling fallback. */
  poll: () => Promise<JobView>;
}

function isTransportError(event: SseEvent): boolean {
  return (
    event.event === 'error' &&
    event.data.page_id === null &&
    (event.data.code === STREAM_FAILED || event.data.code === STREAM_ENDED)
  );
}

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(['needs_review', 'confirmed', 'failed']);

/** Translate a polled snapshot into the same events the stream would have produced. */
export function jobToEvents(job: JobView): SseEvent[] {
  const flagged = job.rows.reduce(
    (sum, row) => sum + Object.values(row.fields).filter((field) => field?.level === 'D').length,
    0,
  );
  const report = job.report;
  const events: SseEvent[] = [
    {
      event: 'counters',
      data: {
        pages_done: job.pages_done,
        pages_total: job.page_count,
        fields_closed_local: report?.fields_closed_local ?? 0,
        crops_sent: (report?.tier1_calls ?? 0) + (report?.tier2_calls ?? 0),
      },
    },
  ];
  if (TERMINAL_STATUSES.has(job.status)) {
    events.push({
      event: 'done',
      data: { status: job.status, flagged, duration_ms: 0 },
    });
  }
  return events;
}

/**
 * Stream with reconnect, then poll. A dropped proxy degrades the UI to polling; it never freezes it.
 * Returns an unsubscribe function that stops the stream, the timers and the polling.
 */
export function subscribeResilient(
  source: ResilientSource,
  on: (event: SseEvent) => void,
  options: ResilientOptions = {},
): () => void {
  const delays = options.reconnectDelaysMs ?? DEFAULT_RECONNECT_DELAYS_MS;
  const pollMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  let stopped = false;
  let attempt = 0;
  let closeStream: (() => void) | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const stop = (): void => {
    stopped = true;
    if (timer !== null) clearTimeout(timer);
    closeStream?.();
    closeStream = null;
  };

  const startPolling = (): void => {
    options.onDegraded?.();
    const tick = (): void => {
      source
        .poll()
        .then((job) => {
          if (stopped) return;
          for (const event of jobToEvents(job)) {
            on(event);
            if (event.event === 'done') stop();
          }
        })
        .catch(() => {
          // A failed poll is retried on the next tick; the UI keeps its last state.
        })
        .finally(() => {
          if (!stopped) timer = setTimeout(tick, pollMs);
        });
    };
    tick();
  };

  const connect = (): void => {
    closeStream = source.open((event) => {
      if (stopped) return;
      if (isTransportError(event)) {
        closeStream?.();
        closeStream = null;
        const delay = delays[attempt];
        if (delay === undefined) {
          startPolling();
          return;
        }
        attempt += 1;
        timer = setTimeout(connect, delay);
        return;
      }
      attempt = 0;
      on(event);
      if (event.event === 'done') stop();
    });
  };

  connect();
  return stop;
}
