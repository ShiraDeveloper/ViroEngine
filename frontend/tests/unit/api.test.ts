import { beforeEach, describe, expect, it } from 'vitest';

import { createHttpClient, selectClient } from '@/api/client';
import { mockClient, resetMock } from '@/api/mockClient';
import { parseSseChunk } from '@/api/sse';
import { formatDate, formatDuration, formatFileSize, minutesUntil } from '@/lib/format';

describe('client selection', () => {
  it('selects_mock_client_when_flag_is_1', () => {
    expect(selectClient({ VITE_USE_MOCK: '1' })).toBe(mockClient);
  });

  it('selects_http_client_when_flag_is_0', () => {
    expect(selectClient({ VITE_USE_MOCK: '0' })).not.toBe(mockClient);
  });

  it('builds_crop_and_export_urls_from_the_base_path', () => {
    const http = createHttpClient({ baseUrl: '/v1', apiKey: 'k' });
    expect(http.cropUrl('j1', 'p#1', [0.1, 0.2, 0.3, 0.4])).toBe(
      '/v1/jobs/j1/pages/p%231/crop?box=0.1,0.2,0.3,0.4&pad=0.02',
    );
    expect(http.exportUrl('j1', 'xlsx')).toBe('/v1/jobs/j1/export?format=xlsx');
  });

  it('sends_the_api_key_header_on_get_job', async () => {
    const seen: Headers[] = [];
    const fetchImpl: typeof fetch = (_input, init) => {
      seen.push(new Headers(init?.headers));
      return Promise.resolve(new Response('{}', { status: 200 }));
    };
    await createHttpClient({ baseUrl: '/v1', apiKey: 'demo-key', fetchImpl }).getJob('j1');
    expect(seen[0]?.get('X-API-Key')).toBe('demo-key');
  });
});

describe('mock client', () => {
  beforeEach(() => resetMock());

  it('edits_a_field_and_undo_restores_it', async () => {
    const before = await mockClient.getJob('demo');
    const row = before.rows[3];
    expect(row?.fields.commitment_number?.value).toBeNull();

    await mockClient.editField('demo', 'r-004', 'commitment_number', { value: '123456' });
    const edited = await mockClient.getJob('demo');
    expect(edited.rows[3]?.fields.commitment_number?.value).toBe('123456');

    await mockClient.undo('demo', 'r-004');
    const restored = await mockClient.getJob('demo');
    expect(restored.rows[3]?.fields.commitment_number?.value).toBeNull();
  });
});

describe('sse parser', () => {
  it('parses_complete_frames_and_keeps_the_tail', () => {
    const chunk =
      'event: counters\ndata: {"pages_done":1,"pages_total":2,"fields_closed_local":3,"crops_sent":0}\n\n' +
      'event: done\ndata: {"status"';
    const { events, rest } = parseSseChunk(chunk);
    expect(events).toHaveLength(1);
    expect(events[0]?.event).toBe('counters');
    expect(rest).toBe('event: done\ndata: {"status"');
  });

  it('drops_unknown_events_and_malformed_json', () => {
    const { events } = parseSseChunk('event: nope\ndata: {}\n\nevent: row\ndata: {oops\n\n');
    expect(events).toEqual([]);
  });
});

describe('format helpers', () => {
  it.each([
    ['2026-10-12', '12/10/2026'],
    ['not a date', 'not a date'],
  ])('formats_date_%s', (input, expected) => {
    expect(formatDate(input)).toBe(expected);
  });

  it('formats_duration_and_size', () => {
    expect(formatDuration(820)).toBe('820ms');
    expect(formatDuration(1400)).toBe('1.4s');
    expect(formatFileSize(3.2 * 1024 * 1024)).toBe('3.2MB');
  });

  it('never_reports_negative_minutes_until_expiry', () => {
    expect(minutesUntil('2026-10-19T09:00:00+03:00', new Date('2026-10-19T12:00:00+03:00'))).toBe(0);
    expect(minutesUntil('2026-10-19T09:30:00+03:00', new Date('2026-10-19T09:00:00+03:00'))).toBe(30);
  });
});
