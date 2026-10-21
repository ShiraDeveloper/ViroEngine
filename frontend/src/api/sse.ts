import type { SseEvent } from './types';

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
    } catch {
      if (!controller.signal.aborted) {
        on({ event: 'error', data: { code: 'stream_failed', page_id: null, recoverable: true } });
      }
    }
  })();

  return () => controller.abort();
}
